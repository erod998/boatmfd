"""Tests for the simulated AIS targets and the CPA/TCPA math.

    python -m unittest discover -s tests -t . -v
"""
import time
import types
import unittest

from app.ais import SimulatedAIS, _closest_approach

OLD_HICKORY_LAT, OLD_HICKORY_LON = 36.306, -86.563


def fake_target(lat, lon, cog_deg, sog_kn):
    return types.SimpleNamespace(lat=lat, lon=lon, cog_deg=cog_deg, sog_kn=sog_kn)


class TestClosestApproach(unittest.TestCase):
    def test_a_target_closing_head_on_has_a_near_zero_cpa(self):
        # 1 nm due north, heading due south (180) straight at us, at 6 kn.
        target = fake_target(OLD_HICKORY_LAT + 1 / 60.0, OLD_HICKORY_LON, 180, 6.0)
        cpa_nm, tcpa_min = _closest_approach(OLD_HICKORY_LAT, OLD_HICKORY_LON, 0, target)
        self.assertLess(cpa_nm, 0.05)
        self.assertAlmostEqual(tcpa_min, 10.0, delta=0.5)  # 1 nm at 6 kn = 10 minutes

    def test_a_target_moving_away_has_no_future_tcpa(self):
        target = fake_target(OLD_HICKORY_LAT + 1 / 60.0, OLD_HICKORY_LON, 0, 6.0)  # heading further north, away
        cpa_nm, tcpa_min = _closest_approach(OLD_HICKORY_LAT, OLD_HICKORY_LON, 0, target)
        self.assertIsNone(tcpa_min)
        self.assertAlmostEqual(cpa_nm, 1.0, delta=0.02)  # just reports current range

    def test_a_stationary_target_has_no_tcpa(self):
        target = fake_target(OLD_HICKORY_LAT + 1 / 60.0, OLD_HICKORY_LON, 90, 0.0)
        cpa_nm, tcpa_min = _closest_approach(OLD_HICKORY_LAT, OLD_HICKORY_LON, 0, target)
        self.assertIsNone(tcpa_min)
        self.assertAlmostEqual(cpa_nm, 1.0, delta=0.02)

    def test_a_target_passing_well_clear_has_a_large_cpa(self):
        # 1 nm due east, heading due north (crosses our bow far away): won't come close.
        target = fake_target(OLD_HICKORY_LAT, OLD_HICKORY_LON + 1 / 60.0 / 0.8, 0, 6.0)
        cpa_nm, tcpa_min = _closest_approach(OLD_HICKORY_LAT, OLD_HICKORY_LON, 0, target)
        self.assertGreater(cpa_nm, 0.9)


class TestSimulatedAIS(unittest.TestCase):
    def test_creates_the_requested_number_of_targets(self):
        ais = SimulatedAIS(OLD_HICKORY_LAT, OLD_HICKORY_LON, count=3)
        self.assertEqual(len(ais.targets()), 3)

    def test_never_creates_more_targets_than_named_vessels_available(self):
        ais = SimulatedAIS(OLD_HICKORY_LAT, OLD_HICKORY_LON, count=99)
        self.assertLessEqual(len(ais.targets()), 5)

    def test_targets_start_near_the_given_center(self):
        ais = SimulatedAIS(OLD_HICKORY_LAT, OLD_HICKORY_LON, count=4)
        for row in ais.targets(OLD_HICKORY_LAT, OLD_HICKORY_LON, 6.0):
            self.assertLess(row["range_nm"], 2.0)

    def test_without_an_own_position_no_range_bearing_or_cpa_fields_are_added(self):
        ais = SimulatedAIS(OLD_HICKORY_LAT, OLD_HICKORY_LON, count=2)
        row = ais.targets()[0]
        self.assertNotIn("range_nm", row)
        self.assertNotIn("cpa_nm", row)

    def test_with_an_own_position_targets_are_sorted_nearest_first(self):
        ais = SimulatedAIS(OLD_HICKORY_LAT, OLD_HICKORY_LON, count=4)
        rows = ais.targets(OLD_HICKORY_LAT, OLD_HICKORY_LON, 6.0)
        ranges = [r["range_nm"] for r in rows]
        self.assertEqual(ranges, sorted(ranges))

    def test_tick_moves_targets(self):
        ais = SimulatedAIS(OLD_HICKORY_LAT, OLD_HICKORY_LON, count=2)
        before = ais.targets()
        time.sleep(0.05)
        ais.tick()
        after = ais.targets()
        moved = any(
            (b["lat"], b["lon"]) != (a["lat"], a["lon"])
            for b, a in zip(before, after)
        )
        self.assertTrue(moved)

    def test_each_target_has_a_unique_mmsi(self):
        ais = SimulatedAIS(OLD_HICKORY_LAT, OLD_HICKORY_LON, count=4)
        mmsis = [row["mmsi"] for row in ais.targets()]
        self.assertEqual(len(mmsis), len(set(mmsis)))
