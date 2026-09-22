"""Local disk cache for USACE IENC chart tiles.

Until now the browser hit ienccloud.us directly for every tile, every time (see
ArcGisExportLayer in static/js/app.js) -- a live dynamic render on the Corps of
Engineers' own server, not a cached tile, so every pan/zoom/rotate needed a
round trip over the internet. That's slow even with a connection, and gives
nothing at all without one -- which is the normal case underway; the boat has
WiFi at the dock, not on the water.

This module makes the Pi's own backend the thing the browser talks to instead.
A tile is fetched from the upstream export service once, written to
data/tiles/<z>/<x>/<y>.png, and served straight from disk on every later
request for that same tile -- fast, and available with no internet at all once
it's been fetched once. IENC data changes rarely enough for a hobby boat that
"cached until someone clears the directory" is a reasonable tradeoff; this is
not a chart current enough to trust blindly for real commercial navigation
regardless of caching.

The bbox/params math in tile_export_url() mirrors ArcGisExportLayer.getTileUrl()
in static/js/app.js exactly (same tile numbering, same Web Mercator projection)
-- if one changes, the other has to change with it, or the two won't agree on
what a given z/x/y actually covers.
"""
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

IENC_EXPORT = "https://ienccloud.us/arcgis/rest/services/IENC/USACE_IENC_Master_Service/MapServer/export"
WEB_MERCATOR_HALF = 20037508.342789244
TILE_PX = 256  # one fixed resolution cached on disk, not a retina/non-retina pair -- see the
# module docstring in seed_tiles.py for why: keeps the cache one file per tile, not two.


def tile_bounds(z, x, y):
    """The Web Mercator (EPSG:3857) bbox a given tile covers: (xmin, ymin, xmax, ymax)."""
    tile_m = (2 * WEB_MERCATOR_HALF) / (2 ** z)
    x0 = -WEB_MERCATOR_HALF + x * tile_m
    y1 = WEB_MERCATOR_HALF - y * tile_m
    return x0, y1 - tile_m, x0 + tile_m, y1


def tile_export_url(z, x, y, hidden_layers=()):
    x0, y0, x1, y1 = tile_bounds(z, x, y)
    params = {
        "bbox": f"{x0},{y0},{x1},{y1}",
        "bboxSR": 102100, "imageSR": 102100,
        "size": f"{TILE_PX},{TILE_PX}", "dpi": 96,
        "format": "png32", "transparent": "true", "f": "image",
    }
    if hidden_layers:
        params["layers"] = "hide:" + ",".join(str(i) for i in hidden_layers)
    return IENC_EXPORT + "?" + urllib.parse.urlencode(params)


class ChartTileCache:
    """z/x/y.png files under `cache_dir`. Tiles requested with hidden_layers (a per-browser
    display preference -- Options -> Map layers & colors -- not something worth multiplying the
    cache by) are fetched and returned but never written to disk, only the default (everything
    shown) tile is ever cached."""

    def __init__(self, cache_dir, fetch=None):
        self.cache_dir = Path(cache_dir)
        self._fetch = fetch or self._fetch_upstream  # swappable for tests

    @staticmethod
    def _fetch_upstream(url):
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()

    def path_for(self, z, x, y):
        return self.cache_dir / str(z) / str(x) / f"{y}.png"

    def get(self, z, x, y, hidden_layers=()):
        """PNG bytes for this tile, or None if it isn't cached and can't be fetched right now
        (no internet, upstream error, timeout -- any reason, all treated the same: no tile)."""
        path = self.path_for(z, x, y)
        if not hidden_layers and path.exists():
            return path.read_bytes()
        try:
            data = self._fetch(tile_export_url(z, x, y, hidden_layers))
        except (urllib.error.URLError, OSError, TimeoutError):
            return None
        if not hidden_layers:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return data

    def is_cached(self, z, x, y):
        return self.path_for(z, x, y).exists()

    def stats(self):
        """(tile_count, total_bytes) currently on disk."""
        if not self.cache_dir.exists():
            return 0, 0
        files = list(self.cache_dir.rglob("*.png"))
        return len(files), sum(f.stat().st_size for f in files)
