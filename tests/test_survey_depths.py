"""Tests for the surveyed-depth grid built from the Corps' eHydro surveys.

    python -m unittest discover -s tests -t . -v
"""
import io
import json
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path

from app.survey_depths import (
    DOWNLOAD_DELAY_S,
    TN_STATE_PLANE_FT,
    DepthGrid,
    build_area,
    cells_from_rows,
    list_surveys,
    looks_like_elevations,
    parse_xyz,
    projection_for,
    survey_files,
)

# Tennessee state plane (EPSG:2274, US feet) -> WGS84, computed with pyproj 3 for these tests.
# The first two are corners of the real river-mile-224 survey on Old Hickory.
PYPROJ_REFERENCE = {
    (1798816.24, 700093.47): (-86.575497889, 36.255246264),
    (1802329.26, 704863.28): (-86.563676601, 36.268404449),
    (1968500.0, 0.0): (-86.000000000, 34.333333333),
    (1700000.0, 650000.0): (-86.909048346, 36.115585998),
    (2100000.0, 720000.0): (-85.553695864, 36.310474190),
}
BBOX = (-86.68, 36.20, -85.92, 36.45)


def survey_zip(dense=None, thin=None, name="S"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if dense is not None:
            z.writestr(f"{name}_A.XYZ", "\n".join(f"{x} {y} {e}" for x, y, e in dense))
        if thin is not None:
            z.writestr(f"{name}.XYZ", "\n".join(f"{x} {y} {e}" for x, y, e in thin))
        z.writestr(f"{name}.PDF", "not a chart")
    return buf.getvalue()


class TestProjection(unittest.TestCase):
    def test_matches_pyproj_to_well_under_a_millimetre(self):
        for (x, y), (lon, lat) in PYPROJ_REFERENCE.items():
            got_lon, got_lat = TN_STATE_PLANE_FT.inverse(x, y)
            with self.subTest(x=x, y=y):
                self.assertAlmostEqual(got_lon, lon, places=8)
                self.assertAlmostEqual(got_lat, lat, places=8)

    def test_recognises_the_tennessee_projection_however_the_survey_names_it(self):
        self.assertIs(projection_for("NAD_1983_StatePlane_Tennessee_FIPS_4100_Feet"), TN_STATE_PLANE_FT)
        self.assertIs(projection_for("Tennessee"), TN_STATE_PLANE_FT)
        self.assertIsNone(projection_for("NAD_1983_UTM_Zone_16N"))
        self.assertIsNone(projection_for(None))


class TestSurveyFiles(unittest.TestCase):
    def test_parses_whitespace_or_commas_and_skips_anything_else(self):
        text = "HEADER LINE\n1 2 3\n4,5,6\n\n7 8\nx y z\n9 10 11 extra\n"
        self.assertEqual(parse_xyz(text), [(1, 2, 3), (4, 5, 6), (9, 10, 11)])

    def test_prefers_the_dense_file_and_keeps_the_thinned_one_for_labels(self):
        dense, thin = survey_files(survey_zip(dense=[(1, 1, 400)] * 5, thin=[(2, 2, 401)]))
        self.assertEqual(len(dense), 5)
        self.assertEqual(thin, [(2, 2, 401)])

    def test_falls_back_to_the_thinned_file_when_there_is_no_dense_one(self):
        dense, thin = survey_files(survey_zip(thin=[(2, 2, 401), (3, 3, 402)]))
        self.assertEqual(dense, thin)

    def test_tells_bottom_elevations_from_depths(self):
        self.assertTrue(looks_like_elevations([(0, 0, 420.0), (0, 0, 390.0), (0, 0, 430.0)], 445.0))
        self.assertFalse(looks_like_elevations([(0, 0, 12.0), (0, 0, 30.0), (0, 0, 5.0)], 445.0))
        self.assertFalse(looks_like_elevations([], 445.0))


class TestDepthGrid(unittest.TestCase):
    def grid(self):
        return DepthGrid(-86.6, 36.2, 36.3)

    def at(self, g, i, j):
        """The middle of cell (i, j)."""
        return g.west + (i + 0.5) * g.dlon, g.south + (j + 0.5) * g.dlat

    def test_cells_are_about_25_feet_square(self):
        g = self.grid()
        self.assertAlmostEqual(g.dlat * 111132, 7.62, places=1)
        self.assertAlmostEqual(g.dlon * 111320 * 0.8059, 7.62, places=1)   # cos(36.3 deg)

    def test_keeps_the_shallowest_sounding_in_a_cell(self):
        g = self.grid()
        lon, lat = self.at(g, 3, 4)
        g.add_survey([(lon, lat, 420.0), (lon, lat, 431.5), (lon, lat, 400.0)])
        self.assertEqual(g.cells[(3, 4)], 4315)

    def test_a_newer_survey_replaces_an_older_one_where_both_have_data(self):
        g = self.grid()
        a, b = self.at(g, 1, 1), self.at(g, 2, 1)
        g.add_survey([(*a, 400.0), (*b, 400.0)], survey_id="old")
        g.add_survey([(*a, 390.0)], survey_id="new")   # deeper now -- and still wins, being newer
        self.assertEqual(g.cells[(1, 1)], 3900)
        self.assertEqual(g.cells[(2, 1)], 4000)
        self.assertTrue(g.current(*a, "new"))
        self.assertFalse(g.current(*a, "old"))
        self.assertTrue(g.current(*b, "old"))

    def test_fills_a_hole_between_survey_lines_with_the_shallowest_neighbour(self):
        g = self.grid()
        pts = [(*self.at(g, i, j), 400.0 + i) for i in range(5) for j in range(5) if (i, j) != (2, 2)]
        g.add_survey(pts)
        g.fill_gaps()
        self.assertEqual(g.cells[(2, 2)], 4030)

    def test_does_not_spread_past_a_straight_edge_of_the_survey(self):
        g = self.grid()
        g.add_survey([(*self.at(g, i, j), 400.0) for i in range(10) for j in range(5)])
        before = set(g.cells)
        g.fill_gaps()
        self.assertEqual(set(g.cells), before)

    def test_rows_round_trip_as_runs_of_consecutive_cells(self):
        g = self.grid()
        g.add_survey([(*self.at(g, i, 0), 400.0 + i) for i in (0, 1, 2, 7, 8)])
        rows = g.rows()
        self.assertEqual(rows["0"], [[0, [4000, 4010, 4020]], [7, [4070, 4080]]])
        self.assertEqual(cells_from_rows(json.loads(json.dumps(rows))), g.cells)


def ehydro_page(*surveys):
    return json.dumps({"features": [
        {"attributes": {"surveyjobidpk": name, "surveydatestart": date_ms, "surveytype": "CS",
                        "sourcedatalocation": f"https://files.example/{name}.ZIP", "sourceprojection": proj},
         "geometry": {"rings": [[[-86.58, 36.25], [-86.56, 36.25], [-86.56, 36.27], [-86.58, 36.25]]]}}
        for name, date_ms, proj in surveys]}).encode()


class TestListSurveys(unittest.TestCase):
    def test_asks_for_one_pool_over_the_area_and_sorts_oldest_first(self):
        asked = []
        page = ehydro_page(("NEW", 1_560_000_000_000, "Tennessee"), ("OLD", 1_450_000_000_000, "Tennessee"))
        surveys = list_surveys(BBOX, "CELRN_CR_ND_OLD", fetch=lambda url: (asked.append(url), page)[1])
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(asked[0]).query))
        self.assertEqual(params["where"], "channelareaidfk = 'CELRN_CR_ND_OLD'")
        self.assertEqual(params["outSR"], "4326")
        self.assertEqual([s["name"] for s in surveys], ["OLD", "NEW"])
        self.assertEqual(surveys[0]["date"], "2015-12-13")
        self.assertEqual(surveys[0]["outline"][0][0], [-86.58, 36.25])

    def test_a_service_error_is_raised_not_read_as_no_surveys(self):
        with self.assertRaises(RuntimeError):
            list_surveys(BBOX, "X", fetch=lambda url: b'{"error": {"message": "nope"}}')


