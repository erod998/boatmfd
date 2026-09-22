"""Pre-download chart tiles for an area into the local cache (app/chart_tiles.py), so they're
already on disk before heading out -- run this over WiFi at the dock, not expecting it to work on
the water. Ordinary use (just driving the boat around with the dashboard open) also fills the
cache one tile at a time as you go, but only for water you've actually already been near; this is
for topping up a whole lake (or checking whether one even has any chart data, like Center Hill
turned out not to) ahead of time.

    python -m app.seed_tiles old-hickory                       # a named spot, sensible defaults
    python -m app.seed_tiles old-hickory --dry-run              # see the tile count/size first
    python -m app.seed_tiles --center 36.037,-85.797 --radius-nm 12 --zoom 11-17

This hits the Corps of Engineers' own public map server, a shared resource this app has no
special allowance to lean on -- there's no published rate limit or robots.txt to calibrate
against (checked, neither exists), so REQUEST_DELAY_S below is deliberately slow (one request
every few seconds, not several a second) and MAX_TILES_PER_RUN below refuses to fetch more than a
few hundred tiles in a single run without explicitly raising --max-tiles. That means covering a
whole lake takes several separate, deliberate runs (spread over time, ideally not back to back
either), not one long unattended pass -- slower, on purpose. If that's still not conservative
enough, turn REQUEST_DELAY_S up further or ask for the whole bulk-fetch capability to be removed
in favor of only the organic per-tile caching that happens while actually driving the boat.

Zoom 11-15 (the default) is a "regional overview down to individual coves" range that stays a
sane tile count for a 12-15 nm radius. Each level down roughly quadruples the tile count for the
same area, and it compounds fast: for Old Hickory Lake's own 15 nm-radius default, zoom 15 alone
is ~3,400 tiles, 16 is ~13,000, and 17 is over 50,000 by itself. Going past 15 is realistic for a
small sub-area (a marina, a favorite cove) with an explicit --center/--radius-nm, not a whole
lake -- always --dry-run first before going there.
"""
import argparse
import math
import sys
import time

from .chart_tiles import WEB_MERCATOR_HALF, ChartTileCache

PRESETS = {
    # name: (center_lat, center_lon, radius_nm)
    "old-hickory": (36.306, -86.563, 15),
    "cumberland-nashville": (36.161, -86.774, 12),
    "center-hill": (36.037, -85.797, 12),
}


