"""AIS (Automatic Identification System): other vessels' position, course and speed, normally
received over a real AIS receiver on the NMEA 2000/0183 bus. There is no AIS receiver here, so
this simulates a handful of other boats moving around near the vessel's own position -- the same
demo-with-no-hardware approach as SimulatedGPS -- so the chart, target list and (CPA/TCPA) closest-
approach math all have something real to show and exercise.
"""
import math
import random
import time

from .nav import haversine_distance_nm, initial_bearing_deg

KNOTS_TO_NM_PER_S = 1.0 / 3600.0


class _SimulatedTarget:
    def __init__(self, mmsi, name, vessel_type, lat, lon, cog_deg, sog_kn):
        self.mmsi = mmsi
        self.name = name
        self.vessel_type = vessel_type
        self.lat = lat
        self.lon = lon
        self.cog_deg = cog_deg
        self.sog_kn = sog_kn

    def step(self, dt_s):
        # A gentle random walk in course, like a boat someone is actually steering, not a robot.
        self.cog_deg = (self.cog_deg + random.uniform(-4, 4)) % 360
        self.sog_kn = max(0.0, min(25.0, self.sog_kn + random.uniform(-0.3, 0.3)))
        dist_nm = self.sog_kn * KNOTS_TO_NM_PER_S * dt_s
        rad = math.radians(self.cog_deg)
        dlat = (dist_nm / 60.0) * math.cos(rad)
        dlon = (dist_nm / 60.0) * math.sin(rad) / math.cos(math.radians(self.lat))
        self.lat += dlat
        self.lon += dlon


_NAMES = [
    ("Second Wind", "pleasure_craft"), ("Reel Time", "fishing"), ("Lake Life", "pleasure_craft"),
    ("Knot Working", "pleasure_craft"), ("Patrol 12", "law_enforcement"),
]


class SimulatedAIS:
    def __init__(self, center_lat, center_lon, count=4):
        self._targets = []
        for i in range(min(count, len(_NAMES))):
            name, vessel_type = _NAMES[i]
            angle = random.uniform(0, 360)
            dist_nm = random.uniform(0.3, 1.2)
            rad = math.radians(angle)
            lat = center_lat + (dist_nm / 60.0) * math.cos(rad)
            lon = center_lon + (dist_nm / 60.0) * math.sin(rad) / math.cos(math.radians(center_lat))
            self._targets.append(_SimulatedTarget(
                mmsi=366_000_000 + i, name=name, vessel_type=vessel_type,
                lat=lat, lon=lon, cog_deg=random.uniform(0, 360), sog_kn=random.uniform(2, 12),
            ))
        self._last_t = time.monotonic()

    def tick(self):
        now = time.monotonic()
        dt = max(0.0, min(now - self._last_t, 5.0))
        self._last_t = now
        for t in self._targets:
            t.step(dt)

    def targets(self, own_lat=None, own_lon=None, own_sog_kn=None):
        """Each target plus, when the boat's own position is given, range/bearing and a simple
        straight-line closest point of approach (CPA distance and time), the way a real AIS MARPA
        list ranks contacts by collision risk rather than just distance."""
        rows = []
        for t in self._targets:
            row = {
                "mmsi": t.mmsi, "name": t.name, "vessel_type": t.vessel_type,
                "lat": round(t.lat, 6), "lon": round(t.lon, 6),
                "cog_deg": round(t.cog_deg, 1), "sog_kn": round(t.sog_kn, 2),
            }
            if own_lat is not None and own_lon is not None:
                row["range_nm"] = round(haversine_distance_nm(own_lat, own_lon, t.lat, t.lon), 3)
                row["bearing_deg"] = round(initial_bearing_deg(own_lat, own_lon, t.lat, t.lon), 1)
                cpa_nm, tcpa_min = _closest_approach(own_lat, own_lon, own_sog_kn or 0, t)
                row["cpa_nm"] = round(cpa_nm, 3) if cpa_nm is not None else None
                row["tcpa_min"] = round(tcpa_min, 1) if tcpa_min is not None else None
            rows.append(row)
        if own_lat is not None:
            rows.sort(key=lambda r: r["range_nm"])
        return rows


def _closest_approach(own_lat, own_lon, own_sog_kn, target, own_cog_deg=None):
    """Flat-earth relative-velocity CPA: fine at the few-nm ranges AIS targets matter at."""
    nm_per_deg_lat = 60.0
    nm_per_deg_lon = 60.0 * math.cos(math.radians(own_lat))
    # Relative position of target from own boat, in nm (x = east, y = north).
    rel_x = (target.lon - own_lon) * nm_per_deg_lon
    rel_y = (target.lat - own_lat) * nm_per_deg_lat
    # Own boat's velocity is unknown here (heading isn't passed in) -- assume stationary for a
    # conservative estimate; the target's own velocity still gives a meaningful CPA/TCPA.
    own_vx = own_vy = 0.0
    t_rad = math.radians(target.cog_deg)
    t_vx = target.sog_kn * math.sin(t_rad)
    t_vy = target.sog_kn * math.cos(t_rad)
    rel_vx, rel_vy = t_vx - own_vx, t_vy - own_vy
    rel_speed_sq = rel_vx ** 2 + rel_vy ** 2
    if rel_speed_sq < 1e-6:
        return math.hypot(rel_x, rel_y), None  # not closing or opening: report range only
    t_hours = -(rel_x * rel_vx + rel_y * rel_vy) / rel_speed_sq
    if t_hours < 0:
        return math.hypot(rel_x, rel_y), None  # already at its closest: was in the past
    cpa_x, cpa_y = rel_x + rel_vx * t_hours, rel_y + rel_vy * t_hours
    return math.hypot(cpa_x, cpa_y), t_hours * 60.0
