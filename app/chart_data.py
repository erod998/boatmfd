"""Local vector chart store -- the boat's charts, on disk, as data rather than pictures.

The first version of this cached *rendered tile images* from the Corps of Engineers' map
server: ~4,500 throttled HTTP requests and ~88 MB to cover one lake at five fixed zoom
levels, needing hours, and still unable to answer "what is that buoy?" or restyle for night
without re-rendering everything.

This fetches the underlying vector features instead. The same service exposes every S-57
feature class as a queryable layer (capabilities "Map,Query,Data"), so one request per layer
returns real GeoJSON -- depth areas with their actual depth ranges in metres, the shoreline,
buoys and beacons and lights with their full attributes. For Old Hickory Lake that is 409
features, about 5 MB, in 28 requests -- seconds, not hours, and gentle on a shared government
server in a way tile scraping never was.

Everything downstream gets better for free: vectors draw crisply at any zoom, restyle to a
night palette by changing colours rather than re-rendering, and can be tapped to identify.

Geometry is generalised server-side (maxAllowableOffset) into two levels of detail, because
raw shoreline polygons carry enormous vertex counts -- the lake's depth areas are 2.1 MB raw
but 207 KB at 11 m precision, which is far below what matters at any zoom a boat navigates at.
"""
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

IENC_SERVICE = "https://ienccloud.us/arcgis/rest/services/IENC/USACE_IENC_Master_Service/MapServer"

# Detail levels, in degrees of generalisation tolerance. "detail" is used once zoomed in far
# enough to be navigating; "overview" is for the wide view where 55 m of precision is invisible.
DETAIL_OFFSETS = {"detail": 0.00002, "overview": 0.0005}

# The S-57 feature classes worth drawing on a chart, by the service's own layer ids. Deliberately
# excludes BUILDING_SINGLE_AREA (5,158 features over this lake) and ROADWAY_LINE (1,137) -- land
# clutter that buries the water. "kind" drives how the frontend styles it.
CHART_LAYERS = {
    # --- areas: drawn first, underneath everything ---
    97: {"name": "land", "kind": "area"},
    96: {"name": "depth_area", "kind": "depth"},
    75: {"name": "rivers", "kind": "area"},
    94: {"name": "built_up", "kind": "area"},
    89: {"name": "harbour", "kind": "area"},
    98: {"name": "berths", "kind": "area"},
    92: {"name": "bridge", "kind": "area"},
    62: {"name": "dam", "kind": "area"},
    93: {"name": "shoreline_construction", "kind": "area"},
    69: {"name": "pylons_area", "kind": "area"},
    85: {"name": "caution", "kind": "caution"},
    53: {"name": "rock_area", "kind": "danger"},
    54: {"name": "wreck_area", "kind": "danger"},
    81: {"name": "obstruction_area", "kind": "danger"},
    # --- lines ---
    33: {"name": "coastline", "kind": "coastline"},
    36: {"name": "depth_contour", "kind": "contour"},
    46: {"name": "recommended_track", "kind": "track"},
    43: {"name": "overhead_cable", "kind": "hazard_line"},
    52: {"name": "submarine_pipeline", "kind": "hazard_line"},
    42: {"name": "obstruction_line", "kind": "hazard_line"},
    # --- points: navigation aids and hazards, drawn on top ---
    15: {"name": "lateral_buoy", "kind": "buoy"},
    2: {"name": "isolated_danger_buoy", "kind": "buoy"},
    6: {"name": "special_purpose_buoy", "kind": "buoy"},
    14: {"name": "lateral_beacon", "kind": "beacon"},
    9: {"name": "daymark", "kind": "beacon"},
    16: {"name": "light", "kind": "light"},
    10: {"name": "distance_mark", "kind": "distance_mark"},
    19: {"name": "obstruction", "kind": "danger_point"},
    28: {"name": "underwater_rock", "kind": "danger_point"},
    31: {"name": "wreck", "kind": "danger_point"},
    20: {"name": "pile", "kind": "danger_point"},
    21: {"name": "pylons", "kind": "danger_point"},
    13: {"name": "landmark", "kind": "landmark"},
    23: {"name": "shoreline_construction_point", "kind": "landmark"},
}

