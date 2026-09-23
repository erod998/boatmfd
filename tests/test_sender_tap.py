"""The passive-tap conversion, against simulated gauges whose insides are known exactly.

    python -m unittest discover -s tests -t . -v

Each simulated gauge is a Thevenin source (a share of the supply behind a resistance) feeding a
standard 240-33 ohm fuel sender -- the model app/sender_tap.py assumes -- except where a test
deliberately breaks that assumption to check the fallback.
"""
import unittest

from app import sender_tap as tap

EMPTY, FULL = 240.0, 33.0


def gauge(r_th, k=1.0):
    """The tap ratio V_S/V_I such a gauge produces at a fuel level."""
    return lambda pct: k * (r_s := tap.sender_ohms_at(pct, EMPTY, FULL)) / (r_s + r_th)


class TestDividerModel(unittest.TestCase):
    def test_two_points_recover_the_whole_scale(self):
        # Whatever the gauge's insides, a full-tank point and one at half fix every other level.
        for r_th, k in [(40, 1.0), (100, 0.9), (250, 0.55), (600, 1.05)]:
            g = gauge(r_th, k)
            points = [(g(100), 100.0), (g(50), 50.0)]
            for pct in (0, 5, 10, 25, 75, 90):
                with self.subTest(r_th=r_th, k=k, pct=pct):
                    level, how = tap.fuel_level(g(pct), points, EMPTY, FULL)
                    self.assertEqual(how["method"], "divider")
                    self.assertAlmostEqual(level, pct, delta=0.05)

    def test_accurate_near_empty_without_ever_capturing_empty(self):
        # The low-fuel alarm lives down here, and nobody runs a tank dry to calibrate it.
        g = gauge(120)
        level, _ = tap.fuel_level(g(8), [(g(100), 100.0), (g(60), 60.0)], EMPTY, FULL)
        self.assertAlmostEqual(level, 8, delta=0.05)

    def test_a_straight_line_would_have_been_badly_wrong(self):
        # Why the model exists: interpolating the raw ratio between full and empty.
        g = gauge(100)
        naive = tap.table_value(g(50), [(g(100), 100.0), (g(0), 0.0)])
        self.assertGreater(abs(naive - 50), 10)

    def test_three_points_are_least_squares_and_report_the_residual(self):
        g = gauge(80)
        points = [(g(100) * 1.004, 100.0), (g(50) * 0.996, 50.0), (g(25) * 1.003, 25.0)]   # a little noise
        level, how = tap.fuel_level(g(40), points, EMPTY, FULL)
        self.assertEqual(how["method"], "divider")
        self.assertLess(how["residual_pct"], tap.MODEL_TOLERANCE_PCT)
        self.assertAlmostEqual(level, 40, delta=1.5)

    def test_a_gauge_that_is_not_a_divider_falls_back_to_the_points(self):
        # A regulated gauge, or a tap on the wrong wire: straight in level, not a divider at all.
        weird = lambda pct: 0.2 + 0.5 * (1 - pct / 100) ** 2          # noqa: E731
        points = [(weird(p), float(p)) for p in (100, 75, 50, 25)]
        level, how = tap.fuel_level(weird(75), points, EMPTY, FULL)
        self.assertEqual(how["method"], "table")
        self.assertIsNotNone(how["note"])
        self.assertAlmostEqual(level, 75, delta=0.01)                   # exact at a captured point

    def test_european_senders_run_the_other_way(self):
        # 10 ohm empty, 180 ohm full: the same maths with the ends swapped.
        g = lambda pct: (r := tap.sender_ohms_at(pct, 10.0, 180.0)) / (r + 90)   # noqa: E731
        level, how = tap.fuel_level(g(30), [(g(100), 100.0), (g(50), 50.0)], 10.0, 180.0)
        self.assertEqual(how["method"], "divider")
        self.assertAlmostEqual(level, 30, delta=0.05)


class TestFuelNeedsEnoughPoints(unittest.TestCase):
    def test_one_point_is_no_reading(self):
        g = gauge(100)
        level, how = tap.fuel_level(g(100), [(g(100), 100.0)], EMPTY, FULL)
        self.assertIsNone(level)
        self.assertIn("two points", how["note"])

    def test_points_too_close_together_are_no_reading(self):
        g = gauge(100)
        level, how = tap.fuel_level(g(90), [(g(100), 100.0), (g(95), 95.0)], EMPTY, FULL)
        self.assertIsNone(level)
        self.assertIn("too close", how["note"])


