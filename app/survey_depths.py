"""Real lake-bottom depths from the Corps of Engineers' hydrographic surveys (eHydro).

The Inland ENC chart (chart_data.py) does not chart the bottom of the lake. On Old Hickory every
depth area is one of two things: "0 to 9 ft" outside the navigation channel, or "9 ft or more,
depth unknown" inside it -- the chart's job is the maintained 9-ft channel, nothing more. That is
why tapping the chart only ever said 0-9 ft.

The Corps does survey the bottom, though: the Nashville District runs condition surveys of the
channel, a mile or so at a time, and publishes every one through eHydro -- tens of thousands of
soundings per mile, bank to bank of the old river bed, as bottom *elevations* (feet, NAVD88) in
Tennessee state-plane coordinates. On Old Hickory those cover river miles 216-225 (the dam up past
Hermitage) and 297-313 (the upper lake below Cordell Hull Dam). Nothing public covers the coves or
the middle of the lake; the Quickdraw recording fills those in from the boat's own sounder.

This turns those surveys into a depth grid the chart can shade:

  * every survey's dense point file is projected to latitude/longitude (Lambert conformal conic,
    written out here rather than pulling in a projection library, and checked against one in
    the tests);
  * the points are binned into ~25 ft cells, keeping the SHALLOWEST sounding in each cell -- a
    chart errs toward less water, never more;
  * a newer survey of the same stretch replaces an older one, cell by cell;
  * small holes between survey lines are filled from their neighbours, again shallowest-first.

Elevations, not depths, are stored: depth is the lake level minus the bottom, and the lake moves
(Old Hickory runs 442-445 ft), so the chart applies the current level -- the "Lake level" setting,
as on a Garmin with lake charts.

    python -m app.fetch_depths old-hickory
"""
import io
import json
import math
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

EHYDRO_SURVEYS = ("https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/"
                  "eHydro_Survey_Data/FeatureServer/0")

# Survey files come from a static file store, but it is still someone else's server, and there is
# no published rate limit: one download every few seconds, and never the same file twice (they
# are cached, and a survey never changes once published).
DOWNLOAD_DELAY_S = 5.0
CELL_FT = 25.0
US_FOOT_M = 1200.0 / 3937.0

# Areas with surveys, and the pool they are in: a survey below a dam is in the next lake down, at
# a different level, so each area only takes its own pool's surveys.
DEPTH_AREAS = {
    "old-hickory": {"channel_area": "CELRN_CR_ND_OLD", "normal_pool_ft": 445.0,
                    "pool_note": "Old Hickory runs 442-445 ft (445 ft is normal summer pool)"},
}


# ---------------------------------------------------------------- projection
class LambertConformal:
    """Lambert conformal conic (two standard parallels) on an ellipsoid, inverse only: projected
    x/y in the projection's units to longitude/latitude in degrees. Snyder, "Map Projections -- A
    Working Manual" (USGS PP 1395), equations 15-9 to 15-11 and 7-9."""

    def __init__(self, lat1, lat2, lat0, lon0, false_e, false_n, unit_m, a=6378137.0, inv_f=298.257222101):
        f = 1.0 / inv_f
        self.e = math.sqrt(2 * f - f * f)
        self.a, self.lon0 = a, math.radians(lon0)
        self.false_e, self.false_n, self.unit_m = false_e, false_n, unit_m
        p1, p2, p0 = (math.radians(v) for v in (lat1, lat2, lat0))
        m1, m2 = self._m(p1), self._m(p2)
        t1, t2, t0 = self._t(p1), self._t(p2), self._t(p0)
        self.n = (math.log(m1) - math.log(m2)) / (math.log(t1) - math.log(t2))
        self.F = m1 / (self.n * t1 ** self.n)
        self.rho0 = a * self.F * t0 ** self.n

    def _m(self, phi):
        return math.cos(phi) / math.sqrt(1 - (self.e * math.sin(phi)) ** 2)

    def _t(self, phi):
        es = self.e * math.sin(phi)
        return math.tan(math.pi / 4 - phi / 2) / ((1 - es) / (1 + es)) ** (self.e / 2)

    def inverse(self, x, y):
        dx = (x - self.false_e) * self.unit_m
        dy = self.rho0 - (y - self.false_n) * self.unit_m
        rho = math.copysign(math.hypot(dx, dy), self.n)
        t = (rho / (self.a * self.F)) ** (1 / self.n)
        theta = math.atan2(dx, dy)
        lon = theta / self.n + self.lon0
        phi = math.pi / 2 - 2 * math.atan(t)
        for _ in range(8):
            es = self.e * math.sin(phi)
            nxt = math.pi / 2 - 2 * math.atan(t * ((1 - es) / (1 + es)) ** (self.e / 2))
            if abs(nxt - phi) < 1e-12:
                phi = nxt
                break
            phi = nxt
        return math.degrees(lon), math.degrees(phi)


