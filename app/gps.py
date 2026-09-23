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
from dataclasses import dataclass, field, replace

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

    def __init__(self, route=ROUTE, speed_kn=35.0, start_index=1):
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
        self.sog_kn = max(0.5, min(40.0, self.sog_kn + random.uniform(-0.4, 0.4)))

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


# No valid position for this long and the fix is reported lost, whatever was heard last. A GPS
# that goes silent -- antenna cable, dead module, unplugged USB -- must not leave the last position
# on screen presented as current; for a chartplotter that is the worst failure there is.
FIX_STALE_S = 3.0
# Below this a GPS course is noise: it cannot measure heading at all, only the direction the
# position is moving, and a boat sitting at the dock "moves" in a random direction every second.
# Course and heading hold their last good value instead of spinning the boat icon and the chart.
COURSE_MIN_SOG_KN = 1.0
REOPEN_S = 5.0   # how often to try the port again after it goes away


class SerialGPS:
    """Reads NMEA 0183 sentences from a real GPS module on a serial port.

    Rebuilt after a review found it had never run against a real module (the simulator does not
    touch it) and would have failed badly on one:

    * It stamped every read as current whether or not anything arrived, so a dead GPS kept the
      last position on screen as live -- indefinitely. Now a fix needs a valid position within
      FIX_STALE_S, and an explicit loss (RMC status V, GGA quality 0) ends it at once.
    * An RMC with status V (no fix) parses with latitude and longitude 0.0, and those were applied
      -- putting the boat off the coast of Africa, dragging the track across the world and adding
      thousands of miles to the trip. Void sentences are now ignored for position entirely.
    * Line noise makes pynmea2 raise ValueError, not ParseError, which escaped and failed the whole
      telemetry frame. Any sentence that fails to parse is now just dropped, and checksums are
      required, so noise that clips one off cannot slip corrupted numbers through.
    * An unplugged USB GPS raised out of every read, freezing the chart, nav, trip, stereo and
      alarm banner until a restart, and the port was never reopened. Now it reports no fix, keeps
      trying the port every REOPEN_S, and carries on when the GPS comes back.
    * Heading was set straight from course over ground at any speed (see COURSE_MIN_SOG_KN).
    """

    def __init__(self, port="/dev/serial0", baudrate=9600, timeout=1.0, opener=None, clock=time.time):
        self.port, self.baudrate, self.timeout = port, baudrate, timeout
        self._opener = opener or self._open_port
        self._clock = clock
        self._serial = None
        self._reopen_at = 0.0
        self._last_error = None
        self._state = Fix(lat=0.0, lon=0.0)   # the last good values of each field
        self._valid_until = 0.0               # a fix is current until this time
        self._gga_seen = False
        self._connect()

    def _open_port(self):
        import serial  # pyserial
        return serial.Serial(self.port, baudrate=self.baudrate, timeout=self.timeout)

    def _note(self, message):
        """Log a change of state once, not every second it persists."""
        if message != self._last_error:
            print(f"[gps] {message}")
            self._last_error = message

    def _connect(self):
        try:
            self._serial = self._opener()
            self._note(f"reading NMEA from {self.port}")
        except Exception as exc:
            self._serial = None
            self._reopen_at = self._clock() + REOPEN_S
            self._note(f"could not open {self.port} ({exc}); showing NO FIX and retrying")

    def _drop_port(self, exc):
        try:
            self._serial.close()
        except Exception:
            pass
        self._serial = None
        self._reopen_at = self._clock() + REOPEN_S
        self._note(f"lost {self.port} ({exc}); showing NO FIX and retrying")

    def _handle(self, raw):
        import pynmea2

        line = raw.decode("ascii", errors="replace").strip()
        if not line.startswith("$"):
            return
        try:
            msg = pynmea2.parse(line, check=True)
        except Exception:   # bad or missing checksum, line noise, a sentence type pynmea2 rejects
            return
        now = self._clock()
        st = self._state
        if isinstance(msg, pynmea2.types.talker.GGA):
            self._gga_seen = True
            quality = int(msg.gps_qual or 0)
            st.satellites = int(msg.num_sats or 0)
            st.hdop = float(msg.horizontal_dil or 99.9)
            if quality > 0:
                st.lat, st.lon, st.fix_quality = msg.latitude, msg.longitude, quality
                self._valid_until = now + FIX_STALE_S
            else:
                self._valid_until = 0.0            # the receiver says the fix is gone: believe it
        elif isinstance(msg, pynmea2.types.talker.RMC):
            if msg.status != "A":
                self._valid_until = 0.0            # void: 0.0/0.0 for position, nothing to use
                return
            st.lat, st.lon = msg.latitude, msg.longitude
            self._valid_until = now + FIX_STALE_S
            if msg.spd_over_grnd is not None:
                st.sog_kn = float(msg.spd_over_grnd)
            if msg.true_course is not None and st.sog_kn >= COURSE_MIN_SOG_KN:
                st.cog_deg = st.heading_deg = float(msg.true_course)
        elif isinstance(msg, pynmea2.types.talker.VTG):
            if msg.spd_over_grnd_kts is not None:
                st.sog_kn = float(msg.spd_over_grnd_kts)
            if msg.true_track is not None and st.sog_kn >= COURSE_MIN_SOG_KN:
                st.cog_deg = st.heading_deg = float(msg.true_track)

    def read(self) -> Fix:
        now = self._clock()
        if self._serial is None and now >= self._reopen_at:
            self._connect()
        if self._serial is not None:
            deadline = now + 1.0
            try:
                while self._clock() < deadline:
                    raw = self._serial.readline()
                    if raw:
                        self._handle(raw)
            except Exception as exc:   # unplugged, I/O error: the rest of the dashboard carries on
                self._drop_port(exc)
        # A copy, never the live object: the caller keeps it while the next read is in progress.
        fix = replace(self._state)
        if self._clock() >= self._valid_until:
            fix.fix_quality = 0
        elif not self._gga_seen:
            fix.fix_quality = 1   # an RMC-only receiver: status A is a fix, just without a quality
        fix.timestamp = self._clock()
        return fix


class NoFixGPS:
    """Stands in when a real GPS is configured but can't be opened: always no fix, never a made-up position."""

    def read(self) -> Fix:
        return Fix(lat=0.0, lon=0.0)


def make_gps_source(settings):
    """Real serial GPS if a port is configured; the simulator only when none is. A configured GPS
    that is missing at boot shows NO FIX and is picked up when it appears -- never the simulator,
    which would put a made-up boat on a real chart."""
    if settings.gps_port:
        return SerialGPS(settings.gps_port, settings.gps_baud)
    return SimulatedGPS()
