"""Tests for circular boundary geofences.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import unittest
from pathlib import Path

from app.boundaries import BoundaryManager

CENTER = (36.30, -86.56)
FT_PER_DEGREE_LAT = 364000  # rough, fine for these small test offsets


def offset_lat(deg_ft):
    return CENTER[0] + deg_ft / FT_PER_DEGREE_LAT


class TestBoundaryManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "boundaries.json"
        self.mgr = BoundaryManager(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_empty(self):
        self.assertEqual(self.mgr.list(), [])

    def test_create_requires_a_positive_radius(self):
        with self.assertRaises(ValueError):
            self.mgr.create(*CENTER, radius_ft=0)
        with self.assertRaises(ValueError):
            self.mgr.create(*CENTER, radius_ft=-10)

    def test_create_rejects_a_bad_alarm_on(self):
        with self.assertRaises(ValueError):
            self.mgr.create(*CENTER, radius_ft=500, alarm_on="sideways")

    def test_create_and_list(self):
        b = self.mgr.create(*CENTER, radius_ft=500, name="No wake zone")
        rows = self.mgr.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], b.id)
        self.assertEqual(rows[0]["name"], "No wake zone")
        self.assertTrue(rows[0]["enabled"])

    def test_first_evaluate_never_fires_an_alarm(self):
        # Establishes the baseline "am I inside" state; nothing crossed yet, so nothing to alarm about.
        self.mgr.create(*CENTER, radius_ft=500, alarm_on="both")
        crossed = self.mgr.evaluate(*CENTER)  # well inside
        self.assertEqual(crossed, [])

    def test_exiting_fires_an_exit_alarm(self):
        self.mgr.create(*CENTER, radius_ft=500, alarm_on="exit")
        self.mgr.evaluate(*CENTER)  # inside: baseline
        crossed = self.mgr.evaluate(offset_lat(1000), CENTER[1])  # now 1000 ft away: outside
        self.assertEqual(len(crossed), 1)
        self.assertEqual(crossed[0]["event"], "exited")

    def test_entering_fires_an_enter_alarm(self):
        self.mgr.create(*CENTER, radius_ft=500, alarm_on="enter")
        self.mgr.evaluate(offset_lat(1000), CENTER[1])  # outside: baseline
        crossed = self.mgr.evaluate(*CENTER)  # now inside
        self.assertEqual(len(crossed), 1)
        self.assertEqual(crossed[0]["event"], "entered")

    def test_alarm_on_exit_does_not_fire_when_entering(self):
        self.mgr.create(*CENTER, radius_ft=500, alarm_on="exit")
        self.mgr.evaluate(offset_lat(1000), CENTER[1])  # outside: baseline
        crossed = self.mgr.evaluate(*CENTER)  # entering, but this boundary only cares about exits
        self.assertEqual(crossed, [])

    def test_alarm_on_both_fires_either_direction(self):
        self.mgr.create(*CENTER, radius_ft=500, alarm_on="both")
        self.mgr.evaluate(*CENTER)
        self.assertEqual(self.mgr.evaluate(offset_lat(1000), CENTER[1])[0]["event"], "exited")
        self.assertEqual(self.mgr.evaluate(*CENTER)[0]["event"], "entered")

    def test_staying_inside_never_fires(self):
        self.mgr.create(*CENTER, radius_ft=500, alarm_on="both")
        self.mgr.evaluate(*CENTER)
        crossed = self.mgr.evaluate(offset_lat(10), CENTER[1])  # still well inside 500 ft
        self.assertEqual(crossed, [])

    def test_a_disabled_boundary_never_fires(self):
        b = self.mgr.create(*CENTER, radius_ft=500, alarm_on="both", )
        self.mgr.update(b.id, enabled=False)
        self.mgr.evaluate(*CENTER)
        crossed = self.mgr.evaluate(offset_lat(1000), CENTER[1])
        self.assertEqual(crossed, [])

    def test_update_radius_and_name(self):
        b = self.mgr.create(*CENTER, radius_ft=500, name="Original")
        updated = self.mgr.update(b.id, radius_ft=1000, name="Renamed")
        self.assertEqual(updated.radius_ft, 1000)
        self.assertEqual(updated.name, "Renamed")

    def test_update_rejects_bad_values(self):
        b = self.mgr.create(*CENTER, radius_ft=500)
        with self.assertRaises(ValueError):
            self.mgr.update(b.id, radius_ft=-5)
        with self.assertRaises(ValueError):
            self.mgr.update(b.id, name="  ")
        with self.assertRaises(ValueError):
            self.mgr.update(b.id, alarm_on="nope")

    def test_delete(self):
        b = self.mgr.create(*CENTER, radius_ft=500)
        self.assertTrue(self.mgr.delete(b.id))
        self.assertFalse(self.mgr.delete(b.id))
        self.assertEqual(self.mgr.list(), [])

    def test_boundaries_survive_a_restart(self):
        self.mgr.create(*CENTER, radius_ft=500, name="Persisted")
        reloaded = BoundaryManager(self.path)
        self.assertEqual(len(reloaded.list()), 1)
        self.assertEqual(reloaded.list()[0]["name"], "Persisted")
