"""Boat dashboard backend — FastAPI app serving the web UI, REST control
endpoints, and a WebSocket telemetry stream.

One background task per rate builds the frames once and sends them to every
connected screen: engine and boat readings (with the alarms) five times a
second, so the gauges can move smoothly, and the full frame (GPS, nav, trip,
stereo, lights, track) once a second.

Run: uvicorn app.main:app --host 0.0.0.0 --port 8090
"""
import asyncio
import contextlib
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ais import SimulatedAIS
from .alarms import AlarmManager
from .boundaries import BoundaryManager
from .celestial import moon_phase, sun_times
from .chart_data import DETAIL_OFFSETS, ChartStore
from .gps import make_gps_source
from .lighting import COLOR_PRESETS, LightingController, PRESET_NAMES, Pca9685RgbDriver, WS281xDriver
from .media import make_media_source
from .n2k import make_n2k_node
from .nav import relative_bearing, waypoint_nav
from .nav_alarms import NavAlarmManager
from .quickdraw import QuickdrawRecorder
from .routes import RouteTracker
from .sensors import make_sensor_sources
from .state import Settings
from .switching import SwitchingPanel
from .telemute import AlarmMute
from .tracks import SavedTracks
from .trips import TripTracker
from .volume_boost import VolumeBoost
from .waypoints import SavedWaypoints

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"

settings = Settings()
gps_source = make_gps_source(settings)
n2k_node = make_n2k_node(settings)  # one NMEA 2000 node shared by the Fusion stereo and the fuel sensor
engine_info, boat_info, sensor_hub = make_sensor_sources(settings, DATA_DIR, n2k_node)
media_source = make_media_source(settings, n2k_node)
trip_tracker = TripTracker(DATA_DIR / "trips.json")
alarms = AlarmManager(DATA_DIR / "alarms.json")
volume_boost = VolumeBoost(DATA_DIR / "volume_boost.json", media_source, settings.redline_rpm)
telemute = AlarmMute(DATA_DIR / "telemute.json", media_source)
saved_tracks = SavedTracks(DATA_DIR / "tracks.json")
saved_waypoints = SavedWaypoints(DATA_DIR / "waypoints.json")
route_tracker = RouteTracker(DATA_DIR / "routes.json")
boundaries = BoundaryManager(DATA_DIR / "boundaries.json")
switching = SwitchingPanel(DATA_DIR / "switching.json")
quickdraw = QuickdrawRecorder(DATA_DIR / "quickdraw.json")
nav_alarms = NavAlarmManager(DATA_DIR / "nav_alarms.json")
ais = SimulatedAIS(36.306, -86.563)  # no real AIS receiver: a few other boats nearby, for demo purposes
chart_store = ChartStore(DATA_DIR / "charts")


def make_led_driver(settings):
    """Return (driver or None for the preview-only mock, pixel count)."""
    pixels = 1 if settings.led_driver == "pwm" else settings.led_pixel_count  # a PWM strip is one color at a time
    try:
        if settings.led_driver == "ws281x":
            return WS281xDriver(pixels, settings.led_gpio_pin), pixels
        if settings.led_driver == "pwm":
            channels = tuple(int(c) for c in settings.led_pwm_channels.split(","))
            return Pca9685RgbDriver(address=settings.pca9685_address, channels=channels, bus_number=settings.i2c_bus), pixels
    except Exception as exc:  # pragma: no cover - hardware-dependent
        print(f"[lighting] could not start the '{settings.led_driver}' LED driver ({exc}); the strip preview will work but no lights will")
    return None, pixels


led_driver, led_pixels = make_led_driver(settings)
lighting = LightingController(led_pixels, driver=led_driver)

FAST_PERIOD_S = 0.2   # engine + boat readings and alarms: 5 frames a second
FULL_PERIOD_S = 1.0   # everything else


