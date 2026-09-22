"""Tests for multi-leg routes: saving them, and following one leg by leg with auto-advance.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import unittest
from pathlib import Path

from app.routes import ARRIVAL_RADIUS_NM, RouteTracker

# Three points heading north up the lake, each leg a bit over a mile.
LEGS = [
    {"lat": 36.30, "lon": -86.56, "name": "Dock"},
    {"lat": 36.32, "lon": -86.56, "name": "Point A"},
    {"lat": 36.34, "lon": -86.56, "name": "Point B"},
]


class TestSavingRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "routes.json"
        self.routes = RouteTracker(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_empty(self):
        self.assertEqual(self.routes.list(), [])

    def test_create_requires_at_least_two_points(self):
        with self.assertRaises(ValueError):
            self.routes.create([LEGS[0]])

    def test_create_and_list(self):
        route = self.routes.create(LEGS, name="Morning run")
        rows = self.routes.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Morning run")
        self.assertEqual(rows[0]["legs"], 2)
        self.assertGreater(rows[0]["distance_nm"], 2.0)

    def test_an_unnamed_route_gets_a_generated_name(self):
        route = self.routes.create(LEGS)
        self.assertTrue(route.name)

    def test_rename(self):
        route = self.routes.create(LEGS, name="Original")
        self.routes.rename(route.id, "Renamed")
        self.assertEqual(self.routes.get(route.id).name, "Renamed")

    def test_rename_rejects_blank(self):
        route = self.routes.create(LEGS, name="Original")
        with self.assertRaises(ValueError):
            self.routes.rename(route.id, "  ")

    def test_delete(self):
        route = self.routes.create(LEGS)
        self.assertTrue(self.routes.delete(route.id))
        self.assertEqual(self.routes.list(), [])

    def test_routes_survive_a_restart(self):
        self.routes.create(LEGS, name="Persisted")
        reloaded = RouteTracker(self.path)
        self.assertEqual(len(reloaded.list()), 1)


class TestFollowingARoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "routes.json"
        self.routes = RouteTracker(self.path)
        self.route = self.routes.create(LEGS, name="Test route")

    def tearDown(self):
        self.tmp.cleanup()

    def test_tick_with_no_active_route_returns_none(self):
        self.assertIsNone(self.routes.tick(36.30, -86.56, 6.0))

    def test_starting_targets_the_first_leg(self):
        self.routes.start(self.route.id)
        nav = self.routes.tick(36.30, -86.56, 6.0)  # at the dock, leg 1 target is Point A
        self.assertEqual(nav["leg"], 1)
        self.assertEqual(nav["leg_name"], "Point A")
        self.assertFalse(nav["finished"])

    def test_nav_includes_the_target_position_for_drawing_on_the_chart(self):
        self.routes.start(self.route.id)
        nav = self.routes.tick(36.30, -86.56, 6.0)
        self.assertEqual((nav["leg_lat"], nav["leg_lon"]), (LEGS[1]["lat"], LEGS[1]["lon"]))

    def test_arriving_at_a_leg_advances_to_the_next(self):
        self.routes.start(self.route.id)
        # Close enough to Point A (leg 1's target) to count as arrived.
        near_point_a = (LEGS[1]["lat"] - ARRIVAL_RADIUS_NM / 70, LEGS[1]["lon"])
        nav = self.routes.tick(*near_point_a, 6.0)
        self.assertTrue(nav["advanced"])
        self.assertEqual(nav["leg"], 2)
        self.assertEqual(nav["leg_name"], "Point B")

    def test_arriving_at_the_last_leg_reports_finished_and_does_not_overrun(self):
        self.routes.start(self.route.id)
        near_point_a = (LEGS[1]["lat"] - ARRIVAL_RADIUS_NM / 70, LEGS[1]["lon"])
        self.routes.tick(*near_point_a, 6.0)  # advance onto the last leg first
        near_point_b = (LEGS[2]["lat"] - ARRIVAL_RADIUS_NM / 70, LEGS[2]["lon"])
        nav = self.routes.tick(*near_point_b, 6.0)
        self.assertTrue(nav["finished"])
        self.assertFalse(nav["advanced"])  # already on the last leg: nothing further to advance to
        self.assertEqual(nav["leg"], 2)

    def test_stop_clears_the_active_route(self):
        self.routes.start(self.route.id)
        self.assertTrue(self.routes.is_active)
        self.assertTrue(self.routes.stop())
        self.assertFalse(self.routes.is_active)
        self.assertIsNone(self.routes.tick(36.30, -86.56, 6.0))

    def test_stopping_when_nothing_is_active_returns_false(self):
        self.assertFalse(self.routes.stop())

    def test_starting_an_unknown_route_raises(self):
        with self.assertRaises(ValueError):
            self.routes.start("nope")

    def test_deleting_the_active_route_stops_navigation(self):
        self.routes.start(self.route.id)
        self.routes.delete(self.route.id)
        self.assertFalse(self.routes.is_active)

    def test_list_marks_the_active_route(self):
        self.routes.start(self.route.id)
        rows = self.routes.list()
        self.assertTrue(rows[0]["active"])