MAX_PAGE = 1000  # the service caps a single response at 2,000; stay well under and page


def _http_get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def query_url(layer_id, bbox, offset=None, result_offset=0):
    """A GeoJSON query for one layer over one bbox. bbox is (west, south, east, north) in WGS84."""
    west, south, east, north = bbox
    params = {
        "geometry": json.dumps({"xmin": west, "ymin": south, "xmax": east, "ymax": north,
                                 "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*", "returnGeometry": "true",
        "resultRecordCount": MAX_PAGE, "resultOffset": result_offset,
        "f": "geojson",
    }
    if offset is not None:
        params["maxAllowableOffset"] = offset
    return f"{IENC_SERVICE}/{layer_id}/query?" + urllib.parse.urlencode(params)


class ChartStore:
    """GeoJSON feature collections on disk, one file per layer per detail level, under
    <base_dir>/<area>/<detail>/<layer>.geojson, plus a manifest describing what was fetched."""

    def __init__(self, base_dir, fetch=None, sleep=time.sleep):
        self.base_dir = Path(base_dir)
        self._fetch = fetch or _http_get
        self._sleep = sleep

    def area_dir(self, area):
        return self.base_dir / area

    def manifest_path(self, area):
        return self.area_dir(area) / "manifest.json"

    def manifest(self, area):
        path = self.manifest_path(area)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def layer_path(self, area, detail, layer_name):
        return self.area_dir(area) / detail / f"{layer_name}.geojson"

    def read_layer(self, area, detail, layer_name):
        path = self.layer_path(area, detail, layer_name)
        return path.read_text() if path.exists() else None

    def _write(self, path, text):
        """Write atomically -- a half-written chart file is worse than none at all."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _fetch_layer(self, layer_id, bbox, offset):
        """All features for one layer, following pagination. Returns a FeatureCollection dict,
        or None if the service couldn't be reached."""
        features = []
        result_offset = 0
        while True:
            try:
                raw = self._fetch(query_url(layer_id, bbox, offset, result_offset))
                page = json.loads(raw)
            except (urllib.error.URLError, OSError, TimeoutError, ValueError):
                return None
            if page.get("error"):
                return None
            batch = page.get("features") or []
            features.extend(batch)
            if len(batch) < MAX_PAGE or not page.get("exceededTransferLimit"):
                break
            result_offset += len(batch)
            self._sleep(0.2)
        return {"type": "FeatureCollection", "features": features}

    def fetch_area(self, area, bbox, details=("detail", "overview"), progress=None, delay=0.2):
        """Download every chart layer for this area at each detail level and save to disk.

        One request per layer per detail level (plus pagination for dense layers) -- for a lake
        that's a few dozen requests total, once, rather than thousands of tile renders.
        """
        summary = {"area": area, "bbox": list(bbox), "fetched_at": time.time(),
                    "source": IENC_SERVICE, "layers": {}, "failed": []}
        for detail in details:
            offset = DETAIL_OFFSETS[detail]
            for layer_id, spec in CHART_LAYERS.items():
                name = spec["name"]
                collection = self._fetch_layer(layer_id, bbox, offset)
                if collection is None:
                    summary["failed"].append({"layer": name, "detail": detail})
                    if progress:
                        progress(name, detail, None)
                    continue
                count = len(collection["features"])
                if count:
                    self._write(self.layer_path(area, detail, name), json.dumps(collection))
                    entry = summary["layers"].setdefault(name, {"kind": spec["kind"], "counts": {}})
                    entry["counts"][detail] = count
                if progress:
                    progress(name, detail, count)
                self._sleep(delay)
        self._write(self.manifest_path(area), json.dumps(summary, indent=2))
        return summary

    def stats(self, area):
        """(file_count, total_bytes) on disk for one area."""
        root = self.area_dir(area)
        if not root.exists():
            return 0, 0
        files = [f for f in root.rglob("*.geojson")]
        return len(files), sum(f.stat().st_size for f in files)
