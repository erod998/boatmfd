"""Tests for the RPM-linked stereo volume boost.

    python -m unittest discover -s tests -t . -v
"""
import json
import tempfile
import unittest
from pathlib import Path

from app.volume_boost import VolumeBoost


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeMedia:
    def __init__(self):
        self.calls = []

    def handle(self, action, value=None, zone=1):
        self.calls.append((action, value, zone))


def zones(*volumes, limit=24, muted=None):
    muted = muted or set()
    return [{"id": i + 1, "volume": v, "limit": limit, "muted": (i + 1) in muted} for i, v in enumerate(volumes)]


class TestDefaultsAndConfig(unittest.TestCase):
    def test_defaults_and_persisted_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "volume_boost.json"
            vb = VolumeBoost(path, FakeMedia(), redline_rpm=4800)
            self.assertEqual(vb.config(), {"enabled": False, "boost_pct": 25.0, "smoothing_pct": 50.0})
            vb.set_config(enabled=True, boost_pct=40, smoothing_pct=0)
            self.assertTrue(path.exists())
            vb2 = VolumeBoost(path, FakeMedia(), redline_rpm=4800)
            self.assertEqual(vb2.config(), {"enabled": True, "boost_pct": 40.0, "smoothing_pct": 0.0})

    def test_rejects_out_of_range_settings(self):
        vb = VolumeBoost(None, FakeMedia(), redline_rpm=4800)
        with self.assertRaises(ValueError):
            vb.set_config(boost_pct=201)
        with self.assertRaises(ValueError):
            vb.set_config(smoothing_pct=101)

    def test_a_damaged_settings_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "volume_boost.json"
            path.write_text("not json")
            vb = VolumeBoost(path, FakeMedia(), redline_rpm=4800)
            self.assertEqual(vb.config()["boost_pct"], 25.0)


class TestBoosting(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.media = FakeMedia()

    def test_disabled_never_touches_volume(self):
        vb = VolumeBoost(None, self.media, redline_rpm=4000, clock=self.clock)
        vb.tick(4000, zones(10))
        self.assertEqual(self.media.calls, [])

    def test_full_rpm_with_no_smoothing_applies_the_full_boost_at_once(self):
        vb = VolumeBoost(None, self.media, redline_rpm=4000, clock=self.clock)
        vb.set_config(enabled=True, boost_pct=40, smoothing_pct=0)
        vb.tick(4000, zones(10))  # at redline: 10 * 1.40 = 14
        self.assertIn(("volume", 14, 1), self.media.calls)

    def test_half_rpm_applies_half_the_boost(self):
        vb = VolumeBoost(None, self.media, redline_rpm=4000, clock=self.clock)
        vb.set_config(enabled=True, boost_pct=40, smoothing_pct=0)
        vb.tick(2000, zones(10))  # half redline: 10 * 1.20 = 12
        self.assertIn(("volume", 12, 1), self.media.calls)

    def test_boost_is_capped_at_the_zone_volume_limit(self):
        vb = VolumeBoost(None, self.media, redline_rpm=4000, clock=self.clock)
        vb.set_config(enabled=True, boost_pct=100, smoothing_pct=0)
        vb.tick(4000, zones(20, limit=24))  # 20 * 2.0 = 40, capped to 24
        self.assertIn(("volume", 24, 1), self.media.calls)

    def test_muted_zones_are_left_alone(self):
        vb = VolumeBoost(None, self.media, redline_rpm=4000, clock=self.clock)
        vb.set_config(enabled=True, boost_pct=40, smoothing_pct=0)
        vb.tick(4000, zones(0, muted={1}))
        self.assertEqual(self.media.calls, [])

    def test_manual_volume_change_becomes_the_new_baseline(self):
        vb = VolumeBoost(None, self.media, redline_rpm=4000, clock=self.clock)
        vb.set_config(enabled=True, boost_pct=50, smoothing_pct=0)
        vb.tick(4000, zones(10))  # baseline 10 -> boosted to 15
        self.media.calls.clear()
        vb.note_manual_volume(1, 8)  # driver turns it back down to 8 while still at full RPM
        vb.tick(4000, zones(15))  # the stereo now reports 15 (the last command); boost from the new baseline of 8
        self.assertIn(("volume", 12, 1), self.media.calls)  # 8 * 1.5 = 12

    def test_smoothing_ramps_toward_the_target_instead_of_jumping(self):
        vb = VolumeBoost(None, self.media, redline_rpm=4000, clock=self.clock)
        vb.set_config(enabled=True, boost_pct=100, smoothing_pct=100)  # ~4s time constant
        self.clock.advance(1.0)
        vb.tick(4000, zones(10))
        first_calls = [c for c in self.media.calls if c[0] == "volume"]
        self.assertTrue(first_calls)
        self.assertLess(first_calls[-1][1], 20)  # nowhere near the full 10*2.0=20 target on the first tick
        self.clock.advance(30)  # long enough for the ease to fully settle
        self.media.calls.clear()
        vb.tick(4000, zones(first_calls[-1][1]))
        self.assertIn(("volume", 20, 1), self.media.calls)

    def test_turning_rpm_back_down_eases_the_boost_back_off(self):
        vb = VolumeBoost(None, self.media, redline_rpm=4000, clock=self.clock)
        vb.set_config(enabled=True, boost_pct=50, smoothing_pct=0)
        vb.tick(4000, zones(10))  # boosted to 15
        self.media.calls.clear()
        vb.tick(0, zones(15))  # engine back to idle: boost should fall away
        self.assertIn(("volume", 10, 1), self.media.calls)

    def test_multiple_zones_are_boosted_independently(self):
        vb = VolumeBoost(None, self.media, redline_rpm=4000, clock=self.clock)
        vb.set_config(enabled=True, boost_pct=50, smoothing_pct=0)
        vb.tick(4000, zones(10, 4))
        self.assertIn(("volume", 15, 1), self.media.calls)
        self.assertIn(("volume", 6, 2), self.media.calls)