@contextlib.asynccontextmanager
async def lifespan(_app):
    lighting.start()  # applies light changes at once and keeps the rainbow moving; see lighting.py
    _read_engine_and_boat()  # so the very first full frame already has engine and boat readings
    tasks = [asyncio.create_task(_fast_loop()), asyncio.create_task(_full_loop())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        lighting.stop()


app = FastAPI(title="Boat Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def revalidate_ui_files(request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/calibrate") or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

waypoint: Optional[dict] = None  # {"lat", "lon", "origin_lat", "origin_lon", "name"}
track: list[dict] = []  # recent breadcrumb trail for the chart
_clients: set[WebSocket] = set()
_recent_boundary_events: dict = {}  # boundary id -> (event dict, monotonic expiry): see _full_frame
BOUNDARY_EVENT_HOLD_S = 8.0


class WaypointIn(BaseModel):
    lat: float
    lon: float
    name: str = "WP"


class LightingIn(BaseModel):
    preset: Optional[str] = None
    r: Optional[int] = None
    g: Optional[int] = None
    b: Optional[int] = None
    brightness: Optional[float] = None
    on: Optional[bool] = None


class MediaIn(BaseModel):
    action: str
    value: Optional[int] = None
    zone: int = 1  # as numbered on the stereo: 1 = Zone 1


class AlarmIn(BaseModel):
    enabled: Optional[bool] = None
    level: Optional[float] = None


class AlarmSoundIn(BaseModel):
    on: bool


class VolumeBoostIn(BaseModel):
    enabled: Optional[bool] = None
    boost_pct: Optional[float] = None
    smoothing_pct: Optional[float] = None


class SaveTrackIn(BaseModel):
    name: Optional[str] = None


class RenameTrackIn(BaseModel):
    name: str


class TelemuteIn(BaseModel):
    enabled: bool


class CalibrationIn(BaseModel):
    action: str
    value: Optional[float] = None
    device: Optional[str] = None


class CreateWaypointIn(BaseModel):
    lat: float
    lon: float
    name: Optional[str] = None


class UpdateWaypointIn(BaseModel):
    name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class RoutePointIn(BaseModel):
    lat: float
    lon: float
    name: str = "WPT"


class CreateRouteIn(BaseModel):
    points: list[RoutePointIn]
    name: Optional[str] = None


class RenameRouteIn(BaseModel):
    name: str


class CreateBoundaryIn(BaseModel):
    lat: float
    lon: float
    radius_ft: float
    name: Optional[str] = None
    alarm_on: str = "exit"


class UpdateBoundaryIn(BaseModel):
    name: Optional[str] = None
    radius_ft: Optional[float] = None
    alarm_on: Optional[str] = None
    enabled: Optional[bool] = None


class SwitchIn(BaseModel):
    on: bool


class NavAlarmSettingsIn(BaseModel):
    arrival_enabled: Optional[bool] = None
    arrival_radius_nm: Optional[float] = None
    off_course_enabled: Optional[bool] = None
    off_course_xte_nm: Optional[float] = None
    anchor_radius_ft: Optional[float] = None
    gps_accuracy_enabled: Optional[bool] = None
    gps_accuracy_hdop_max: Optional[float] = None


class QuickdrawIn(BaseModel):
    enabled: bool


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/calibrate")
def calibrate_page():
    return FileResponse(str(STATIC_DIR / "calibrate.html"))


@app.get("/api/chart/areas")
def chart_areas():
    """Which chart areas are on disk, so the frontend can pick one without being told."""
    root = DATA_DIR / "charts"
    areas = []
    if root.exists():
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            manifest = chart_store.manifest(d.name)
            if manifest:
                files, total_bytes = chart_store.stats(d.name)
                areas.append({"name": d.name, "bbox": manifest.get("bbox"),
                              "layers": len(manifest.get("layers", {})),
                              "fetched_at": manifest.get("fetched_at"), "bytes": total_bytes})
    return {"areas": areas}


@app.get("/api/chart/{area}/{detail}")
def chart_bundle(area: str, detail: str):
    """Every chart layer for one area at one detail level, in a single response.

    Bundled rather than a request per layer because this is served off local disk -- one ~1 MB
    response beats 24 round trips, and the whole point is that it works with no internet."""
    if detail not in DETAIL_OFFSETS or "/" in area or ".." in area:
        return Response(status_code=404)
    manifest = chart_store.manifest(area)
    if not manifest:
        return Response(status_code=404)
    layers = {}
    for name, entry in manifest.get("layers", {}).items():
        raw = chart_store.read_layer(area, detail, name)
        if raw:
            layers[name] = {"kind": entry["kind"], "geojson": json.loads(raw)}
    return {"area": area, "detail": detail, "bbox": manifest.get("bbox"), "layers": layers}


@app.get("/api/sensors")
def sensors_status():
    if sensor_hub:
        return sensor_hub.status()
    # No real sensor hub: nothing to calibrate, but the calibration page can still show what the
    # simulator is currently making up, read from the same cache the telemetry loop already fills
    # (not re-ticked here, which would perturb the same random walk the dashboard's gauges show).
    engine, boat = _latest["engine"], _latest["boat"]
    return {
        "real": False,
        "sim": {
            "rpm": engine.get("rpm"),
            "oil_pressure_psi": engine.get("oil_pressure_psi"),
            "trim_pct": engine.get("trim_pct"),
            "coolant_f": engine.get("coolant_f"),
            "fuel_pct": engine.get("fuel_pct"),
            "fuel_gph": engine.get("fuel_gph"),
            "battery_voltage": boat.get("battery_voltage"),
            "depth_ft": boat.get("depth_ft"),
            "water_temp_f": boat.get("water_temp_f"),
        },
    }


@app.post("/api/calibration")
def calibrate(cmd: CalibrationIn):
    if not sensor_hub:
        return {"ok": False, "error": "sensors are simulated (set BOAT_SENSORS=n2k or real)"}
    try:
        message = sensor_hub.capture(cmd.action, cmd.value, cmd.device)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": message, "status": sensor_hub.status()}


@app.get("/api/health")
def health():
    media = media_source.state()
    return {
        "ok": True,
        "time": time.time(),
        "gps_source": type(gps_source).__name__,
        "sensors": settings.sensors if sensor_hub else "simulated",  # n2k | real | simulated
        "engine_heard": sensor_hub.engine_heard() if sensor_hub else None,  # is the NMEA 2000 engine converter talking?
        "stereo": "simulated" if media["simulated"] else "connected" if media["connected"] else "not found",
        "can_channel": settings.can_channel or None,
        "led_driver": settings.led_driver,
    }


@app.post("/api/waypoint")
def set_waypoint(wp: WaypointIn):
    """The single "Go To" target: a straight line to one point, right now. Setting one stops any
    active route (Route To) -- the two are mutually exclusive, like on a real chartplotter."""
    global waypoint
    route_tracker.stop()
    origin = None
    if track:
        origin = track[-1]
    waypoint = {
        "lat": wp.lat,
        "lon": wp.lon,
        "name": wp.name,
        "origin_lat": origin["lat"] if origin else wp.lat,
        "origin_lon": origin["lon"] if origin else wp.lon,
    }
    return {"ok": True, "waypoint": waypoint}


@app.delete("/api/waypoint")
def clear_waypoint():
    global waypoint
    waypoint = None
    return {"ok": True}


# ---------------- Saved Waypoints: a named list of marked positions ----------------
@app.post("/api/waypoints")
def create_waypoint(wp: CreateWaypointIn):
    saved = saved_waypoints.create(wp.lat, wp.lon, wp.name)
    return {"ok": True, "waypoint": asdict(saved)}


@app.get("/api/waypoints")
def list_waypoints():
    return {"waypoints": saved_waypoints.list()}


@app.patch("/api/waypoints/{wp_id}")
def update_waypoint(wp_id: str, cmd: UpdateWaypointIn):
    try:
        updated = saved_waypoints.update(wp_id, cmd.name, cmd.lat, cmd.lon)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if updated is None:
        return {"ok": False, "error": "no waypoint with that id"}
    return {"ok": True, "waypoint": asdict(updated)}


@app.delete("/api/waypoints/{wp_id}")
def delete_waypoint(wp_id: str):
    return {"ok": saved_waypoints.delete(wp_id)}


@app.delete("/api/waypoints")
def delete_all_waypoints():
    return {"ok": True, "deleted": saved_waypoints.delete_all()}


@app.post("/api/waypoints/{wp_id}/goto")
def goto_waypoint(wp_id: str):
    """Copies a saved waypoint into the single active "Go To" target."""
    wp = saved_waypoints.get(wp_id)
    if wp is None:
        return {"ok": False, "error": "no waypoint with that id"}
    return set_waypoint(WaypointIn(lat=wp.lat, lon=wp.lon, name=wp.name))


# ---------------- Routes: multi-leg navigation ("Route To") ----------------
@app.post("/api/routes")
def create_route(cmd: CreateRouteIn):
    try:
        route = route_tracker.create([p.model_dump() for p in cmd.points], cmd.name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "route": {"id": route.id, "name": route.name}}


@app.get("/api/routes")
def list_routes():
    return {"routes": route_tracker.list()}


@app.get("/api/routes/{route_id}")
def get_route(route_id: str):
    route = route_tracker.get(route_id)
    if route is None:
        return {"ok": False, "error": "no route with that id"}
    return {"ok": True, "id": route.id, "name": route.name, "points": route.points}


@app.patch("/api/routes/{route_id}")
def rename_route(route_id: str, cmd: RenameRouteIn):
    try:
        renamed = route_tracker.rename(route_id, cmd.name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if renamed is None:
        return {"ok": False, "error": "no route with that id"}
    return {"ok": True, "name": renamed.name}


@app.delete("/api/routes/{route_id}")
def delete_route(route_id: str):
    return {"ok": route_tracker.delete(route_id)}


@app.post("/api/routes/{route_id}/start")
def start_route(route_id: str):
    """Following a route stops any single "Go To" -- the two are mutually exclusive."""
    global waypoint
    try:
        route = route_tracker.start(route_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    waypoint = None
    return {"ok": True, "route": route.id}


@app.post("/api/routes/stop")
def stop_route():
    return {"ok": True, "stopped": route_tracker.stop()}


# ---------------- Boundaries: circular geofence alarms ----------------
@app.post("/api/boundaries")
def create_boundary(cmd: CreateBoundaryIn):
    try:
        b = boundaries.create(cmd.lat, cmd.lon, cmd.radius_ft, cmd.name, cmd.alarm_on)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "boundary": asdict(b)}


@app.get("/api/boundaries")
def list_boundaries():
    return {"boundaries": boundaries.list()}


@app.patch("/api/boundaries/{boundary_id}")
def update_boundary(boundary_id: str, cmd: UpdateBoundaryIn):
    try:
        updated = boundaries.update(boundary_id, cmd.enabled, cmd.alarm_on, cmd.radius_ft, cmd.name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if updated is None:
        return {"ok": False, "error": "no boundary with that id"}
    return {"ok": True, "boundary": asdict(updated)}


@app.delete("/api/boundaries/{boundary_id}")
def delete_boundary(boundary_id: str):
    return {"ok": boundaries.delete(boundary_id)}

# ---------------- Navigation alarms: Arrival, Off Course, Anchor Drag, GPS Accuracy ----------------
@app.get("/api/nav-alarms")
def get_nav_alarms():
    return {"settings": asdict(nav_alarms.settings), "anchor_dropped": nav_alarms.anchor_dropped}


@app.post("/api/nav-alarms")
def set_nav_alarms(cmd: NavAlarmSettingsIn):
    try:
        return {"ok": True, "settings": nav_alarms.update_settings(**cmd.model_dump())}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/anchor/drop")
def drop_anchor():
    fix = _latest["fix"]
    if fix is None or not fix.has_fix:
        return {"ok": False, "error": "no GPS fix yet"}
    return {"ok": True, "anchor": nav_alarms.drop_anchor(fix.lat, fix.lon)}


@app.post("/api/anchor/raise")
def raise_anchor():
    return {"ok": True, "was_dropped": nav_alarms.raise_anchor()}


# ---------------- Digital Switching: simulated relay circuits ----------------
@app.get("/api/switching")
def get_switching():
    return {"circuits": switching.list()}


@app.post("/api/switching/{circuit_id}")
def set_switching(circuit_id: str, cmd: SwitchIn):
    try:
        circuit = switching.set(circuit_id, cmd.on)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "circuit": asdict(circuit)}


# ---------------- AIS: simulated nearby vessels ----------------
@app.get("/api/ais")
def get_ais():
    fix = _latest["fix"]
    if fix and fix.has_fix:
        return {"targets": ais.targets(fix.lat, fix.lon, fix.sog_kn)}
    return {"targets": ais.targets()}


# ---------------- Quickdraw-style depth recording ----------------
@app.get("/api/quickdraw")
def get_quickdraw():
    return {"enabled": quickdraw.enabled, "points": quickdraw.points()}


@app.post("/api/quickdraw")
def set_quickdraw(cmd: QuickdrawIn):
    quickdraw.set_enabled(cmd.enabled)
    return {"ok": True, "enabled": quickdraw.enabled}


@app.delete("/api/quickdraw")
def clear_quickdraw():
    return {"ok": True, "deleted": quickdraw.clear()}


@app.delete("/api/track")
def clear_track():
    """The breadcrumb trail drawn on the chart (not a saved trip): Options > Map layers & colors > User Data > Clear Track."""
    track.clear()
    return {"ok": True}


@app.post("/api/tracks")
def save_track(cmd: SaveTrackIn):
    """Saves whatever is currently in the breadcrumb trail (up to the last 600 points/~10 minutes
    at the 1 Hz it's recorded) as a permanent, named record — the same limit a real chartplotter's
    active-track memory has. Doesn't clear the live trail; Clear Track (above) is a separate action."""
    try:
        saved = saved_tracks.save(track, cmd.name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "track": {"id": saved.id, "name": saved.name}}


@app.get("/api/tracks")
def list_saved_tracks():
    return {"tracks": saved_tracks.list()}


@app.get("/api/tracks/{track_id}")
def get_saved_track(track_id: str):
    found = saved_tracks.get(track_id)
    if found is None:
        return {"ok": False, "error": "no saved track with that id"}
    return {"ok": True, "id": found.id, "name": found.name, "points": found.points}


@app.patch("/api/tracks/{track_id}")
def rename_saved_track(track_id: str, cmd: RenameTrackIn):
    try:
        renamed = saved_tracks.rename(track_id, cmd.name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if renamed is None:
        return {"ok": False, "error": "no saved track with that id"}
    return {"ok": True, "name": renamed.name}


@app.delete("/api/tracks/{track_id}")
def delete_saved_track(track_id: str):
    return {"ok": saved_tracks.delete(track_id)}


@app.delete("/api/tracks")
def delete_all_saved_tracks():
    return {"ok": True, "deleted": saved_tracks.delete_all()}


@app.get("/api/lighting/presets")
def list_presets():
    return {"presets": PRESET_NAMES, "colors": COLOR_PRESETS}


@app.post("/api/lighting")
def update_lighting(cmd: LightingIn):
    if cmd.on is not None:
        lighting.set_power(cmd.on)
    if cmd.brightness is not None:
        lighting.set_brightness(cmd.brightness)
    if cmd.r is not None and cmd.g is not None and cmd.b is not None:
        lighting.set_solid_color(cmd.r, cmd.g, cmd.b)
    if cmd.preset is not None:
        lighting.set_preset(cmd.preset)
    lighting.tick()  # apply now rather than waiting for the loop, so the answer already shows the new lights
    return {"ok": True, "state": lighting.state()}


@app.get("/api/alarms")
def get_alarms():
    return alarms.snapshot()


@app.post("/api/alarms/ack")
def acknowledge_alarms():
    return {"ok": True, "acknowledged": alarms.acknowledge(), **alarms.snapshot()}


@app.post("/api/alarms/sound")
def set_alarm_sound(cmd: AlarmSoundIn):
    alarms.set_sound(cmd.on)
    return {"ok": True, **alarms.snapshot()}


@app.post("/api/alarms/{alarm_id}")
def update_alarm(alarm_id: str, cmd: AlarmIn):
    try:
        alarms.update(alarm_id, cmd.enabled, cmd.level)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), **alarms.snapshot()}
    return {"ok": True, **alarms.snapshot()}


@app.get("/api/media")
def get_media():
    return {"media": media_source.state()}


@app.post("/api/media")
def media_command(cmd: MediaIn):
    try:
        media_source.handle(cmd.action, cmd.value, cmd.zone)
    except (ValueError, RuntimeError, OSError) as exc:
        return {"ok": False, "error": str(exc), "media": media_source.state()}
    media = media_source.state()
    if cmd.action == "volume" and cmd.value is not None:
        volume_boost.note_manual_volume(cmd.zone, int(cmd.value))
    elif cmd.action == "master_volume" and cmd.value is not None:
        # every zone just got a new baseline, not only the one the command named
        for z in media["zones"]:
            volume_boost.note_manual_volume(z["id"], z["volume"])
    elif cmd.action == "mute":
        telemute.note_external_mute(bool(cmd.value))
    return {"ok": True, "media": media}


@app.get("/api/volume-boost")
def get_volume_boost():
    return volume_boost.config()


@app.post("/api/volume-boost")
def set_volume_boost(cmd: VolumeBoostIn):
    try:
        return {"ok": True, **volume_boost.set_config(cmd.enabled, cmd.boost_pct, cmd.smoothing_pct)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/telemute")
def get_telemute():
    return {"enabled": telemute.enabled}


@app.post("/api/telemute")
def set_telemute(cmd: TelemuteIn):
    telemute.set_enabled(cmd.enabled)
    return {"ok": True, "enabled": telemute.enabled}


@app.post("/api/trips/start")
def start_trip():
    if trip_tracker.is_active:
        return {"ok": False, "error": "a trip is already active"}
    if not track:
        return {"ok": False, "error": "no GPS fix yet"}
    trip = trip_tracker.start(track[-1]["lat"], track[-1]["lon"])
    return {"ok": True, "trip": trip.id}


@app.post("/api/trips/stop")
def stop_trip():
    finished = trip_tracker.stop()
    if finished is None:
        return {"ok": False, "error": "no active trip"}
    return {"ok": True, "trip": asdict(finished)}


@app.get("/api/trips")
def list_trips():
    return {"trips": trip_tracker.history()}


@app.delete("/api/trips/{trip_id}")
def delete_trip(trip_id: str):
    return {"ok": trip_tracker.delete(trip_id)}


_latest = {"fix": None, "engine": {}, "boat": {}, "full": None}


def _read_engine_and_boat():
    """The fast readings. The simulated engine follows boat speed, so it gets the latest GPS speed."""
    fix = _latest["fix"]
    engine = engine_info.read(fix.sog_kn if fix else 0.0)
    boat = boat_info.read((engine["rpm"] or 0) > 500)
    alarms.evaluate(engine, boat)
    telemute.tick(alarms.active())
    _latest["engine"], _latest["boat"] = engine, boat
    return engine, boat


def _fast_frame():
    engine, boat = _read_engine_and_boat()
    return {"type": "fast", "engine": engine, "boat_info": boat, "alarms": alarms.active()}


def _full_frame():
    """Everything else: GPS, nav, trip, stereo, lights and the chart track. May block on the GPS serial port, so it runs in a thread."""
    fix = gps_source.read()
    _latest["fix"] = fix
    engine, boat = _latest["engine"], _latest["boat"]
    media_source.tick()
    volume_boost.tick(engine.get("rpm"), media_source.state()["zones"])

    if fix.has_fix and trip_tracker.is_active:
        trip_tracker.tick(fix.lat, fix.lon, fix.sog_kn, engine.get("fuel_gph"))

    nav = None
    if waypoint is not None and fix.has_fix:
        nav = waypoint_nav(
            fix.lat, fix.lon, fix.sog_kn,
            waypoint["lat"], waypoint["lon"],
            waypoint["origin_lat"], waypoint["origin_lon"],
        )
        nav["waypoint"] = {"lat": waypoint["lat"], "lon": waypoint["lon"], "name": waypoint["name"]}
        nav["relative_bearing_deg"] = relative_bearing(fix.heading_deg, nav["bearing_deg"])

    route_nav = route_tracker.tick(fix.lat, fix.lon, fix.sog_kn) if fix.has_fix else None
    if route_nav and route_nav["finished"]:
        route_tracker.stop()

    if fix.has_fix:
        track.append({"lat": fix.lat, "lon": fix.lon, "t": fix.timestamp})
        if len(track) > 600:
            del track[: len(track) - 600]
        quickdraw.record(fix.lat, fix.lon, boat.get("depth_ft"))

    # A boundary crossing is a one-tick event (unlike the anchor/off-course alarms above, which
    # stay alarming for as long as the condition holds), so on its own it would only show up in
    # the alert banner for about a second -- too brief to notice. Keep it around for a few
    # seconds after the fact instead, the same idea as telemute.py's mute window.
    now = time.monotonic()
    for e in (boundaries.evaluate(fix.lat, fix.lon) if fix.has_fix else []):
        _recent_boundary_events[e["id"]] = (e, now + BOUNDARY_EVENT_HOLD_S)
    for stale_id in [bid for bid, (_, expires) in _recent_boundary_events.items() if expires <= now]:
        del _recent_boundary_events[stale_id]
    nav_alerts = nav_alarms.evaluate(fix, nav) + [
        {"id": "boundary_%s" % e["id"], "severity": "warning", "message": "%s: %s the boundary" % (e["name"], e["event"])}
        for e, _ in _recent_boundary_events.values()
    ]

    ais.tick()
    ais_targets = ais.targets(fix.lat, fix.lon, fix.sog_kn) if fix.has_fix else ais.targets()

    sunrise, sunset = sun_times(fix.lat, fix.lon, datetime.now().date()) if fix.has_fix else (None, None)
    moon_phase_name, moon_illum = moon_phase(datetime.now().date())

    return {
        "type": "frame",
        "gps": {
            "lat": fix.lat,
            "lon": fix.lon,
            "sog_kn": fix.sog_kn,
            "cog_deg": fix.cog_deg,
            "heading_deg": fix.heading_deg,
            "satellites": fix.satellites,
            "hdop": fix.hdop,
            "fix_quality": fix.fix_quality,
            "has_fix": fix.has_fix,
            "timestamp": fix.timestamp,
        },
        "nav": nav,
        "route_nav": route_nav,
        "nav_alerts": nav_alerts,
        "anchor": {"dropped": nav_alarms.anchor_dropped},
        "ais": ais_targets,
        "switching": switching.list(),
        "quickdraw_enabled": quickdraw.enabled,
        "sun": {
            "sunrise": sunrise.isoformat() if sunrise else None,
            "sunset": sunset.isoformat() if sunset else None,
            "moon_phase": moon_phase_name,
            "moon_illumination": round(moon_illum, 3),
        },
        "boat_info": boat,
        "engine": engine,
        "trip": trip_tracker.current(),
        "media": media_source.state(),
        "lighting": lighting.state(),
        "alarms": alarms.active(),
        "alarm_cfg": alarms.frame_config(),
        "alarm_sound": alarms.sound,
        "alarm_rev": alarms.revision,
        "track": track[-200:],
    }


async def _broadcast(message):
    if not _clients:
        return
    text = json.dumps(message)
    for ws in list(_clients):
        try:
            await asyncio.wait_for(ws.send_text(text), timeout=2.0)
        except Exception:  # a screen that went away (or stalled) is dropped; it reconnects on its own
            _clients.discard(ws)


async def _paced(period, work):
    """Run work() every `period` seconds without drifting, and keep going if one pass fails."""
    loop = asyncio.get_running_loop()
    next_at = loop.time()
    while True:
        try:
            await work()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[telemetry] {exc!r}")
        next_at += period
        await asyncio.sleep(max(0.0, next_at - loop.time()))
        if loop.time() - next_at > 5 * period:  # fell far behind (a stall): don't fire a burst to catch up
            next_at = loop.time()


async def _fast_loop():
    async def work():
        await _broadcast(_fast_frame())
    await _paced(FAST_PERIOD_S, work)


async def _full_loop():
    async def work():
        frame = await asyncio.to_thread(_full_frame)
        _latest["full"] = frame
        await _broadcast(frame)
    await _paced(FULL_PERIOD_S, work)


@app.websocket("/ws/telemetry")
async def telemetry_ws(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    try:
        if _latest["full"] is not None:
            await ws.send_text(json.dumps(_latest["full"]))  # a screen that just opened doesn't wait for the next second
        while True:
            await ws.receive_text()  # screens never send anything; this is just how a disconnect is noticed
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)