# NAD83 / Tennessee (US survey feet), EPSG:2274 -- what every Nashville District survey uses.
TN_STATE_PLANE_FT = LambertConformal(35.25, 36.41666666666666, 34.33333333333334, -86.0,
                                     1968500.0, 0.0, US_FOOT_M)


def projection_for(name):
    """The projection a survey's `sourceprojection` names, or None if it isn't one handled here."""
    text = (name or "").lower()
    if "tennessee" in text and ("4100" in text or "feet" in text or text.strip() == "tennessee"):
        return TN_STATE_PLANE_FT
    return None


# ---------------------------------------------------------------- survey files
def parse_xyz(text):
    """(x, y, z) triples from an eHydro XYZ file: whitespace or comma separated, anything that
    isn't three numbers (headers, blank lines) skipped."""
    points = []
    for line in text.splitlines():
        parts = line.replace(",", " ").split()
        if len(parts) < 3:
            continue
        try:
            points.append((float(parts[0]), float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    return points


def survey_files(zip_bytes):
    """(dense points, chart-label points) from a survey zip, in its projected units. The dense set
    is the high-density _A.XYZ when the survey has one, else the thinned .XYZ; the labels are the
    thinned .XYZ, the soundings the surveyor chose to print on their own chart."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        dense_name = next((n for n in names if n.upper().endswith("_A.XYZ")), None)
        thin_name = next((n for n in names if n.upper().endswith(".XYZ")
                          and not n.upper().endswith(("_A.XYZ", "_FULL.XYZ"))), None)
        read = lambda n: parse_xyz(z.read(n).decode("latin-1")) if n else []
        thin = read(thin_name)
        dense = read(dense_name) or thin
    return dense, thin


def looks_like_elevations(points, normal_pool_ft):
    """True if the z values are bottom elevations in feet in this lake (some districts publish
    depths instead, positive down, which would read as a lake 440 ft deep)."""
    if not points:
        return False
    zs = sorted(p[2] for p in points)
    median = zs[len(zs) // 2]
    return normal_pool_ft - 150 < median < normal_pool_ft + 5


# ---------------------------------------------------------------- the grid
class DepthGrid:
    """Bottom elevations on a regular latitude/longitude grid of ~CELL_FT cells, anchored at the
    area's south-west corner. Each cell holds the shallowest (highest) bottom seen in it, in
    tenths of a foot; a later survey replaces an earlier one wherever it has data."""

    def __init__(self, west, south, mid_lat, cell_ft=CELL_FT):
        cell_m = cell_ft * US_FOOT_M
        self.west, self.south = west, south
        self.dlat = cell_m / 111132.0
        self.dlon = cell_m / (111320.0 * math.cos(math.radians(mid_lat)))
        self.cells = {}          # (i, j) -> tenths of a foot
        self.owner = {}          # (i, j) -> the survey that set it

    def index(self, lon, lat):
        return int((lon - self.west) // self.dlon), int((lat - self.south) // self.dlat)

    def add_survey(self, points_lonlat, survey_id=None):
        """One survey's (lon, lat, elevation ft) points: shallowest per cell within the survey,
        then laid over whatever older surveys put there."""
        mine = {}
        for lon, lat, z in points_lonlat:
            key = self.index(lon, lat)
            tenths = round(z * 10)
            if key not in mine or tenths > mine[key]:
                mine[key] = tenths
        self.cells.update(mine)
        self.owner.update(dict.fromkeys(mine, survey_id))
        return len(mine)

    def current(self, lon, lat, survey_id):
        """Whether this survey is still the one on record at this spot (not replaced by a newer one)."""
        return self.owner.get(self.index(lon, lat)) == survey_id

    def fill_gaps(self, passes=2, min_neighbours=4):
        """Fill empty cells that sit between survey lines -- at least `min_neighbours` of their
        eight neighbours have data -- with the shallowest neighbour. The swath does not spread
        into open water: a cell outside a straight edge has only three surveyed neighbours, so
        only holes and notches in the edge fill in."""
        filled = 0
        for _ in range(passes):
            new = {}
            candidates = set()
            for (i, j) in self.cells:
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        k = (i + di, j + dj)
                        if k not in self.cells:
                            candidates.add(k)
            for (i, j) in candidates:
                around = [self.cells[k] for k in ((i + di, j + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1))
                          if k in self.cells]
                if len(around) >= min_neighbours:
                    new[(i, j)] = max(around)
            if not new:
                break
            self.cells.update(new)
            filled += len(new)
        return filled

    def rows(self):
        """{j: [[first i, [tenths, ...]], ...]} -- runs of consecutive cells in each row, which
        is what keeps a lake's worth of cells to a few hundred kilobytes of JSON."""
        by_row = {}
        for (i, j), v in self.cells.items():
            by_row.setdefault(j, []).append((i, v))
        out = {}
        for j, cells in by_row.items():
            cells.sort()
            runs, start, values, prev = [], None, [], None
            for i, v in cells:
                if prev is not None and i == prev + 1:
                    values.append(v)
                else:
                    if values:
                        runs.append([start, values])
                    start, values = i, [v]
                prev = i
            runs.append([start, values])
            out[str(j)] = runs
        return out


def cells_from_rows(rows):
    """The inverse of DepthGrid.rows(), for tests and readers: {(i, j): tenths}."""
    cells = {}
    for j, runs in rows.items():
        for start, values in runs:
            for k, v in enumerate(values):
                cells[(start + k, int(j))] = v
    return cells


# ---------------------------------------------------------------- fetching
def _http_get(url, timeout=120):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def list_surveys(bbox, channel_area, fetch=_http_get):
    """The surveys of one pool that touch this area, oldest first, each with its outline."""
    west, south, east, north = bbox
    params = {
        "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "outSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        "where": f"channelareaidfk = '{channel_area}'",
        "outFields": "surveyjobidpk,surveydatestart,surveytype,sourcedatalocation,sourceprojection",
        "returnGeometry": "true", "maxAllowableOffset": 0.0001, "f": "json",
    }
    page = json.loads(fetch(f"{EHYDRO_SURVEYS}/query?" + urllib.parse.urlencode(params)))
    if page.get("error"):
        raise RuntimeError(page["error"].get("message", "eHydro query failed"))
    surveys = []
    for feature in page.get("features", []):
        a = feature.get("attributes", {})
        start = a.get("surveydatestart")
        surveys.append({
            "name": a.get("surveyjobidpk"),
            "date": datetime.fromtimestamp(start / 1000, tz=timezone.utc).date().isoformat() if start else None,
            "type": a.get("surveytype"),
            "url": a.get("sourcedatalocation"),
            "projection": a.get("sourceprojection"),
            "outline": [[[round(x, 6), round(y, 6)] for x, y in ring]
                        for ring in (feature.get("geometry") or {}).get("rings", [])],
        })
    surveys.sort(key=lambda s: (s["date"] or "", s["name"] or ""))
    return surveys


def build_area(area, bbox, cache_dir, out_path, fetch=_http_get, sleep=time.sleep, progress=print):
    """Download (or reuse) every survey for one area and write the depth grid file."""
    spec = DEPTH_AREAS[area]
    pool = spec["normal_pool_ft"]
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    surveys = list_surveys(bbox, spec["channel_area"], fetch)
    sleep(DOWNLOAD_DELAY_S)
    grid = DepthGrid(bbox[0], bbox[1], (bbox[1] + bbox[3]) / 2)
    labels, used, skipped = [], [], []
    for survey in surveys:
        name, url = survey["name"], survey["url"]
        proj = projection_for(survey["projection"])
        if not (name and url and proj):
            skipped.append({"name": name, "why": f"unhandled projection {survey['projection']!r}"})
            continue
        cached = cache_dir / f"{name}.zip"
        if cached.exists():
            data = cached.read_bytes()
        else:
            try:
                data = fetch(url)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                skipped.append({"name": name, "why": f"download failed: {exc}"})
                sleep(DOWNLOAD_DELAY_S)
                continue
            _write_bytes(cached, data)
            sleep(DOWNLOAD_DELAY_S)
        try:
            dense, thin = survey_files(data)
        except zipfile.BadZipFile:
            cached.unlink(missing_ok=True)
            skipped.append({"name": name, "why": "not a zip file"})
            continue
        if not looks_like_elevations(dense, pool):
            skipped.append({"name": name, "why": "values are not bottom elevations for this lake"})
            continue
        project = lambda pts: [(*proj.inverse(x, y), z) for x, y, z in pts]
        cells = grid.add_survey(project(dense), survey_id=name)
        labels.append((name, project(thin)))
        used.append({"name": name, "date": survey["date"], "type": survey["type"], "outline": survey["outline"]})
        progress(f"  {name}  {survey['date']}  {len(dense):>6} soundings  {cells:>5} cells")
    filled = grid.fill_gaps()
    # The printed soundings of a survey that a newer one has since replaced would contradict the
    # shading around them, so only the survey on record at each spot keeps its labels.
    soundings = [[round(lat, 6), round(lon, 6), round(z * 10)]
                 for name, pts in labels for lon, lat, z in pts if grid.current(lon, lat, name)]
    result = {
        "source": "US Army Corps of Engineers, eHydro hydrographic surveys",
        "service": EHYDRO_SURVEYS,
        "fetched_at": time.time(),
        "vertical_datum": "NAVD88",
        "units": "bottom elevation, tenths of a foot",
        "normal_pool_ft": pool,
        "pool_note": spec["pool_note"],
        "cell_ft": CELL_FT,
        "west": grid.west, "south": grid.south, "dlat": grid.dlat, "dlon": grid.dlon,
        "rows": grid.rows(),
        "soundings": soundings,
        "surveys": used,
        "skipped": skipped,
    }
    _write_bytes(Path(out_path), json.dumps(result, separators=(",", ":")).encode("utf-8"))
    return {"surveys": len(used), "skipped": skipped, "cells": len(grid.cells), "filled": filled,
            "soundings": len(soundings)}


def _write_bytes(path, data):
    """Atomically, so a half-written file never replaces a good one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
