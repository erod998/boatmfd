"""GPS source abstraction.

Two implementations:
- SimulatedGPS: cruises a loop route through open water so the dashboard is
  fully demoable with no hardware attached. Used automatically when no
  serial GPS is configured/available.
- SerialGPS: reads real NMEA 0183 sentences (GGA/RMC/VTG) from a serial GPS
  module (e.g. u-blox NEO-6M/NEO-M8N on a Raspberry Pi UART or USB adapter).
  Requires pyserial + pynmea2, both optional dependencies.
"""
import math
import random
import time
from dataclasses import dataclass, field

from .nav import haversine_distance_nm, initial_bearing_deg


@dataclass
class Fix:
    lat: float
    lon: float
    sog_kn: float = 0.0       # speed over ground, knots
    cog_deg: float = 0.0      # course over ground, degrees true
    heading_deg: float = 0.0  # magnetic/compass heading (may differ from COG)
    satellites: int = 0
    hdop: float = 99.9
    fix_quality: int = 0      # 0 = no fix, 1 = GPS fix, 2 = DGPS fix
    timestamp: float = field(default_factory=time.time)

    @property
    def has_fix(self):
        return self.fix_quality > 0


# Down the main channel of Old Hickory Lake (Cumberland River, Hendersonville TN 37075),
# river mile ~225 to ~236. Waypoints were picked from the USACE Inland ENC and checked to
# stay in water with >= ~60 m of clearance.
_CHANNEL = [
    (36.27614, -86.5624), (36.30615, -86.5636), (36.30841, -86.5592), (36.31292, -86.5572),
    (36.32066, -86.5480), (36.32066, -86.5464), (36.31550, -86.5396), (36.29098, -86.5392),
    (36.28098, -86.5292), (36.28033, -86.5240), (36.27904, -86.5224), (36.28259, -86.5172),
    (36.28550, -86.5156), (36.29098, -86.5152), (36.30034, -86.5116), (36.31131, -86.4964),
    (36.31389, -86.4960),
]
ROUTE = _CHANNEL + _CHANNEL[-2:0:-1]  # out and back, forever


class SimulatedGPS:
    """Cruises the boat up and down a channel route at a boat-like turn rate, for demo purposes."""

    def __init__(self, route=ROUTE, speed_kn=6.0, start_index=1):
        self.route = route
        self._target = (start_index + 1) % len(route)
        self.lat, self.lon = route[start_index]
        self.sog_kn = speed_kn
        self.cog_deg = initial_bearing_deg(*route[start_index], *route[self._target])

    def read(self) -> Fix:
        target = self.route[self._target]
        if haversine_distance_nm(self.lat, self.lon, *target) < 0.04:
            self._target = (self._target + 1) % len(self.route)
            target = self.route[self._target]

        # Steer toward the next route point with a limited turn rate, like a real boat.
        turn = (initial_bearing_deg(self.lat, self.lon, *target) - self.cog_deg + 540) % 360 - 180
        self.cog_deg = (self.cog_deg + max(-6.0, min(6.0, turn))) % 360
        self.sog_kn = max(0.5, min(9.0, self.sog_kn + random.uniform(-0.15, 0.15)))

        dt_hours = 1.0 / 3600.0  # advance ~1 simulated second per read() call
        dist_nm = self.sog_kn * dt_hours
        dlat = (dist_nm / 60.0) * math.cos(math.radians(self.cog_deg))
        dlon = (dist_nm / 60.0) * math.sin(math.radians(self.cog_deg)) / math.cos(math.radians(self.lat))
        self.lat += dlat
        self.lon += dlon

        # A small compass wobble, not independent per-second noise: +-2 deg with no correlation between
        # ticks made the chart's heading-up rotation (and the boat icon's own rotation in North Up)
        # visibly shake every second, on top of the already-smooth, turn-rate-limited cog_deg above.
        heading = (self.cog_deg + random.uniform(-0.3, 0.3)) % 360
        return Fix(
            lat=self.lat,
            lon=self.lon,
            sog_kn=round(self.sog_kn, 2),
            cog_deg=round(self.cog_deg, 1),
            heading_deg=round(heading, 1),
            satellites=random.randint(7, 12),
            hdop=round(random.uniform(0.8, 1.6), 1),
            fix_quality=1,
        )


class SerialGPS:
    """Reads NMEA sentences from a real serial GPS module."""

    def __init__(self, port="/dev/serial0", baudrate=9600, timeout=1.0):
        import serial  # pyserial
        self._serial = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        self._last = Fix(lat=0.0, lon=0.0)

    def read(self) -> Fix:
        import pynmea2

        deadline = time.time() + 1.0
        while time.time() < deadline:
            line = self._serial.readline().decode("ascii", errors="replace").strip()
            if not line.startswith("$"):
                continue
            try:
                msg = pynmea2.parse(line)
            except pynmea2.ParseError:
                continue

            if isinstance(msg, pynmea2.types.talker.GGA):
                self._last.lat = msg.latitude
                self._last.lon = msg.longitude
                self._last.satellites = int(msg.num_sats or 0)
                self._last.hdop = float(msg.horizontal_dil or 99.9)
                self._last.fix_quality = int(msg.gps_qual or 0)
            elif isinstance(msg, pynmea2.types.talker.RMC):
                self._last.lat = msg.latitude
                self._last.lon = msg.longitude
                if msg.spd_over_grnd is not None:
                    self._last.sog_kn = float(msg.spd_over_grnd)
                if msg.true_course is not None:
                    self._last.cog_deg = float(msg.true_course)
                    self._last.heading_deg = float(msg.true_course)
            elif isinstance(msg, pynmea2.types.talker.VTG):
                if msg.spd_over_grnd_kts is not None:
                    self._last.sog_kn = float(msg.spd_over_grnd_kts)
                if msg.true_track is not None:
                    self._last.heading_deg = float(msg.true_track)

        self._last.timestamp = time.time()
        return self._last


class NoFixGPS:
    """Stands in when a real GPS is configured but can't be opened: always no fix, never a made-up position."""

    def read(self) -> Fix:
        return Fix(lat=0.0, lon=0.0)


def make_gps_source(settings):
    """Real serial GPS if a port is configured; the simulator only when none is."""
    if settings.gps_port:
        try:
            return SerialGPS(settings.gps_port, settings.gps_baud)
        except Exception as exc:  # pragma: no cover - hardware-dependent
            print(f"[gps] could not open serial GPS on {settings.gps_port} ({exc}); showing NO FIX")
            return NoFixGPS()
    return SimulatedGPS()