def latlon_to_merc(lat, lon):
    r = 6378137.0
    x = r * math.radians(lon)
    y = r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def tiles_for_area(center_lat, center_lon, radius_nm, zoom):
    """Every (z, x, y) tile touching a bounding box radius_nm out from the center, at one zoom level."""
    dlat = radius_nm / 60.0
    dlon = dlat / math.cos(math.radians(center_lat))
    (x0, y0), (x1, y1) = (
        latlon_to_merc(center_lat - dlat, center_lon - dlon),
        latlon_to_merc(center_lat + dlat, center_lon + dlon),
    )
    tile_m = (2 * WEB_MERCATOR_HALF) / (2 ** zoom)
    tx0, tx1 = sorted(int((v + WEB_MERCATOR_HALF) // tile_m) for v in (x0, x1))
    ty0, ty1 = sorted(int((WEB_MERCATOR_HALF - v) // tile_m) for v in (y0, y1))
    return [(zoom, x, y) for x in range(tx0, tx1 + 1) for y in range(ty0, ty1 + 1)]


def tile_for_point(lat, lon, zoom):
    x, y = latlon_to_merc(lat, lon)
    tile_m = (2 * WEB_MERCATOR_HALF) / (2 ** zoom)
    return zoom, int((x + WEB_MERCATOR_HALF) // tile_m), int((WEB_MERCATOR_HALF - y) // tile_m)


# A real IENC tile with actual chart content runs tens of KB; an empty one (outside the Corps'
# survey coverage -- Center Hill Lake, for one) comes back a near-blank PNG under 5 KB regardless
# of location. Checked empirically against several known-good and known-empty spots, not assumed.
BLANK_TILE_BYTES = 5000

# Deliberately slow: a shared government map server, not a CDN built to take bulk scraping, and
# there's no published rate limit to calibrate against instead of this guess (see the module
# docstring). Raise --max-tiles for a bigger single run; there's no flag to shorten the delay --
# that one's meant to be annoying to change.
REQUEST_DELAY_S = 3.0
MAX_TILES_PER_RUN = 300


def parse_zoom_range(text):
    lo, hi = text.split("-")
    return range(int(lo), int(hi) + 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("preset", nargs="?", choices=sorted(PRESETS), help="a named spot from PRESETS")
    parser.add_argument("--center", help="lat,lon -- overrides the preset's center")
    parser.add_argument("--radius-nm", type=float, help="overrides the preset's radius")
    parser.add_argument("--zoom", default="11-15", help="e.g. 11-15 (default)")
    parser.add_argument("--cache-dir", default="data/tiles")
    parser.add_argument("--max-tiles", type=int, default=MAX_TILES_PER_RUN,
                         help=f"refuse to fetch more than this in one run (default {MAX_TILES_PER_RUN}); "
                              "raise it deliberately, there's no flag to speed up the delay between requests")
    parser.add_argument("--dry-run", action="store_true", help="just report the tile count, fetch nothing")
    args = parser.parse_args(argv)

    if args.preset:
        lat, lon, radius = PRESETS[args.preset]
    elif args.center and args.radius_nm:
        lat, lon = (float(v) for v in args.center.split(","))
        radius = args.radius_nm
    else:
        parser.error("give a preset name, or both --center and --radius-nm")
    # a preset's center or radius can still be overridden individually
    if args.preset and args.center:
        lat, lon = (float(v) for v in args.center.split(","))
    if args.preset and args.radius_nm:
        radius = args.radius_nm

    zooms = parse_zoom_range(args.zoom)
    tiles = [t for z in zooms for t in tiles_for_area(lat, lon, radius, z)]
    print(f"{lat:.4f},{lon:.4f}, {radius:g} nm radius, zoom {args.zoom}: {len(tiles)} tiles")

    cache = ChartTileCache(args.cache_dir)

    # One quick sample fetch at the center, before committing to (possibly) thousands more: this
    # is what would have caught Center Hill Lake having no chart data at all before downloading
    # ~44,000 nearly-blank tiles for nothing.
    sample_zoom = sorted(zooms)[len(list(zooms)) // 2]
    sample = cache.get(*tile_for_point(lat, lon, sample_zoom))
    if sample is None:
        print("Couldn't even fetch a sample tile -- check internet, or the Corps' service may be down.")
        return 1
    if len(sample) < BLANK_TILE_BYTES:
        print(f"Warning: the sample tile at the center came back only {len(sample)} bytes -- that's what an "
              "empty (out of survey coverage) tile looks like, not real chart content. This spot may not "
              "have any USACE IENC data at all (Center Hill Lake doesn't). Worth double-checking before "
              "downloading the rest.")
        if not args.dry_run:
            reply = input("Continue anyway? [y/N] ").strip().lower()
            if reply != "y":
                return 1

    already, _ = cache.stats()
    to_fetch = [t for t in tiles if not cache.is_cached(*t)]
    est_minutes = len(to_fetch) * REQUEST_DELAY_S / 60
    print(f"{len(tiles) - len(to_fetch)} already cached, {len(to_fetch)} to fetch"
          f" (~{len(to_fetch) * 20 / 1024:.1f} MB at a rough 20 KB/tile average, and at least"
          f" {est_minutes:.0f} minutes at one request every {REQUEST_DELAY_S:g}s, deliberately slow --"
          " see the module docstring)")
    if args.dry_run or not to_fetch:
        return 0

    if len(to_fetch) > args.max_tiles:
        print(f"That's more than --max-tiles ({args.max_tiles}) for one run -- fetching the first "
              f"{args.max_tiles} now. Run this again (later, not immediately back to back) to keep going; "
              "it picks up where it left off since already-cached tiles are skipped.")
        to_fetch = to_fetch[:args.max_tiles]

    start = time.monotonic()
    ok = fail = 0
    for i, (z, x, y) in enumerate(to_fetch, 1):
        data = cache.get(z, x, y)
        if data is None:
            fail += 1
        else:
            ok += 1
        if i % 25 == 0 or i == len(to_fetch):
            elapsed = time.monotonic() - start
            print(f"  {i}/{len(to_fetch)} ({ok} ok, {fail} failed) -- {elapsed:.0f}s elapsed", file=sys.stderr)
        if i < len(to_fetch):
            time.sleep(REQUEST_DELAY_S)

    count, total_bytes = cache.stats()
    print(f"Done: {ok} fetched, {fail} failed (probably offline, or no data at that spot -- Center"
          f" Hill Lake has none, for example). Cache now has {count} tiles, {total_bytes / 1024 / 1024:.1f} MB.")
    return 1 if fail and not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
