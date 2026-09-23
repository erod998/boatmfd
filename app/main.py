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
import gzip
import json
import math
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .ais import SimulatedAIS
from .alarms import AlarmManager
from .boundaries import BoundaryManager
from .celestial import moon_phase, sun_times
from .chart_data import DETAIL_OFFSETS, ChartStore
from .gps import make_gps_source
from .guidance import GuidedPath
from .lighting import COLOR_PRESETS, LightingController, PRESET_NAMES, Pca9685RgbDriver, WS281xDriver
from .media import make_media_source
from .n2k import make_n2k_node
from .nav import relative_bearing
from .nav_alarms import NavAlarmManager
from .offline_check import report as report_offline_assets
from .quickdraw import QuickdrawRecorder
from .routes import RouteTracker
from .sensors import make_sensor_sources
from .state import Settings
from .storage import read_dict, write_json
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
            return Pca9685RgbDriver(address=settings.pca9685_address, channels=channels, bus_number=settings.led_i2c_bus), pixels
    except Exception as exc:  # pragma: no cover - hardware-dependent
        print(f"[lighting] could not start the '{settings.led_driver}' LED driver ({exc}); the strip preview will work but no lights will")
    return None, pixels


led_driver, led_pixels = make_led_driver(settings)
lighting = LightingController(led_pixels, driver=led_driver)

FAST_PERIOD_S = 0.2   # engine + boat readings and alarms: 5 frames a second
FULL_PERIOD_S = 1.0   # everything else


