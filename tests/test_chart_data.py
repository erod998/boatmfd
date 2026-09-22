"""Tests for the local vector chart store.

    python -m unittest discover -s tests -t . -v
"""
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from app.chart_data import CHART_LAYERS, DETAIL_OFFSETS, MAX_PAGE, ChartStore, query_url

BBOX = (-86.62, 36.24, -86.44, 36.36)  # Old Hickory Lake


def params_of(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


def collection(n, start=0):
    return json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"i": start + i},
                      "geometry": {"type": "Point", "coordinates": [-86.5, 36.3]}} for i in range(n)],
    })


class TestQueryUrl(unittest.TestCase):
    def test_asks_for_geojson_over_the_bbox_in_wgs84(self):
        p = params_of(query_url(96, BBOX))
        self.assertEqual(p["f"], "geojson")
        self.assertEqual(p["inSR"], "4326")
        self.assertEqual(p["outSR"], "4326")
        self.assertEqual(json.loads(p["geometry"])["xmin"], BBOX[0])
        self.assertEqual(json.loads(p["geometry"])["ymax"], BBOX[3])

    def test_generalisation_is_sent_only_when_asked_for(self):
        self.assertNotIn("maxAllowableOffset", params_of(query_url(96, BBOX)))
        self.assertEqual(params_of(query_url(96, BBOX, 0.0001))["maxAllowableOffset"], "0.0001")

    def test_paging_offset_is_passed_through(self):
        self.assertEqual(params_of(query_url(96, BBOX, None, 2000))["resultOffset"], "2000")

    def test_targets_the_requested_layer(self):
        self.assertIn("/96/query", query_url(96, BBOX))
        self.assertIn("/33/query", query_url(33, BBOX))


class TestChartLayers(unittest.TestCase):
    def test_the_land_clutter_layers_are_deliberately_excluded(self):
        # BUILDING_SINGLE_AREA (58) and ROADWAY_LINE (48) are 6,295 of the lake's 6,756 features
        self.assertNotIn(58, CHART_LAYERS)
        self.assertNotIn(48, CHART_LAYERS)

    def test_the_layers_a_chart_is_useless_without_are_present(self):
        names = {spec["name"] for spec in CHART_LAYERS.values()}
        for essential in ("coastline", "depth_area", "depth_contour", "lateral_buoy", "light"):
            self.assertIn(essential, names)

    def test_every_layer_has_a_unique_name_and_a_style_kind(self):
        names = [spec["name"] for spec in CHART_LAYERS.values()]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(spec.get("kind") for spec in CHART_LAYERS.values()))


class TestFetchArea(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.requested = []

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, fetch):
        return ChartStore(Path(self.tmp.name), fetch=fetch, sleep=lambda s: None)

    def test_writes_a_file_per_layer_per_detail_level(self):
        store = self.store(lambda url: (self.requested.append(url), collection(3))[1])
        store.fetch_area("lake", BBOX, details=("detail",))
        for spec in CHART_LAYERS.values():
            self.assertIsNotNone(store.read_layer("lake", "detail", spec["name"]), spec["name"])

    def test_fetches_each_detail_level_at_its_own_generalisation(self):
        store = self.store(lambda url: (self.requested.append(url), collection(1))[1])
        store.fetch_area("lake", BBOX, details=("detail", "overview"))
        offsets = {params_of(u).get("maxAllowableOffset") for u in self.requested}
        self.assertEqual(offsets, {str(DETAIL_OFFSETS["detail"]), str(DETAIL_OFFSETS["overview"])})

    def test_empty_layers_are_not_written_to_disk(self):
        store = self.store(lambda url: collection(0))
        store.fetch_area("lake", BBOX, details=("detail",))
        self.assertIsNone(store.read_layer("lake", "detail", "coastline"))
        self.assertEqual(store.stats("lake")[0], 0)

    def test_manifest_records_counts_and_the_source(self):
        store = self.store(lambda url: collection(2))
        summary = store.fetch_area("lake", BBOX, details=("detail",))
        saved = store.manifest("lake")
        self.assertEqual(saved["layers"]["coastline"]["counts"]["detail"], 2)
        self.assertEqual(saved["bbox"], list(BBOX))
        self.assertIn("ienccloud.us", saved["source"])
        self.assertEqual(summary["failed"], [])

    def test_a_layer_that_fails_is_recorded_and_does_not_stop_the_rest(self):
        def flaky(url):
            if "/33/query" in url:
                raise OSError("offline")
            return collection(1)
        store = self.store(flaky)
        summary = store.fetch_area("lake", BBOX, details=("detail",))
        self.assertEqual([f["layer"] for f in summary["failed"]], ["coastline"])
        self.assertIsNone(store.read_layer("lake", "detail", "coastline"))
        self.assertIsNotNone(store.read_layer("lake", "detail", "depth_area"))

    def test_a_service_error_response_counts_as_a_failure_not_as_data(self):
        store = self.store(lambda url: json.dumps({"error": {"code": 400, "message": "bad"}}))
        summary = store.fetch_area("lake", BBOX, details=("detail",))
        self.assertEqual(len(summary["failed"]), len(CHART_LAYERS))
        self.assertEqual(store.stats("lake")[0], 0)

    def test_a_dense_layer_is_paged_until_everything_is_collected(self):
        def paged(url):
            offset = int(params_of(url).get("resultOffset", 0))
            if "/96/query" not in url:
                return collection(1)
            if offset == 0:
                return json.dumps({**json.loads(collection(MAX_PAGE)), "exceededTransferLimit": True})
            return collection(5, start=MAX_PAGE)
        store = self.store(paged)
        store.fetch_area("lake", BBOX, details=("detail",))
        saved = json.loads(store.read_layer("lake", "detail", "depth_area"))
        self.assertEqual(len(saved["features"]), MAX_PAGE + 5)

    def test_refetching_replaces_the_previous_copy(self):
        store = self.store(lambda url: collection(2))
        store.fetch_area("lake", BBOX, details=("detail",))
        store = self.store(lambda url: collection(7))
        store.fetch_area("lake", BBOX, details=("detail",))
        saved = json.loads(store.read_layer("lake", "detail", "coastline"))
        self.assertEqual(len(saved["features"]), 7)

    def test_stats_counts_files_and_bytes(self):
        store = self.store(lambda url: collection(1))
        store.fetch_area("lake", BBOX, details=("detail",))
        count, total = store.stats("lake")
        self.assertEqual(count, len(CHART_LAYERS))
        self.assertGreater(total, 0)

    def test_manifest_is_none_before_anything_is_fetched(self):
        self.assertIsNone(self.store(lambda url: "").manifest("never-fetched"))