class TestFaultsAreNoDataNotANumber(unittest.TestCase):
    def setUp(self):
        g = gauge(100)
        self.g, self.points = g, [(g(100), 100.0), (g(50), 50.0)]

    def test_a_tap_wire_that_fell_off_is_not_a_full_tank(self):
        for ratio in (0.0, 0.0005, 0.004):
            with self.subTest(ratio=ratio):
                self.assertIsNone(tap.fuel_level(ratio, self.points, EMPTY, FULL)[0])

    def test_an_open_sender_is_not_an_empty_tank(self):
        # Open circuit: the S terminal rises to V_th, at or beyond which no sender resistance fits.
        self.assertIsNone(tap.fuel_level(1.0, self.points, EMPTY, FULL)[0])

    def test_gauges_switched_off_are_no_reading(self):
        self.assertIsNone(tap.tap_ratio(3.1, 0.4))
        self.assertIsNone(tap.tap_ratio(3.1, None))

    def test_a_little_past_the_ends_is_clamped_not_rejected(self):
        # A tank topped right up, or a sender that bottoms slightly past its nominal 33 ohm.
        g = self.g
        self.assertEqual(tap.fuel_level(g(104), self.points, EMPTY, FULL)[0], 100.0)
        self.assertEqual(tap.fuel_level(g(-5), self.points, EMPTY, FULL)[0], 0.0)


class TestRatiometric(unittest.TestCase):
    def test_charging_voltage_does_not_move_the_level(self):
        # Key on at 12.4 V, then running at 14.3 V, same fuel: an air-core gauge's S voltage scales.
        g = gauge(100)
        points = [(g(100), 100.0), (g(50), 50.0)]
        levels = [tap.fuel_level(tap.tap_ratio(g(60) * v, v), points, EMPTY, FULL)[0] for v in (12.4, 13.6, 14.3)]
        self.assertAlmostEqual(max(levels) - min(levels), 0.0, places=6)

    def test_a_regulated_gauge_can_use_the_bare_voltage(self):
        self.assertEqual(tap.tap_ratio(4.2, 9.0, ratiometric=False), 4.2)
        self.assertEqual(tap.tap_ratio(4.2, None, ratiometric=False), 4.2)


class TestTrim(unittest.TestCase):
    def test_down_and_up_give_the_ends(self):
        points = [(0.62, 0.0), (0.18, 100.0)]
        self.assertAlmostEqual(tap.trim_level(0.62, points)[0], 0.0)
        self.assertAlmostEqual(tap.trim_level(0.18, points)[0], 100.0)
        self.assertAlmostEqual(tap.trim_level(0.40, points)[0], 50.0)

    def test_a_middle_point_bends_the_line(self):
        points = [(0.62, 0.0), (0.30, 50.0), (0.18, 100.0)]
        self.assertAlmostEqual(tap.trim_level(0.30, points)[0], 50.0)
        self.assertAlmostEqual(tap.trim_level(0.24, points)[0], 75.0)

    def test_needs_both_ends(self):
        level, how = tap.trim_level(0.4, [(0.62, 0.0)])
        self.assertIsNone(level)
        self.assertIn("DOWN", how["note"])

    def test_far_past_an_end_is_a_fault(self):
        self.assertIsNone(tap.trim_level(0.95, [(0.62, 0.0), (0.18, 100.0)])[0])


class TestOil(unittest.TestCase):
    POINTS = [(0.10, 0.0), (0.35, 40.0)]

    def test_between_and_above_the_points(self):
        self.assertAlmostEqual(tap.oil_pressure(0.225, self.POINTS)[0], 20.0)
        self.assertAlmostEqual(tap.oil_pressure(0.4125, self.POINTS)[0], 50.0)   # at WOT, above the top point

    def test_never_negative(self):
        self.assertEqual(tap.oil_pressure(0.08, self.POINTS)[0], 0.0)

    def test_an_unplugged_tap_is_not_zero_pressure(self):
        # Zero pressure would fire the low-oil alarm on a loose wire.
        self.assertIsNone(tap.oil_pressure(0.0, self.POINTS)[0])
        self.assertIsNone(tap.oil_pressure(0.01, self.POINTS)[0])


class TestPoints(unittest.TestCase):
    def test_capturing_the_same_level_again_replaces_it(self):
        pts = tap.add_point([], 0.30, 100.0, same_within=3)
        pts = tap.add_point(pts, 0.55, 50.0, same_within=3)
        pts = tap.add_point(pts, 0.31, 100.0, same_within=3)   # the next fill-up
        self.assertEqual(sorted(p[1] for p in pts), [50.0, 100.0])
        self.assertIn([0.31, 100.0], pts)

    def test_keeps_a_bounded_number(self):
        pts = []
        for i in range(40):
            pts = tap.add_point(pts, 0.2 + i * 0.01, float(i * 2), same_within=0.5)
        self.assertEqual(len(pts), tap.MAX_POINTS)

    def test_malformed_points_from_a_damaged_file_are_ignored(self):
        self.assertEqual(tap.clean_points([[0.3, 100], "x", [None, 5], [0.5], [float("nan"), 3], [-1, 5], [0.4, 50]]),
                         [(0.3, 100.0), (0.4, 50.0)])


if __name__ == "__main__":
    unittest.main()