@contextlib.asynccontextmanager
async def lifespan(_app):
    # Names anything in static/ that needs the internet, which the boat does not have. Prints
    # and carries on: the helm display refusing to boot over a lint finding would be worse than
    # whatever it found. tests/test_offline_check.py is the gate that actually stops these.
    report_offline_assets(STATIC_DIR)
    lighting.start()  # applies light changes at once and keeps the rainbow moving; see lighting.py
    _read_engine_and_boat()  # so the very first full frame already has engine and boat readings
    tasks = [asyncio.create_task(_fast_loop()), asyncio.create_task(_full_loop())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        lighting.stop()
        # A clean stop (systemctl restart, an update) writes out what is otherwise only saved on a
        # timer. The battery switch gives no such chance, which is what those timers are for.
        trip_tracker.flush()
        quickdraw.flush()
        if sensor_hub:
            sensor_hub.stop()


app = FastAPI(title="Boat Dashboard", lifespan=lifespan)
# The chart is several megabytes of GeoJSON, which compresses about eight to one: over the boat's
# WiFi to a tablet that is the difference between a pause and none. Small responses (telemetry,
# settings) are left alone -- compressing them costs more than it saves.
app.add_middleware(GZipMiddleware, minimum_size=50_000)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def revalidate_ui_files(request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/calibrate") or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

waypoint: Optional[dict] = None  # {"lat", "lon", "name", "guide": GuidedPath or None until the first fix}
track: list[dict] = []  # recent breadcrumb trail for the chart
_clients: set[WebSocket] = set()
_recent_boundary_events: dict = {}  # boundary id -> (event dict, monotonic expiry): see _full_frame
BOUNDARY_EVENT_HOLD_S = 8.0


# Bounds on everything that arrives from a screen. These are not paranoia about the dashboard's
# own UI -- they are what stops one malformed request from poisoning the track, the nav solution
# and the alarm state for the rest of the trip. A NaN latitude is the worst case: it compares
# False against everything, so it silently survives range checks written by hand and then makes
# every distance it touches NaN too. A Field constraint rejects it at the door.
Latitude = Field(ge=-90.0, le=90.0)
Longitude = Field(ge=-180.0, le=180.0)
Name = Field(default=None, max_length=60)


PathPoint = Annotated[list[float], Field(min_length=2, max_length=2)]


class WaypointIn(BaseModel):
    lat: float = Latitude
    lon: float = Longitude
    name: str = Field(default="WP", max_length=60)
    # The route there, as planned by the browser along the channel (static/js/guidance.js):
    # [[lat, lon], ...] from the boat to this waypoint. None for a straight line.
    path: Optional[list[PathPoint]] = Field(default=None, max_length=5000)

    @field_validator("path")
    @classmethod
    def _real_positions(cls, path):
        if path is None:
            return path
        if len(path) < 2:
            raise ValueError("a path needs a start and a destination")
        for lat, lon in path:
            # isfinite first: NaN compares False against every bound and would slip through them.
            if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("every point on the path must be a real position")
        return path


class LightingIn(BaseModel):
    preset: Optional[str] = Field(default=None, max_length=40)
    r: Optional[int] = Field(default=None, ge=0, le=255)
    g: Optional[int] = Field(default=None, ge=0, le=255)
    b: Optional[int] = Field(default=None, ge=0, le=255)
    brightness: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    on: Optional[bool] = None


class MediaIn(BaseModel):
    action: str = Field(max_length=40)
    # Shared by every action, so bounded to the widest real domain -- a byte, since the head unit
    # reports its own source ids -- and each action checks its own range (volume 0..max) after.
    value: Optional[int] = Field(default=None, ge=0, le=255)
    # As numbered on the stereo: 1 = Zone 1. Settings caps a Fusion head unit at four zones.
    zone: int = Field(default=1, ge=1, le=4)


class AlarmIn(BaseModel):
    enabled: Optional[bool] = None
    # Wide, because each alarm's level is in its own units (volts, psi, degrees, feet); the
    # point of the bound is to exclude NaN and absurdities, not to second-guess the scale.
    level: Optional[float] = Field(default=None, ge=-10_000.0, le=10_000.0)


class AlarmSoundIn(BaseModel):
    on: bool


class ChartSettingsIn(BaseModel):
    # The lake's surface elevation, in feet: the surveyed depth shown anywhere is this minus the
    # charted bottom. None goes back to the area's normal pool. Bounded to rule out absurdities;
    # every lake this could chart sits somewhere in between.
    lake_level_ft: Optional[float] = Field(default=None, ge=-300.0, le=15_000.0)


class VolumeBoostIn(BaseModel):
    enabled: Optional[bool] = None
    boost_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    smoothing_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)


class SaveTrackIn(BaseModel):
    name: Optional[str] = Name


class RenameTrackIn(BaseModel):
    name: str = Field(max_length=60)


class TelemuteIn(BaseModel):
    enabled: bool


class CalibrationIn(BaseModel):
    action: str
    value: Optional[float] = None
    device: Optional[str] = None


class CreateWaypointIn(BaseModel):
    lat: float = Latitude
    lon: float = Longitude
    name: Optional[str] = Name


class UpdateWaypointIn(BaseModel):
    name: Optional[str] = Name
    lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    lon: Optional[float] = Field(default=None, ge=-180.0, le=180.0)


class RoutePointIn(BaseModel):
    lat: float = Latitude
    lon: float = Longitude
    name: str = Field(default="WPT", max_length=60)


class CreateRouteIn(BaseModel):
    points: list[RoutePointIn] = Field(min_length=2, max_length=100)
    name: Optional[str] = Name


class RenameRouteIn(BaseModel):
    name: str = Field(max_length=60)


class CreateBoundaryIn(BaseModel):
    lat: float = Latitude
    lon: float = Longitude
    radius_ft: float = Field(gt=0.0, le=100_000.0)
    name: Optional[str] = Name
    alarm_on: Literal["enter", "exit", "both"] = "exit"


class UpdateBoundaryIn(BaseModel):
    name: Optional[str] = Name
    radius_ft: Optional[float] = Field(default=None, gt=0.0, le=100_000.0)
    alarm_on: Optional[Literal["enter", "exit", "both"]] = None
    enabled: Optional[bool] = None


class SwitchIn(BaseModel):
    on: bool


class NavAlarmSettingsIn(BaseModel):
    arrival_enabled: Optional[bool] = None
    arrival_radius_nm: Optional[float] = Field(default=None, gt=0.0, le=100.0)
    off_course_enabled: Optional[bool] = None
    off_course_xte_nm: Optional[float] = Field(default=None, gt=0.0, le=100.0)
    anchor_radius_ft: Optional[float] = Field(default=None, gt=0.0, le=100_000.0)
    gps_accuracy_enabled: Optional[bool] = None
    gps_accuracy_hdop_max: Optional[float] = Field(default=None, gt=0.0, le=100.0)


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
                              "fetched_at": manifest.get("fetched_at"), "bytes": total_bytes,
                              "survey": (d / "survey_depth.json").exists()})
    return {"areas": areas}


@app.get("/api/chart/{area}/survey")
def chart_survey(area: str):
    """The surveyed-depth grid for one area (app/survey_depths.py), as built by fetch_depths.
    Served as the file it is: it is already compact JSON, and parsing it only to re-encode it
    would cost the Pi a second or two for nothing."""
    if "/" in area or ".." in area:
        return Response(status_code=404)
    path = DATA_DIR / "charts" / area / "survey_depth.json"
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(str(path), media_type="application/json")


CHART_SETTINGS_FILE = DATA_DIR / "chart_settings.json"


@app.get("/api/chart-settings")
def get_chart_settings():
    """Chart settings every screen shares -- the Pi's own display and a tablet must show the same
    depths, so the lake level lives here rather than in one browser's storage."""
    saved = read_dict(CHART_SETTINGS_FILE)
    level = saved.get("lake_level_ft")
    return {"lake_level_ft": level if isinstance(level, (int, float)) else None}


