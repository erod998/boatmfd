"""Download the vector charts for an area onto this machine, once.

Run it over WiFi at the dock; after that the charts are on disk and the boat needs no internet
at all. This replaces the old tile-scraping approach entirely: that needed thousands of throttled
requests and hours to cover one lake in fixed-zoom pictures, this needs a few dozen requests and
seconds to get the actual chart data -- which then draws crisply at any zoom, restyles for night
instantly, and can be tapped to identify a buoy.

    python -m app.fetch_charts old-hickory              # a named area
    python -m app.fetch_charts old-hickory --dry-run     # what it would fetch, fetches nothing
    python -m app.fetch_charts --name my-lake --bbox -86.62,36.24,-86.44,36.36

Charts are updated by USACE bi-monthly, so re-running this occasionally at the dock is worth it;
it simply overwrites what's there.

Coverage note: this is USACE Inland ENC data, which charts the commercially-navigable federal
waterway system -- the Cumberland (including Old Hickory Lake), Tennessee, Ohio, Mississippi and
similar. Lakes off that system may have nothing at all; Center Hill Lake, checked directly, has
no USACE chart data in any product. --dry-run reports the feature count before committing, so an
area with no coverage is obvious immediately.
"""
import argparse
import sys
import time

from .chart_data import CHART_LAYERS, ChartStore

AREAS = {
    # name: (west, south, east, north)
    "old-hickory": (-86.62, 36.24, -86.44, 36.36),
    "cumberland-nashville": (-86.90, 36.08, -86.62, 36.26),
    "center-hill": (-85.95, 35.95, -85.60, 36.15),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("area", nargs="?", choices=sorted(AREAS), help="a named area")
    parser.add_argument("--name", help="name for a custom area (with --bbox)")
    parser.add_argument("--bbox", help="west,south,east,north in degrees")
    parser.add_argument("--charts-dir", default="data/charts")
    parser.add_argument("--detail-only", action="store_true", help="skip the zoomed-out overview level")
    parser.add_argument("--dry-run", action="store_true", help="report what's there, fetch nothing")
    args = parser.parse_args(argv)

    if args.area:
        name, bbox = args.area, AREAS[args.area]
    elif args.name and args.bbox:
        name = args.name
        bbox = tuple(float(v) for v in args.bbox.split(","))
        if len(bbox) != 4:
            parser.error("--bbox needs west,south,east,north")
    else:
        parser.error("give a named area, or both --name and --bbox")

    store = ChartStore(args.charts_dir)
    details = ("detail",) if args.detail_only else ("detail", "overview")
    print(f"{name}: {bbox[0]},{bbox[1]} to {bbox[2]},{bbox[3]}")
    print(f"{len(CHART_LAYERS)} chart layers x {len(details)} detail level(s) "
          f"= about {len(CHART_LAYERS) * len(details)} requests")

    if args.dry_run:
        # One real query tells us whether this area has any chart coverage at all.
        found = store._fetch_layer(33, bbox, None)  # COASTLINE_LINE
        if found is None:
            print("Couldn't reach the chart service -- check the connection.")
            return 1
        n = len(found["features"])
        print(f"Coverage check: {n} shoreline features in this box.")
        if n == 0:
            print("No USACE chart coverage here. Center Hill Lake is like this -- USACE has no "
                  "chart for it in any product, so there is nothing to download.")
        return 0

    start = time.monotonic()

    def progress(layer, detail, count):
        if count is None:
            print(f"  {detail:9} {layer:32} FAILED", file=sys.stderr)
        elif count:
            print(f"  {detail:9} {layer:32} {count}", file=sys.stderr)

    summary = store.fetch_area(name, bbox, details=details, progress=progress)
    files, total_bytes = store.stats(name)
    total_features = sum(sum(e["counts"].values()) for e in summary["layers"].values())
    print(f"Done in {time.monotonic() - start:.0f}s: {len(summary['layers'])} layers, "
          f"{total_features} features, {files} files, {total_bytes / 1024 / 1024:.1f} MB on disk.")
    if summary["failed"]:
        print(f"{len(summary['failed'])} layer/detail fetches failed -- re-run to fill them in.")
    if not summary["layers"]:
        print("Nothing found here at all -- this area has no USACE chart coverage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
