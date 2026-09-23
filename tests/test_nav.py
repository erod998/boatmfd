"""Tests for the great-circle navigation math.

    python -m unittest discover -s tests -t . -v

These are the numbers the nav data fields show and the off-course alarm fires on, so the
cases here are mostly about the ones that are easy to get subtly wrong: which way a signed
value points, and what happens when the boat is not actually going anywhere.
"""
import math
import unittest

from app.nav import (
    cross_track_error_nm,
    haversine_distance_nm,
    initial_bearing_deg,
    relative_bearing,
    signed_angle_diff,
    velocity_made_good_kn,
    waypoint_nav,
)

# Old Hickory Lake. A tenth of a degree of latitude is 6 nm, due north.
LAT, LON = 36.306, -86.563
NORTH_6NM = (LAT + 0.1, LON)


class TestDistanceAndBearing(unittest.TestCase):
    def test_a_tenth_of_a_degree_of_latitude_is_six_nautical_miles(self):
        self.assertAlmostEqual(haversine_distance_nm(LAT, LON, *NORTH_6NM), 6.0, places=1)

    def test_zero_distance_to_itself(self):
        self.assertEqual(haversine_distance_nm(LAT, LON, LAT, LON), 0.0)

    def test_cardinal_bearings(self):
        for dlat, dlon, expected in [(0.1, 0, 0), (-0.1, 0, 180), (0, 0.1, 90), (0, -0.1, 270)]:
            with self.subTest(expected=expected):
                self.assertAlmostEqual(
                    initial_bearing_deg(LAT, LON, LAT + dlat, LON + dlon), expected, places=0)

    def test_bearing_is_always_a_positive_compass_angle(self):
        for dlat, dlon in [(0.1, 0.1), (-0.1, 0.1), (0.1, -0.1), (-0.1, -0.1)]:
            with self.subTest(dlat=dlat, dlon=dlon):
                b = initial_bearing_deg(LAT, LON, LAT + dlat, LON + dlon)
                self.assertTrue(0 <= b < 360, b)


class TestSignedAngleDiff(unittest.TestCase):
    def test_turning_right_is_positive_and_left_is_negative(self):
        self.assertAlmostEqual(signed_angle_diff(0, 90), 90)
        self.assertAlmostEqual(signed_angle_diff(0, 270), -90)

    def test_takes_the_short_way_round_north(self):
        self.assertAlmostEqual(signed_angle_diff(350, 10), 20)    # not -340
        self.assertAlmostEqual(signed_angle_diff(10, 350), -20)   # not 340

    def test_stays_within_half_a_turn(self):
        for a in range(0, 360, 17):
            for b in range(0, 360, 23):
                with self.subTest(a=a, b=b):
                    self.assertTrue(-180 < signed_angle_diff(a, b) <= 180)


class TestVelocityMadeGood(unittest.TestCase):
    def test_straight_at_the_mark_makes_good_the_whole_speed(self):
        self.assertAlmostEqual(velocity_made_good_kn(10, 0, 0), 10.0, places=6)

    def test_crossing_at_a_right_angle_closes_nothing(self):
        self.assertAlmostEqual(velocity_made_good_kn(10, 90, 0), 0.0, places=6)

    def test_forty_five_degrees_off_makes_good_the_cosine(self):
        self.assertAlmostEqual(velocity_made_good_kn(10, 45, 0), 10 * math.cos(math.radians(45)), places=6)

    def test_heading_away_makes_good_negative(self):
        self.assertAlmostEqual(velocity_made_good_kn(10, 180, 0), -10.0, places=6)

    def test_unknown_when_barely_moving(self):
        # A drifting boat's COG wanders freely, so anything derived from it is noise.
        self.assertIsNone(velocity_made_good_kn(0.05, 0, 0))

    def test_unknown_without_a_course_over_ground(self):
        self.assertIsNone(velocity_made_good_kn(10, None, 0))


class TestCrossTrackError(unittest.TestCase):
    def test_on_the_line_is_zero(self):
        mid = (LAT + 0.05, LON)
        self.assertAlmostEqual(cross_track_error_nm(*mid, LAT, LON, *NORTH_6NM), 0.0, places=3)

    def test_right_of_a_northbound_track_is_positive(self):
        # Heading north, drifted east: that is off to starboard.
        self.assertGreater(cross_track_error_nm(LAT + 0.05, LON + 0.02, LAT, LON, *NORTH_6NM), 0)

    def test_left_of_a_northbound_track_is_negative(self):
        self.assertLess(cross_track_error_nm(LAT + 0.05, LON - 0.02, LAT, LON, *NORTH_6NM), 0)