class TestBuildArea(unittest.TestCase):
    # Two surveys of the same spot near river mile 224, in state-plane feet, and one in a
    # projection this doesn't handle.
    X, Y = 1800000.0, 702000.0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.downloads, self.pauses = [], []
        old = [(self.X, self.Y, 420.0), (self.X + 200, self.Y, 410.0)]
        new = [(self.X, self.Y, 415.0)]
        self.files = {
            "OLD": survey_zip(dense=old, thin=old),
            "NEW": survey_zip(dense=new, thin=new),
        }
        self.page = ehydro_page(("OLD", 1_450_000_000_000, "Tennessee"), ("NEW", 1_560_000_000_000, "Tennessee"),
                                ("UTM", 1_500_000_000_000, "NAD_1983_UTM_Zone_16N"))

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, url):
        if "FeatureServer" in url:
            return self.page
        self.downloads.append(url)
        return self.files[url.rsplit("/", 1)[1].removesuffix(".ZIP")]

    def build(self):
        return build_area("old-hickory", BBOX, self.root / "cache", self.root / "out.json",
                          fetch=self.fetch, sleep=self.pauses.append, progress=lambda *a: None)

    def test_writes_the_grid_the_surveys_and_what_was_skipped(self):
        summary = self.build()
        out = json.loads((self.root / "out.json").read_text())
        self.assertEqual(summary["surveys"], 2)
        self.assertEqual([s["name"] for s in out["surveys"]], ["OLD", "NEW"])
        self.assertEqual([s["name"] for s in out["skipped"]], ["UTM"])
        self.assertEqual(out["normal_pool_ft"], 445.0)
        self.assertEqual(out["vertical_datum"], "NAVD88")
        self.assertEqual(sorted(cells_from_rows(out["rows"]).values()), [4100, 4150])   # the newer 415 ft replaced 420

    def test_only_the_survey_on_record_keeps_its_printed_soundings(self):
        self.build()
        out = json.loads((self.root / "out.json").read_text())
        self.assertEqual(sorted(e for _, _, e in out["soundings"]), [4100, 4150])

    def test_every_download_is_followed_by_the_polite_pause(self):
        self.build()
        self.assertEqual(len(self.downloads), 2)
        self.assertGreaterEqual(len(self.pauses), 3)   # after the listing, and after each download
        self.assertTrue(all(p >= DOWNLOAD_DELAY_S for p in self.pauses))

    def test_a_second_run_downloads_nothing_it_already_has(self):
        self.build()
        self.downloads.clear()
        self.build()
        self.assertEqual(self.downloads, [])


if __name__ == "__main__":
    unittest.main()
