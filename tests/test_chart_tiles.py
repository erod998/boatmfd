"""Tests for the local chart-tile disk cache.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import unittest
from pathlib import Path

from app.chart_tiles import WEB_MERCATOR_HALF, ChartTileCache, tile_bounds, tile_export_url


class TestTileBounds(unittest.TestCase):
    def test_zoom_0_covers_the_whole_world(self):
        x0, y0, x1, y1 = tile_bounds(0, 0, 0)
        self.assertAlmostEqual(x0, -WEB_MERCATOR_HALF)
        self.assertAlmostEqual(y0, -WEB_MERCATOR_HALF)
        self.assertAlmostEqual(x1, WEB_MERCATOR_HALF)
        self.assertAlmostEqual(y1, WEB_MERCATOR_HALF)

    def test_zoom_1_quadrants_tile_the_world_with_no_gaps_or_overlap(self):
        quadrants = [tile_bounds(1, x, y) for x in (0, 1) for y in (0, 1)]
        xs = sorted({round(q[0], 3) for q in quadrants} | {round(q[2], 3) for q in quadrants})
        ys = sorted({round(q[1], 3) for q in quadrants} | {round(q[3], 3) for q in quadrants})
        self.assertEqual(xs, [round(-WEB_MERCATOR_HALF, 3), 0.0, round(WEB_MERCATOR_HALF, 3)])
        self.assertEqual(ys, [round(-WEB_MERCATOR_HALF, 3), 0.0, round(WEB_MERCATOR_HALF, 3)])

    def test_a_tile_one_zoom_level_deeper_covering_the_same_corner_is_a_quarter_the_size(self):
        b0 = tile_bounds(5, 10, 10)
        b1 = tile_bounds(6, 20, 20)  # same NW corner, one zoom level deeper
        self.assertAlmostEqual(b0[0], b1[0])
        self.assertAlmostEqual(b0[3], b1[3])
        self.assertAlmostEqual((b0[2] - b0[0]) / 2, b1[2] - b1[0])


class TestTileExportUrl(unittest.TestCase):
    def test_includes_the_computed_bbox_and_fixed_size(self):
        url = tile_export_url(10, 5, 5)
        self.assertIn("bboxSR=102100", url)
        self.assertIn("size=256%2C256", url)
        self.assertIn("format=png32", url)
        self.assertNotIn("layers=", url)

    def test_hidden_layers_are_passed_through(self):
        url = tile_export_url(10, 5, 5, hidden_layers=[5, 11])
        self.assertIn("layers=hide%3A5%2C11", url)


class TestChartTileCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fetched_urls = []
        self.fake_bytes = b"fake-png-bytes"
        self.cache = ChartTileCache(Path(self.tmp.name), fetch=self._fake_fetch)

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_fetch(self, url):
        self.fetched_urls.append(url)
        return self.fake_bytes

    def test_first_request_fetches_and_caches(self):
        data = self.cache.get(12, 100, 200)
        self.assertEqual(data, self.fake_bytes)
        self.assertEqual(len(self.fetched_urls), 1)
        self.assertTrue(self.cache.is_cached(12, 100, 200))

    def test_second_request_is_served_from_disk_not_fetched_again(self):
        self.cache.get(12, 100, 200)
        self.cache.get(12, 100, 200)
        self.assertEqual(len(self.fetched_urls), 1)

    def test_a_fetch_failure_returns_none_and_caches_nothing(self):
        def failing_fetch(url):
            raise OSError("offline")
        cache = ChartTileCache(Path(self.tmp.name), fetch=failing_fetch)
        self.assertIsNone(cache.get(5, 1, 1))
        self.assertFalse(cache.is_cached(5, 1, 1))

    def test_different_tiles_are_cached_separately(self):
        self.cache.get(12, 100, 200)
        self.cache.get(12, 100, 201)
        self.assertTrue(self.cache.is_cached(12, 100, 200))
        self.assertTrue(self.cache.is_cached(12, 100, 201))
        self.assertEqual(len(self.fetched_urls), 2)

    def test_hidden_layers_are_never_written_to_disk(self):
        self.cache.get(12, 100, 200, hidden_layers=[5])
        self.assertFalse(self.cache.is_cached(12, 100, 200))

    def test_hidden_layers_always_fetch_fresh_even_if_the_default_tile_is_cached(self):
        self.cache.get(12, 100, 200)                     # caches the default (nothing hidden) tile
        self.cache.get(12, 100, 200, hidden_layers=[5])   # a different render: must not reuse that cache
        self.assertEqual(len(self.fetched_urls), 2)

    def test_stats_counts_files_and_bytes(self):
        self.cache.get(5, 1, 1)
        self.cache.get(5, 1, 2)
        count, total = self.cache.stats()
        self.assertEqual(count, 2)
        self.assertEqual(total, len(self.fake_bytes) * 2)

    def test_stats_on_a_missing_cache_dir_is_zero_not_an_error(self):
        cache = ChartTileCache(Path(self.tmp.name) / "not-created-yet")
        self.assertEqual(cache.stats(), (0, 0))
