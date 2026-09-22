"""Tests for saved chart tracks.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import unittest
from pathlib import Path

from app.tracks import SavedTracks

# Roughly 0.01 degrees of latitude apart, about 0.6 nm each hop.
POINTS = [
    {"lat": 36.30, "lon": -86.56, "t": 1000.0},
    {"lat": 36.31, "lon": -86.56, "t": 1001.0},
    {"lat": 36.32, "lon": -86.56, "t": 1002.0},
]


class TestSavedTracks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "tracks.json"
        self.tracks = SavedTracks(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_empty(self):
        self.assertEqual(self.tracks.list(), [])

    def test_save_refuses_an_empty_track(self):
        with self.assertRaises(ValueError):
            self.tracks.save([])

    def test_save_and_list_a_track(self):
        saved = self.tracks.save(POINTS, name="Morning cruise")
        rows = self.tracks.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], saved.id)
        self.assertEqual(rows[0]["name"], "Morning cruise")
        self.assertEqual(rows[0]["points"], 3)
        self.assertGreater(rows[0]["distance_nm"], 1.0)  # two ~0.6 nm hops

    def test_an_unnamed_track_gets_a_generated_name(self):
        saved = self.tracks.save(POINTS)
        self.assertTrue(saved.name)
        saved_blank = self.tracks.save(POINTS, name="   ")
        self.assertTrue(saved_blank.name.strip())

    def test_newest_saved_track_is_listed_first(self):
        first = self.tracks.save(POINTS, name="First")
        second = self.tracks.save(POINTS, name="Second")
        self.assertEqual([r["id"] for r in self.tracks.list()], [second.id, first.id])

    def test_get_returns_the_full_points(self):
        saved = self.tracks.save(POINTS, name="Full")
        fetched = self.tracks.get(saved.id)
        self.assertEqual(fetched.points, POINTS)
        self.assertIsNone(self.tracks.get("nope"))

    def test_rename(self):
        saved = self.tracks.save(POINTS, name="Original")
        renamed = self.tracks.rename(saved.id, "New name")
        self.assertEqual(renamed.name, "New name")
        self.assertEqual(self.tracks.get(saved.id).name, "New name")
        self.assertEqual(self.tracks.list()[0]["name"], "New name")

    def test_rename_rejects_a_blank_name(self):
        saved = self.tracks.save(POINTS, name="Original")
        with self.assertRaises(ValueError):
            self.tracks.rename(saved.id, "   ")
        self.assertEqual(self.tracks.get(saved.id).name, "Original")

    def test_rename_an_unknown_id_returns_none(self):
        self.assertIsNone(self.tracks.rename("nope", "New name"))

    def test_delete_one(self):
        keep = self.tracks.save(POINTS, name="Keep")
        gone = self.tracks.save(POINTS, name="Gone")
        self.assertTrue(self.tracks.delete(gone.id))
        self.assertFalse(self.tracks.delete(gone.id))  # already gone
        self.assertEqual([r["id"] for r in self.tracks.list()], [keep.id])

    def test_delete_all(self):
        self.tracks.save(POINTS, name="A")
        self.tracks.save(POINTS, name="B")
        count = self.tracks.delete_all()
        self.assertEqual(count, 2)
        self.assertEqual(self.tracks.list(), [])

    def test_saved_tracks_survive_a_restart(self):
        self.tracks.save(POINTS, name="Persisted")
        reloaded = SavedTracks(self.path)
        self.assertEqual(len(reloaded.list()), 1)
        self.assertEqual(reloaded.list()[0]["name"], "Persisted")

    def test_a_damaged_file_falls_back_to_empty(self):
        self.path.write_text("not json")
        broken = SavedTracks(self.path)
        self.assertEqual(broken.list(), [])
