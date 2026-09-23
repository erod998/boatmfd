"""Tests for following a Go To's planned path (Auto Guidance, the steering half).

    python -m unittest discover -s tests -t . -v
"""
import unittest

from app.guidance import TURN_RADIUS_NM, GuidedPath, along_fraction
from app.nav import haversine_distance_nm, waypoint_nav

LAT, LON = 36.30, -86.56
# An L-shaped channel: 1 nm north, then 1 nm east. (1 minute of latitude is 1 nm.)
CORNER = (LAT + 1 / 60, LON)
END = (LAT + 1 / 60, LON + 1 / 60 / 0.8059)   # 1 nm east at this latitude
L_PATH = [(LAT, LON), CORNER, END]


class TestAlongFraction(unittest.TestCase):
    def test_zero_at_the_start_one_at_the_end_half_in_the_middle(self):
        a, b = (LAT, LON), CORNER
        self.assertAlmostEqual(along_fraction(*a, a, b), 0.0)
        self.assertAlmostEqual(along_fraction(*b, a, b), 1.0)
        self.assertAlmostEqual(along_fraction(LAT + 0.5 / 60, LON, a, b), 0.5)

    def test_measures_abeam_not_distance(self):
        # Well off to the side of the leg's midpoint is still halfway along it.
        self.assertAlmostEqual(along_fraction(LAT + 0.5 / 60, LON + 0.01, (LAT, LON), CORNER), 0.5, places=3)


class TestGuidedPath(unittest.TestCase):
    def test_a_two_point_path_is_exactly_the_straight_go_to(self):
        g = GuidedPath([(LAT, LON), CORNER])
        here = (LAT + 0.2 / 60, LON + 0.001)
        nav = g.tick(*here, 10.0, 5.0)
        plain = waypoint_nav(*here, 10.0, *CORNER, LAT, LON, 5.0)
        for key in ("bearing_deg", "course_deg", "distance_nm", "xte_nm", "turn_deg"):
            self.assertEqual(nav[key], plain[key], key)

    def test_distance_to_go_is_along_the_path_and_steering_is_to_the_next_turn(self):
        g = GuidedPath(L_PATH)
        nav = g.tick(LAT, LON, 10.0, 0.0)
        self.assertAlmostEqual(nav["distance_nm"], 2.0, places=2)      # 1 north + 1 east, not the 1.41 diagonal
        self.assertAlmostEqual(nav["turn_distance_nm"], 1.0, places=2)
        self.assertAlmostEqual(nav["bearing_deg"], 0.0, delta=0.5)     # up the first leg
        self.assertAlmostEqual(nav["course_deg"], 0.0, delta=0.5)
        self.assertEqual((nav["leg"], nav["legs"]), (1, 2))

    def test_time_to_go_is_the_path_distance_at_the_speed_over_the_ground(self):
        nav = GuidedPath(L_PATH).tick(LAT, LON, 10.0, 0.0)
        self.assertAlmostEqual(nav["ete_s"], 2.0 / 10.0 * 3600, delta=30)
        self.assertIsNone(GuidedPath(L_PATH).tick(LAT, LON, 0.0, 0.0)["ete_s"])

    def test_reaching_the_turn_takes_the_next_leg(self):
        g = GuidedPath(L_PATH)
        just_short = (CORNER[0] - TURN_RADIUS_NM / 2 / 60, CORNER[1])
        nav = g.tick(*just_short, 10.0, 0.0)
        self.assertEqual(nav["leg"], 2)
        self.assertAlmostEqual(nav["bearing_deg"], 90.0, delta=2)      # now steering east
        self.assertAlmostEqual(nav["course_deg"], 90.0, delta=0.5)

    def test_cutting_the_corner_still_takes_the_next_leg(self):
        # Past the corner's latitude, well east of it and outside the arrival circle: abeam of
        # the turn, which is behind now. Steering back to it would be wrong.
        g = GuidedPath(L_PATH)
        cut = (CORNER[0] + 0.05 / 60, CORNER[1] + 0.3 / 60 / 0.8059)
        self.assertGreater(haversine_distance_nm(*cut, *CORNER), TURN_RADIUS_NM)
        nav = g.tick(*cut, 10.0, 60.0)
        self.assertEqual(nav["leg"], 2)
        self.assertAlmostEqual(nav["distance_nm"], haversine_distance_nm(*cut, *END), places=3)

    def test_never_advances_past_the_destination(self):
        g = GuidedPath(L_PATH)
        nav = g.tick(END[0] + 0.1 / 60, END[1] + 0.2, 10.0, 90.0)   # beyond the end altogether
        self.assertEqual(nav["leg"], 2)
        self.assertGreater(nav["distance_nm"], 0)

    def test_takes_several_legs_at_once_if_the_boat_has_passed_them_all(self):
        path = [(LAT + i / 600, LON) for i in range(10)]   # ten short legs due north
        g = GuidedPath(path)
        nav = g.tick(LAT + 7.5 / 600, LON, 10.0, 0.0)
        self.assertEqual(nav["leg"], 8)

    def test_a_path_needs_two_points(self):
        with self.assertRaises(ValueError):
            GuidedPath([(LAT, LON)])


if __name__ == "__main__":
    unittest.main()
