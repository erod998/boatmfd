"""Tests for the saved-waypoint list.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import unittest
from pathlib import Path

from app.waypoints import SavedWaypoints


class TestSavedWaypoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "waypoints.json"
        self.wps = SavedWaypoints(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_empty(self):
        self.assertEqual(self.wps.list(), [])

    def test_create_and_list(self):
        wp = self.wps.create(36.3, -86.5, "Dock")
        rows = self.wps.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], wp.id)
        self.assertEqual(rows[0]["name"], "Dock")
        self.assertEqual(rows[0]["lat"], 36.3)
        self.assertEqual(rows[0]["lon"], -86.5)

    def test_an_unnamed_waypoint_gets_a_generated_name(self):
        wp1 = self.wps.create(36.3, -86.5)
        wp2 = self.wps.create(36.31, -86.51)
        self.assertEqual(wp1.name, "WPT 1")
        self.assertEqual(wp2.name, "WPT 2")

    def test_waypoints_are_listed_in_creation_order(self):
        first = self.wps.create(36.3, -86.5, "First")
        second = self.wps.create(36.31, -86.51, "Second")
        self.assertEqual([w["id"] for w in self.wps.list()], [first.id, second.id])

    def test_get_an_unknown_id_returns_none(self):
        self.assertIsNone(self.wps.get("nope"))

    def test_update_name(self):
        wp = self.wps.create(36.3, -86.5, "Original")
        updated = self.wps.update(wp.id, name="Renamed")
        self.assertEqual(updated.name, "Renamed")
        self.assertEqual(self.wps.get(wp.id).name, "Renamed")

    def test_update_rejects_a_blank_name(self):
        wp = self.wps.create(36.3, -86.5, "Original")
        with self.assertRaises(ValueError):
            self.wps.update(wp.id, name="   ")

    def test_update_position(self):
        wp = self.wps.create(36.3, -86.5, "Movable")
        updated = self.wps.update(wp.id, lat=36.4, lon=-86.6)
        self.assertEqual((updated.lat, updated.lon), (36.4, -86.6))

    def test_update_an_unknown_id_returns_none(self):
        self.assertIsNone(self.wps.update("nope", name="x"))

    def test_delete_one(self):
        keep = self.wps.create(36.3, -86.5, "Keep")
        gone = self.wps.create(36.31, -86.51, "Gone")
        self.assertTrue(self.wps.delete(gone.id))
        self.assertFalse(self.wps.delete(gone.id))
        self.assertEqual([w["id"] for w in self.wps.list()], [keep.id])

    def test_delete_all(self):
        self.wps.create(36.3, -86.5, "A")
        self.wps.create(36.31, -86.51, "B")
        self.assertEqual(self.wps.delete_all(), 2)
        self.assertEqual(self.wps.list(), [])

    def test_waypoints_survive_a_restart(self):
        self.wps.create(36.3, -86.5, "Persisted")
        reloaded = SavedWaypoints(self.path)
        self.assertEqual(len(reloaded.list()), 1)
        self.assertEqual(reloaded.list()[0]["name"], "Persisted")

    def test_a_damaged_file_falls_back_to_empty(self):
        self.path.write_text("not json")
        broken = SavedWaypoints(self.path)
        self.assertEqual(broken.list(), [])
