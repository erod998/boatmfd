"""Depth and sea temperature broadcast on NMEA 2000 by a depth (or depth/speed/temperature)
transducer, such as an Airmar DST800 or DST810.

- PGN 128267 "Water Depth": depth below the transducer, plus the transducer's own offset to
  the waterline (positive) or the keel (negative) — the device is configured with this at
  install time, but it is often left at 0 and corrected on the display instead (see the
  `depth.offset_ft` calibration value in sensors.py).
- PGN 130311 "Environmental Parameters": a generic multi-source temperature message, kept only
  when its Temperature Source field says "Sea Temperature" (0) — the same PGN also carries
  outside air, cabin and other temperatures from other instruments.

Field layouts are from canboat's PGN database. Values a device marks as not available come
back as None (never as a number).
"""
import threading
import time

PGN_WATER_DEPTH = 128267
PGN_ENVIRONMENTAL = 130311
METERS_PER_FOOT = 0.3048
TEMP_SOURCE_SEA = 0


def parse_water_depth(payload):
    """(sid, depth below transducer in m, transducer offset in m), or None if too short.

    Byte 0 is the SID, a sequence number that ties one second's depth to the same second's speed
    and temperature (or 0xFF, unused). It is not an instance: this PGN has none, so a transducer
    is told apart from another by its source address alone."""
    if len(payload) < 5:
        return None
    raw_depth = int.from_bytes(payload[1:5], "little")
    depth_m = raw_depth * 0.01 if raw_depth < 0xFFFFFFFD else None
    offset_m = None
    if len(payload) >= 7:
        raw_offset = int.from_bytes(payload[5:7], "little", signed=True)
        offset_m = raw_offset * 0.001 if raw_offset != -32768 else None
    return {"sid": payload[0], "depth_m": depth_m, "offset_m": offset_m}


def parse_environmental(payload):
    """Sea-temperature reading in Celsius from PGN 130311, or None if this is some other
    temperature source, the source is marked not available, or the payload is too short."""
    if len(payload) < 4:
        return None
    source = payload[1] & 0x3F  # bits 0-5 of byte 1; bits 6-7 are the humidity source
    if source != TEMP_SOURCE_SEA:
        return None
    raw_temp = int.from_bytes(payload[2:4], "little")
    if raw_temp >= 0xFFFD:
        return None
    celsius = raw_temp * 0.01 - 273.15
    if not -10.0 <= celsius <= 60.0:  # outside anything a real sea temperature could be: a filler value
        return None
    return {"sid": payload[0], "water_temp_c": celsius}   # byte 0 is the SID here too


def c_to_f(celsius):
    return None if celsius is None else celsius * 9 / 5 + 32


class N2kEnvData:
    """Keeps the latest depth and sea-temperature readings seen on the bus; stale readings count as no data."""

    STALE_S = {PGN_WATER_DEPTH: 3.0, PGN_ENVIRONMENTAL: 4.0}
    REPORT_S = 30.0  # how long the calibration page keeps listing a device that has gone quiet

    def __init__(self, node, depth_source=None, clock=time.monotonic):
        self.depth_source = depth_source   # a transducer's source address, or None for whichever is freshest
        self._clock = clock
        self._depth = {}   # source -> (time seen, depth_m, offset_m)
        self._temp = {}    # source -> (time seen, water_temp_c)
        self._lock = threading.Lock()
        node.subscribe(PGN_WATER_DEPTH, PGN_ENVIRONMENTAL)  # both are single-frame PGNs
        node.add_listener(self._on_message)

    def _on_message(self, pgn, source, payload):
        now = self._clock()
        if pgn == PGN_WATER_DEPTH:
            parsed = parse_water_depth(payload)
            if not parsed:
                return
            with self._lock:
                if parsed["depth_m"] is None:
                    self._depth.pop(source, None)  # "not available": don't let an old depth linger
                else:
                    self._depth[source] = (now, parsed["depth_m"], parsed["offset_m"])
        elif pgn == PGN_ENVIRONMENTAL:
            parsed = parse_environmental(payload)
            if parsed:
                with self._lock:
                    self._temp[source] = (now, parsed["water_temp_c"])

    def depth_ft(self, extra_offset_ft=0.0):
        """Depth below the surface (or keel, depending on how the transducer's own offset is set) in
        feet, plus a further calibration offset, or None if nothing fresh has been heard."""
        now = self._clock()
        with self._lock:
            fresh = [(t, depth_m, offset_m) for source, (t, depth_m, offset_m) in self._depth.items()
                     if self.depth_source in (None, source) and now - t <= self.STALE_S[PGN_WATER_DEPTH]]
        if not fresh:
            return None
        _, depth_m, offset_m = max(fresh)
        return (depth_m + (offset_m or 0.0)) / METERS_PER_FOOT + extra_offset_ft

    def water_temp_f(self):
        now = self._clock()
        with self._lock:
            fresh = [(t, c) for (t, c) in self._temp.values() if now - t <= self.STALE_S[PGN_ENVIRONMENTAL]]
        return c_to_f(max(fresh)[1]) if fresh else None

    def heard(self):
        """Is anything (depth or sea temperature) fresh enough to be shown right now?"""
        return self.depth_ft() is not None or self.water_temp_f() is not None

    def status(self):
        """Everything heard recently, for the calibration page."""
        now = self._clock()
        with self._lock:
            depths = [{"source": s, "depth_ft": d / METERS_PER_FOOT,
                       "offset_ft": None if o is None else o / METERS_PER_FOOT, "age_s": round(now - t, 1)}
                      for s, (t, d, o) in self._depth.items() if now - t <= self.REPORT_S]
            temps = [{"source": s, "water_temp_f": c_to_f(c), "age_s": round(now - t, 1)}
                     for s, (t, c) in self._temp.items() if now - t <= self.REPORT_S]
        return {"depths": depths, "temps": temps, "depth_source": self.depth_source}
