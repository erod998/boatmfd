"""Tests for the simplified Quickdraw-style depth recorder.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import unittest
from pathlib import Path

from app.quickdraw import QuickdrawRecorder

LAT, LON = 36.306, -86.563


class TestQuickdrawRecorder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "quickdraw.json"
        self.rec = QuickdrawRecorder(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_empty_and_disabled(self):
        self.assertEqual(self.rec.points(), [])
        self.assertFalse(self.rec.enabled)

    def test_recording_while_disabled_does_nothing(self):
        recorded = self.rec.record(LAT, LON, 15.0)
        self.assertFalse(recorded)
        self.assertEqual(self.rec.points(), [])

    def test_recording_with_no_depth_does_nothing(self):
        self.rec.set_enabled(True)
        recorded = self.rec.record(LAT, LON, None)
        self.assertFalse(recorded)
        self.assertEqual(self.rec.points(), [])

    def test_recording_when_enabled_adds_a_point(self):
        self.rec.set_enabled(True)
        recorded = self.rec.record(LAT, LON, 15.0)
        self.assertTrue(recorded)
        points = self.rec.points()
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["depth_ft"], 15.0)

    def test_points_too_close_together_are_not_both_kept(self):
        self.rec.set_enabled(True)
        self.rec.record(LAT, LON, 15.0)
        recorded_again = self.rec.record(LAT + 0.00001, LON, 15.2)  # a few feet away
        self.assertFalse(recorded_again)
        self.assertEqual(len(self.rec.points()), 1)

    def test_points_far_enough_apart_are_both_kept(self):
        self.rec.set_enabled(True)
        self.rec.record(LAT, LON, 15.0)
        recorded_again = self.rec.record(LAT + 0.02, LON, 12.0)  # well over a mile away
        self.assertTrue(recorded_again)
        self.assertEqual(len(self.rec.points()), 2)

    def test_clear(self):
        self.rec.set_enabled(True)
        self.rec.record(LAT, LON, 15.0)
        count = self.rec.clear()
        self.assertEqual(count, 1)
        self.assertEqual(self.rec.points(), [])

    def test_points_survive_a_restart(self):
        self.rec.set_enabled(True)
        self.rec.record(LAT, LON, 15.0)
        reloaded = QuickdrawRecorder(self.path)
        self.assertEqual(len(reloaded.points()), 1)

    def test_enabled_does_not_survive_a_restart(self):
        self.rec.set_enabled(True)
        reloaded = QuickdrawRecorder(self.path)
        self.assertFalse(reloaded.enabled)

    def test_a_damaged_file_falls_back_to_empty(self):
        self.path.write_text("not json")
        broken = QuickdrawRecorder(self.path)
        self.assertEqual(broken.points(), [])
