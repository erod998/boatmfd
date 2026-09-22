"""Media control for a Garmin Fusion stereo over NMEA 2000.

Two implementations behind the same interface (state / handle / tick):
- SimulatedMedia: an in-memory stereo so the dashboard is fully demoable
  with no hardware. Used when no CAN channel is configured. (If one is
  configured but can't be opened, OfflineMedia reports "no stereo" instead
  of pretending.)
- FusionMedia: talks to a real Fusion head unit through a CAN interface
  wired to the boat's NMEA 2000 backbone (see fusion.py / n2k.py).

state() returns the same shape for both, so the UI never needs to know which
one it's talking to.
"""
import threading
import time

from . import fusion
from .artwork import ArtworkFinder

STATUS_STALE_S = 15.0
STATUS_REFRESH_S = 10.0


def _check_zone(zone, zones):
    if not isinstance(zone, int) or not 1 <= zone <= zones:
        raise ValueError("zone must be 1 to %d" % zones)


def _zone_rows(zones, volumes, names, limits, restore, max_volume):
    """What the UI shows for each zone: its name, volume, limit, and whether it is muted (volume 0 with a level to go back to)."""
    rows = []
    for z in range(1, zones + 1):
        volume = volumes[z - 1]
        limit = limits[z - 1] if limits and limits[z - 1] else max_volume
        rows.append({"id": z, "name": names.get(z) or "Zone %d" % z, "volume": volume,
                     "limit": min(limit, max_volume), "muted": volume == 0 and z in restore})
    return rows


def _master_targets(volumes, zones, limits, max_volume, target, restore=None):
    """New per-zone volumes for a "master" move to `target`: every un-muted zone shifts by the
    same amount needed to bring the loudest of them to `target`, so the mix between zones is
    kept rather than flattened, and each is still capped at its own limit. Zones muted via
    zone_mute (volume 0, remembered in `restore`) are left alone -- the master control doesn't
    wake up a zone someone deliberately silenced."""
    restore = restore or {}
    audible = [(z, volumes[z - 1]) for z in range(1, zones + 1) if not (volumes[z - 1] == 0 and z in restore)]
    if not audible:
        return list(volumes[:zones])
    delta = target - max(v for _, v in audible)
    targets = list(volumes[:zones])
    for z, v in audible:
        cap = min(limits[z - 1], max_volume) if limits and limits[z - 1] else max_volume
        targets[z - 1] = max(0, min(cap, v + delta))
    return targets


