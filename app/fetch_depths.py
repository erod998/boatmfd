"""Download the Corps of Engineers' depth surveys for an area and build its depth grid, once.

Run it over WiFi at the dock, after fetch_charts. The result is one file next to the chart,
data/charts/<area>/survey_depth.json, which the chart shades by real depth at the lake level set
under Map Settings; the survey downloads themselves are cached in data/survey-cache/ so a re-run
only fetches surveys published since.

    python -m app.fetch_depths old-hickory
    python -m app.fetch_depths old-hickory --dry-run     # list the surveys, download nothing

See app/survey_depths.py for what the surveys are and what they cover.
"""
import argparse
import sys

from .fetch_charts import AREAS
from .survey_depths import DEPTH_AREAS, DOWNLOAD_DELAY_S, build_area, list_surveys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("area", choices=sorted(DEPTH_AREAS))
    parser.add_argument("--charts-dir", default="data/charts")
    parser.add_argument("--cache-dir", default="data/survey-cache")
    parser.add_argument("--dry-run", action="store_true", help="list the surveys, download nothing")
    args = parser.parse_args(argv)

    bbox = AREAS[args.area]
    if args.dry_run:
        surveys = list_surveys(bbox, DEPTH_AREAS[args.area]["channel_area"])
        for s in surveys:
            print(f"  {s['date']}  {s['type'] or '':3}  {s['name']}")
        print(f"{len(surveys)} surveys; downloading them takes about "
              f"{len(surveys) * DOWNLOAD_DELAY_S / 60:.0f} minutes at one every {DOWNLOAD_DELAY_S:.0f} s")
        return 0

    out = f"{args.charts_dir}/{args.area}/survey_depth.json"
    summary = build_area(args.area, bbox, args.cache_dir, out)
    print(f"{summary['surveys']} surveys, {summary['cells']} depth cells ({summary['filled']} filled "
          f"between survey lines), {summary['soundings']} soundings -> {out}")
    for s in summary["skipped"]:
        print(f"  skipped {s['name']}: {s['why']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