class TestWaypointNav(unittest.TestCase):
    def test_reports_bearing_and_distance_to_the_mark(self):
        nav = waypoint_nav(LAT, LON, 10.0, *NORTH_6NM)
        self.assertAlmostEqual(nav["bearing_deg"], 0.0, places=0)
        self.assertAlmostEqual(nav["distance_nm"], 6.0, places=1)

    def test_course_and_cross_track_need_an_origin(self):
        without = waypoint_nav(LAT, LON, 10.0, *NORTH_6NM)
        self.assertIsNone(without["course_deg"])
        self.assertIsNone(without["xte_nm"])
        withit = waypoint_nav(LAT + 0.05, LON, 10.0, *NORTH_6NM, origin_lat=LAT, origin_lon=LON)
        self.assertAlmostEqual(withit["course_deg"], 0.0, places=0)
        self.assertAlmostEqual(withit["xte_nm"], 0.0, places=3)

    def test_vmg_and_turn_need_a_course_over_ground(self):
        without = waypoint_nav(LAT, LON, 10.0, *NORTH_6NM)
        self.assertIsNone(without["vmg_kn"])
        self.assertIsNone(without["turn_deg"])
        withit = waypoint_nav(LAT, LON, 10.0, *NORTH_6NM, cog_deg=0.0)
        self.assertAlmostEqual(withit["vmg_kn"], 10.0, places=1)
        self.assertAlmostEqual(withit["turn_deg"], 0.0, places=1)

    def test_six_miles_at_ten_knots_is_thirty_six_minutes(self):
        nav = waypoint_nav(LAT, LON, 10.0, *NORTH_6NM, cog_deg=0.0)
        self.assertAlmostEqual(nav["ete_s"] / 60.0, 36.0, places=0)

    def test_time_remaining_uses_vmg_not_speed(self):
        # Sliding past a waypoint at 20 kn is not arriving at 20 kn. Steering 60 deg off makes
        # good half the speed, so the trip takes twice as long -- which is the whole point of
        # the field, and what a real chartplotter shows.
        straight = waypoint_nav(LAT, LON, 10.0, *NORTH_6NM, cog_deg=0.0)
        angled = waypoint_nav(LAT, LON, 10.0, *NORTH_6NM, cog_deg=60.0)
        self.assertAlmostEqual(angled["ete_s"] / straight["ete_s"], 2.0, places=1)

    def test_no_time_remaining_when_heading_away(self):
        # VMG is negative: the boat is not arriving at all, and a negative ETE would be a lie.
        self.assertIsNone(waypoint_nav(LAT, LON, 10.0, *NORTH_6NM, cog_deg=180.0)["ete_s"])

    def test_no_time_remaining_when_stopped(self):
        self.assertIsNone(waypoint_nav(LAT, LON, 0.0, *NORTH_6NM, cog_deg=0.0)["ete_s"])

    def test_turn_says_which_way_to_go(self):
        # Mark is due north; pointed north-east, so the correction is to port.
        self.assertLess(waypoint_nav(LAT, LON, 10.0, *NORTH_6NM, cog_deg=45.0)["turn_deg"], 0)
        # Pointed north-west, so the correction is to starboard.
        self.assertGreater(waypoint_nav(LAT, LON, 10.0, *NORTH_6NM, cog_deg=315.0)["turn_deg"], 0)

    def test_every_documented_field_is_present(self):
        nav = waypoint_nav(LAT, LON, 10.0, *NORTH_6NM, origin_lat=LAT, origin_lon=LON, cog_deg=10.0)
        self.assertEqual(set(nav), {"bearing_deg", "course_deg", "distance_nm", "vmg_kn",
                                    "ete_s", "xte_nm", "turn_deg"})

    def test_arriving_on_top_of_the_mark_is_not_an_error(self):
        nav = waypoint_nav(LAT, LON, 5.0, LAT, LON, cog_deg=0.0)
        self.assertEqual(nav["distance_nm"], 0.0)
        self.assertEqual(nav["ete_s"], 0.0)


class TestRelativeBearing(unittest.TestCase):
    def test_port_is_negative_and_starboard_positive(self):
        self.assertAlmostEqual(relative_bearing(0, 90), 90)
        self.assertAlmostEqual(relative_bearing(0, 270), -90)

    def test_wraps_the_short_way(self):
        self.assertAlmostEqual(relative_bearing(350, 10), 20)


if __name__ == "__main__":
    unittest.main()
