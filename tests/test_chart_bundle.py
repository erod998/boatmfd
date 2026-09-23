"""The chart bundle endpoint builds its JSON by hand from the layer files, so check it parses.

    python -m unittest discover -s tests -t . -v
"""
import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app.main as main
from app.chart_data import ChartStore


def collection(name):
    return json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"Object_Name": f'{name} "quoted"'},
         "geometry": {"type": "Point", "coordinates": [-86.5, 36.3]}}]})


class TestChartBundle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store = ChartStore(Path(self.tmp.name))
        store._write(store.layer_path("lake", "detail", "light"), collection("light"))
        store._write(store.layer_path("lake", "detail", "rivers"), collection("rivers"))
        store._write(store.manifest_path("lake"), json.dumps({
            "bbox": [-86.7, 36.2, -85.9, 36.45],
            "layers": {"light": {"kind": "light"}, "rivers": {"kind": "water_area"}, "gone": {"kind": "area"}}}))
        patches = [mock.patch.object(main, "chart_store", store), mock.patch.dict(main._bundle_cache, clear=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def get(self, area, detail, encoding="identity"):
        """Calls the endpoint as FastAPI would, with a stand-in for the request's headers."""
        request = mock.Mock(headers={"accept-encoding": encoding})
        return main.chart_bundle(area, detail, request)

    def body(self, res):
        raw = gzip.decompress(res.body) if res.headers.get("content-encoding") == "gzip" else res.body
        return json.loads(raw)

    def tearDown(self):
        self.tmp.cleanup()

    def test_is_valid_json_with_every_layer_that_has_a_file(self):
        body = self.body(self.get("lake", "detail"))
        self.assertEqual(body["area"], "lake")
        self.assertEqual(body["bbox"], [-86.7, 36.2, -85.9, 36.45])
        self.assertEqual(set(body["layers"]), {"light", "rivers"})
        self.assertEqual(body["layers"]["rivers"]["kind"], "water_area")
        self.assertEqual(body["layers"]["light"]["geojson"]["features"][0]["properties"]["Object_Name"], 'light "quoted"')

    def test_is_served_compressed_to_a_client_that_accepts_it(self):
        res = self.get("lake", "detail", "gzip, deflate")
        self.assertEqual(res.headers.get("content-encoding"), "gzip")
        self.assertEqual(set(self.body(res)["layers"]), {"light", "rivers"})
        plain = self.get("lake", "detail")
        self.assertIsNone(plain.headers.get("content-encoding"))
        self.assertEqual(json.loads(plain.body)["area"], "lake")

    def test_a_refetched_chart_is_served_fresh_not_from_the_cache(self):
        self.body(self.get("lake", "detail"))
        store = main.chart_store
        store._write(store.layer_path("lake", "detail", "light"), collection("relit"))
        manifest = store.manifest_path("lake")
        os.utime(manifest, (manifest.stat().st_atime, manifest.stat().st_mtime + 10))
        props = self.body(self.get("lake", "detail"))["layers"]["light"]["geojson"]["features"][0]["properties"]
        self.assertEqual(props["Object_Name"], 'relit "quoted"')

    def test_unknown_areas_and_levels_are_not_found(self):
        self.assertEqual(self.get("nowhere", "detail").status_code, 404)
        self.assertEqual(self.get("lake", "zoomed").status_code, 404)


if __name__ == "__main__":
    unittest.main()