class SimulatedMedia:
    simulated = True

    SOURCES = [
        {"id": 0, "name": "FM", "type": "FM"},
        {"id": 1, "name": "Bluetooth", "type": "Bluetooth"},
        {"id": 2, "name": "USB", "type": "USB"},
        {"id": 3, "name": "AUX", "type": "Aux"},
    ]
    TRACKS = [
        ("Harbor Lights", "The Dockhands", "Slack Water", 214),
        ("Salt and Diesel", "Marlinspike", "Low Tide Sessions", 187),
        ("Nine Knots", "Ketch & Yawl", "Weather Helm", 241),
        ("Fair Winds", "Ketch & Yawl", "Weather Helm", 198),
    ]

    ZONE_NAMES = {1: "Cockpit", 2: "Cabin", 3: "Bow", 4: "Aft"}

    def __init__(self, max_volume=24, zones=4):
        self.max_volume = max_volume
        self.zones = zones
        self._power = True
        self._source_id = 1
        self._playing = True
        self._track = 0
        self._position = 0.0
        self._volumes = [10, 6, 4, 8]
        self._zone_restore = {}
        self._muted = False
        self._last_t = time.monotonic()

    def _has_tracks(self):
        return self._source_id in (1, 2)

    def tick(self):
        now = time.monotonic()
        dt, self._last_t = now - self._last_t, now
        if self._power and self._playing and self._has_tracks():
            self._position += dt
            while self._position >= self.TRACKS[self._track][3]:
                self._position -= self.TRACKS[self._track][3]
                self._track = (self._track + 1) % len(self.TRACKS)

    def handle(self, action, value=None, zone=1):
        self.tick()
        if action == "power":
            self._power = bool(value)
        elif action == "play":
            self._playing = True
        elif action == "pause":
            self._playing = False
        elif action in ("next", "prev"):
            step = 1 if action == "next" else -1
            self._track = (self._track + step) % len(self.TRACKS)
            self._position = 0.0
        elif action == "mute":
            self._muted = bool(value)
        elif action == "volume":
            _check_zone(zone, self.zones)
            if value is None or not 0 <= value <= self.max_volume:
                raise ValueError("volume must be 0-%d" % self.max_volume)
            self._volumes[zone - 1] = int(value)
        elif action == "master_volume":
            if value is None or not 0 <= value <= self.max_volume:
                raise ValueError("volume must be 0-%d" % self.max_volume)
            targets = _master_targets(self._volumes, self.zones, None, self.max_volume, int(value), self._zone_restore)
            for z, v in enumerate(targets, start=1):
                self._volumes[z - 1] = v
        elif action == "zone_mute":
            _check_zone(zone, self.zones)
            if value:
                if self._volumes[zone - 1] > 0:
                    self._zone_restore[zone] = self._volumes[zone - 1]
                self._volumes[zone - 1] = 0
            else:
                self._volumes[zone - 1] = self._zone_restore.pop(zone, max(1, self.max_volume // 3))
        elif action == "source":
            if value not in [s["id"] for s in self.SOURCES]:
                raise ValueError("unknown source")
            self._source_id = int(value)
            self._position = 0.0
        else:
            raise ValueError("unknown action '%s'" % action)

    def state(self):
        self.tick()
        source = next(s for s in self.SOURCES if s["id"] == self._source_id)
        title, artist, album, length = "", "", "", 0
        if source["type"] == "FM":
            title, artist = "FM 98.7", "Rhode Island Sound Radio"
        elif source["type"] == "Aux":
            title = "Aux input"
        else:
            title, artist, album, length = self.TRACKS[self._track]
        return {
            "simulated": True,
            "connected": True,
            "power": self._power,
            "sources": self.SOURCES,
            "source_id": self._source_id,
            "playing": self._playing and self._has_tracks(),
            "title": title,
            "artist": artist,
            "album": album,
            "art_url": None,  # fictional demo tracks: the UI draws generated cover art
            "length_s": length,
            "position_s": round(self._position, 1) if length else 0,
            "volume": self._volumes[0],
            "volumes": self._volumes,
            "zones": _zone_rows(self.zones, self._volumes, self.ZONE_NAMES, None, self._zone_restore, self.max_volume),
            "max_volume": self.max_volume,
            "muted": self._muted,
        }


class FusionMedia:
    simulated = False

    def __init__(self, node, max_volume=24, artwork=None, zones=2):
        self.node = node  # the shared NMEA 2000 node (see n2k.make_n2k_node)
        self.max_volume = max_volume
        self.zones = zones  # how many zones the stereo has (the status messages always carry four slots)
        self._artwork = artwork  # ArtworkFinder or None
        self._zone_names = {}     # zone number (1 = Zone 1) -> name set on the stereo
        self._zone_restore = {}   # zone -> volume to return to when un-muted
        self._lock = threading.Lock()
        self._stereo_address = None
        self._last_seen = 0.0
        self._last_request = 0.0
        self._sources = {}
        self._s = {
            "power": False, "source_id": None, "play_status": "stopped",
            "title": "", "artist": "", "album": "", "length_s": 0.0, "position_s": 0.0,
            "volumes": [0, 0, 0, 0], "volume_limits": None, "muted": False,
        }
        node.add_listener(self._on_message)

    def _on_message(self, pgn, source, payload):
        if pgn != fusion.PGN_STATUS:
            return
        update = fusion.parse_status(payload)
        if update is None:
            return
        with self._lock:
            self._stereo_address = source
            self._last_seen = time.monotonic()
            entry = update.pop("source_entry", None)
            if entry:
                self._sources[entry["id"]] = entry
            zone_name = update.pop("zone_name", None)
            if zone_name:
                self._zone_names[zone_name["zone"]] = zone_name["name"]
            self._s.update(update)

    def _send(self, payload):
        self.node.send_fast(fusion.PGN_COMMAND, payload, dest=self._stereo_address or 255)

    def tick(self):
        if time.monotonic() - self._last_request > STATUS_REFRESH_S:
            self._last_request = time.monotonic()
            try:
                self._send(fusion.request_status())
            except RuntimeError:
                pass  # address not claimed yet; retried next tick

    def _current_source(self):
        with self._lock:
            source_id = self._s["source_id"]
        if source_id is None:
            raise RuntimeError("stereo hasn't reported its source yet")
        return source_id

    def handle(self, action, value=None, zone=1):
        if action == "power":
            self._send(fusion.set_power(bool(value)))
        elif action == "play":
            self._send(fusion.media_command(self._current_source(), fusion.MEDIA_PLAY))
        elif action == "pause":
            self._send(fusion.media_command(self._current_source(), fusion.MEDIA_PAUSE))
        elif action == "next":
            self._send(fusion.media_command(self._current_source(), fusion.MEDIA_NEXT))
        elif action == "prev":
            self._send(fusion.media_command(self._current_source(), fusion.MEDIA_PREV))
        elif action == "mute":
            self._send(fusion.set_mute(bool(value)))
        elif action == "volume":
            _check_zone(zone, self.zones)
            if value is None or not 0 <= value <= self.max_volume:
                raise ValueError("volume must be 0-%d" % self.max_volume)
            self._send(fusion.set_zone_volume(zone, int(value)))
        elif action == "master_volume":
            if value is None or not 0 <= value <= self.max_volume:
                raise ValueError("volume must be 0-%d" % self.max_volume)
            with self._lock:
                current = list(self._s["volumes"])
                limits = self._s["volume_limits"]
                restore = dict(self._zone_restore)
            targets = _master_targets(current, self.zones, limits, self.max_volume, int(value), restore)
            for z, v in enumerate(targets, start=1):
                if v != current[z - 1]:
                    self._send(fusion.set_zone_volume(z, v))
        elif action == "zone_mute":
            # The stereo has one mute for everything, so muting a zone means turning it to 0 and remembering where it was.
            _check_zone(zone, self.zones)
            with self._lock:
                current = self._s["volumes"][zone - 1]
                if value:
                    if current > 0:
                        self._zone_restore[zone] = current
                    target = 0
                else:
                    target = self._zone_restore.pop(zone, max(1, self.max_volume // 3))
            self._send(fusion.set_zone_volume(zone, target))
        elif action == "source":
            if value is None or not 0 <= value <= 255:
                raise ValueError("invalid source")
            self._send(fusion.set_source(int(value)))
        else:
            raise ValueError("unknown action '%s'" % action)
        # The stereo confirms by broadcasting its new status; ask for it right away.
        self._send(fusion.request_status())

    def state(self):
        with self._lock:
            s = dict(self._s)
            sources = sorted(self._sources.values(), key=lambda x: x["id"])
            connected = time.monotonic() - self._last_seen < STATUS_STALE_S
            names, restore = dict(self._zone_names), dict(self._zone_restore)
            for zone in [z for z in restore if s["volumes"][z - 1] > 0]:   # changed on the stereo itself: forget the old level
                self._zone_restore.pop(zone, None)
                restore.pop(zone)
        return {
            "simulated": False,
            "connected": connected,
            "power": s["power"],
            "sources": sources,
            "source_id": s["source_id"],
            "playing": s["play_status"] == "playing",
            "title": s["title"],
            "artist": s["artist"],
            "album": s["album"],
            "art_url": self._artwork.get(s["artist"], s["album"], s["title"]) if self._artwork and s["power"] else None,
            "length_s": s["length_s"],
            "position_s": s["position_s"],
            "volume": s["volumes"][0],
            "volumes": s["volumes"],
            "zones": _zone_rows(self.zones, s["volumes"], names, s["volume_limits"], restore, self.max_volume),
            "max_volume": self.max_volume,
            "muted": s["muted"],
        }


class OfflineMedia:
    """Stands in when a real stereo link is configured but the CAN interface won't open: shows "no stereo"."""

    simulated = False

    def __init__(self, max_volume=24):
        self.max_volume = max_volume

    def tick(self):
        pass

    def handle(self, action, value=None, zone=1):
        raise RuntimeError("the NMEA 2000 (CAN) interface isn't available")

    def state(self):
        return {
            "simulated": False, "connected": False, "power": False, "sources": [], "source_id": None,
            "playing": False, "title": "", "artist": "", "album": "", "art_url": None,
            "length_s": 0, "position_s": 0, "volume": 0, "volumes": [0, 0, 0, 0], "zones": [],
            "max_volume": self.max_volume, "muted": False,
        }


def make_media_source(settings, node):
    """Real Fusion over the shared NMEA 2000 node if a CAN channel is configured (no stereo if it won't
    open); the simulator only when no CAN channel is configured."""
    if not settings.can_channel:
        return SimulatedMedia(settings.fusion_max_volume, settings.fusion_zones)
    if node is None:
        return OfflineMedia(settings.fusion_max_volume)
    return FusionMedia(node, settings.fusion_max_volume, ArtworkFinder() if settings.album_art else None, settings.fusion_zones)
