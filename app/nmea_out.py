"""The boat's position, speed, heading, depth and water temperature as NMEA 0183, over TCP.

For a chart app of its own on the helm display (GPS Nautical Charts' Boating App, see
kiosk/chart-app.py): its settings take an address and port to read NMEA 0183 from, and this is
that source -- 127.0.0.1, port 10110 (the usual NMEA-over-IP port) unless BOAT_NMEA_TCP_PORT and
BOAT_NMEA_TCP_HOST say otherwise. Once a second, from the same readings the dashboard shows:

    RMC, GGA, VTG   position, speed and course over ground (GPS)
    HDM             heading, magnetic (the compass)
    DPT, DBT        depth below the transducer (the depth sounder)
    MTW             water temperature

A reading the dashboard doesn't have (no fix, no depth) is sent empty or not at all, never made up.
"""
import asyncio
import math
import time


def checksum(body):
    c = 0
    for ch in body.encode("ascii"):
        c ^= ch
    return "%02X" % c


def sentence(body):
    """'GPRMC,...' -> '$GPRMC,...*CS\\r\\n'"""
    return "$%s*%s\r\n" % (body, checksum(body))


def _lat(v):
    a = abs(v)
    deg = int(a)
    return "%02d%07.4f" % (deg, (a - deg) * 60.0), "N" if v >= 0 else "S"


def _lon(v):
    a = abs(v)
    deg = int(a)
    return "%03d%07.4f" % (deg, (a - deg) * 60.0), "E" if v >= 0 else "W"


def _num(v, fmt="%.1f"):
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else fmt % v


def sentences(gps, boat, now=None):
    """The NMEA 0183 sentences for one second's readings. gps: the telemetry frame's "gps" dict
    (lat, lon, sog_kn, cog_deg, heading_deg, satellites, hdop, fix_quality, has_fix, timestamp);
    boat: its "boat_info" (depth_ft, water_temp_f)."""
    t = time.gmtime(gps.get("timestamp") or now or time.time())
    hms = time.strftime("%H%M%S.00", t)
    dmy = time.strftime("%d%m%y", t)
    out = []
    if gps.get("has_fix") and gps.get("lat") is not None and gps.get("lon") is not None:
        lat, ns = _lat(gps["lat"])
        lon, ew = _lon(gps["lon"])
        sog, cog = gps.get("sog_kn") or 0.0, (gps.get("cog_deg") or 0.0) % 360.0
        out.append(sentence("GPRMC,%s,A,%s,%s,%s,%s,%.1f,%.1f,%s,,,A" % (hms, lat, ns, lon, ew, sog, cog, dmy)))
        out.append(sentence("GPGGA,%s,%s,%s,%s,%s,%d,%02d,%s,,M,,M,," % (
            hms, lat, ns, lon, ew, gps.get("fix_quality") or 1, gps.get("satellites") or 0,
            _num(gps.get("hdop")))))
        out.append(sentence("GPVTG,%.1f,T,,M,%.1f,N,%.1f,K,A" % (cog, sog, sog * 1.852)))
    else:
        out.append(sentence("GPRMC,%s,V,,,,,,,%s,,,N" % (hms, dmy)))
        out.append(sentence("GPGGA,%s,,,,,0,00,,,M,,M,," % hms))
    if gps.get("heading_deg") is not None and gps.get("has_fix"):
        out.append(sentence("HCHDM,%.1f,M" % (gps["heading_deg"] % 360.0)))
    depth_ft = boat.get("depth_ft")
    if depth_ft is not None:
        m = depth_ft * 0.3048
        out.append(sentence("SDDPT,%.1f,0.0" % m))
        out.append(sentence("SDDBT,%.1f,f,%.1f,M,%.1f,F" % (depth_ft, m, depth_ft / 6.0)))
    temp_f = boat.get("water_temp_f")
    if temp_f is not None:
        out.append(sentence("YXMTW,%.1f,C" % ((temp_f - 32.0) * 5.0 / 9.0)))
    return out


class NmeaServer:
    """A TCP server that sends every client the same sentences, once a second (publish())."""

    def __init__(self, host="127.0.0.1", port=10110):
        self.host, self.port = host, port
        self._writers = set()
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._client, self.host, self.port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for w in list(self._writers):
            w.close()

    async def _client(self, reader, writer):
        self._writers.add(writer)
        try:
            while await reader.read(1024):   # nothing to read from a chart app; this notices it leave
                pass
        except (ConnectionError, OSError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    async def publish(self, lines):
        if not self._writers:
            return
        data = "".join(lines).encode("ascii")
        for w in list(self._writers):
            try:
                w.write(data)
                await asyncio.wait_for(w.drain(), timeout=2.0)
            except Exception:           # gone, or stalled: dropped; it reconnects on its own
                self._writers.discard(w)
                w.close()