@app.post("/api/chart-settings")
def set_chart_settings(cmd: ChartSettingsIn):
    saved = read_dict(CHART_SETTINGS_FILE)
    saved["lake_level_ft"] = None if cmd.lake_level_ft is None else round(cmd.lake_level_ft, 1)
    write_json(CHART_SETTINGS_FILE, saved)
    return {"ok": True, **get_chart_settings()}


_bundle_cache: dict = {}   # (area, detail) -> (manifest mtime, bytes, gzipped bytes)


@app.get("/api/chart/{area}/{detail}")
def chart_bundle(area: str, detail: str, request: Request):
    """Every chart layer for one area at one detail level, in a single response.

    Bundled rather than a request per layer because this is served off local disk -- one response
    beats sixty round trips, and the whole point is that it works with no internet. Assembled
    from the layer files as they are (each is already GeoJSON) rather than parsed and re-encoded:
    for the whole of Old Hickory that is 5-6 MB, which took the Pi seconds to decode and encode
    again on every zoom across the detail threshold. Kept in memory, compressed once, until a
    fetch rewrites the manifest (the compression middleware would otherwise redo ~6 MB at its
    maximum level on every request)."""
    if detail not in DETAIL_OFFSETS or "/" in area or ".." in area:
        return Response(status_code=404)
    manifest_path = chart_store.manifest_path(area)
    try:
        mtime = manifest_path.stat().st_mtime
    except OSError:
        return Response(status_code=404)
    cached = _bundle_cache.get((area, detail))
    if not (cached and cached[0] == mtime):
        body = _assemble_bundle(area, detail)
        if body is None:
            return Response(status_code=404)
        cached = _bundle_cache[(area, detail)] = (mtime, body, gzip.compress(body, compresslevel=6))
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(cached[2], media_type="application/json", headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(cached[1], media_type="application/json")


def _assemble_bundle(area, detail):
    manifest = chart_store.manifest(area)
    if not manifest:
        return None
    parts = []
    for name, entry in manifest.get("layers", {}).items():
        raw = chart_store.read_layer(area, detail, name)
        if raw:
            parts.append(f'{json.dumps(name)}:{{"kind":{json.dumps(entry["kind"])},"geojson":{raw}}}')
    return (f'{{"area":{json.dumps(area)},"detail":{json.dumps(detail)},'
            f'"bbox":{json.dumps(manifest.get("bbox"))},"layers":{{{",".join(parts)}}}}}').encode("utf-8")


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
    """The single "Go To" target. Setting one stops any active route (Route To) -- the two are
    mutually exclusive, like on a real chartplotter.

    With a path (the browser plans one along the channel) the boat is steered along it leg by
    leg (app/guidance.py); without, it is a straight line from where the boat is."""
    global waypoint
    route_tracker.stop()
    guide = None
    if wp.path:
        points = [(p[0], p[1]) for p in wp.path]
        points[-1] = (wp.lat, wp.lon)   # the path ends exactly at the waypoint, whatever the planner snapped to
        guide = GuidedPath(points)
    elif track:
        guide = GuidedPath([(track[-1]["lat"], track[-1]["lon"]), (wp.lat, wp.lon)])
    # With no position yet (just booted, no fix) the start is unknown, so the straight line is
    # drawn from the first fix -- see _full_frame. It used to default to the destination itself, a
    # zero-length leg that made course read 0 and cross-track error meaningless for the whole
    # trip: an off-course alarm on nothing, and Course Up aimed north.
    waypoint = {"lat": wp.lat, "lon": wp.lon, "name": wp.name, "guide": guide}
    return {"ok": True, "waypoint": {"lat": wp.lat, "lon": wp.lon, "name": wp.name},
            "legs": len(guide.points) - 1 if guide else 1}


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
    # A level alone cannot answer "how far can I get on this?", which is the question the
    # Fuel Remaining / Economy / Range fields exist for. Gallons can.
    engine["fuel_capacity_gal"] = settings.fuel_capacity_gal
    engine["fuel_remaining_gal"] = (
        round(settings.fuel_capacity_gal * engine["fuel_pct"] / 100.0, 2)
        if engine.get("fuel_pct") is not None else None)
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
        if waypoint["guide"] is None:   # set before the first fix: the line starts where the boat first is
            waypoint["guide"] = GuidedPath([(fix.lat, fix.lon), (waypoint["lat"], waypoint["lon"])])
        guide = waypoint["guide"]
        nav = guide.tick(fix.lat, fix.lon, fix.sog_kn, fix.cog_deg)
        nav["waypoint"] = {"lat": waypoint["lat"], "lon": waypoint["lon"], "name": waypoint["name"],
                           "path": [[round(a, 6), round(b, 6)] for a, b in guide.points] if nav["legs"] > 1 else None}
        nav["relative_bearing_deg"] = relative_bearing(fix.heading_deg, nav["bearing_deg"])

    route_nav = route_tracker.tick(fix.lat, fix.lon, fix.sog_kn, fix.cog_deg) if fix.has_fix else None
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
