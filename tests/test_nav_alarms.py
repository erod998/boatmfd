"""Tests for navigation alarms: Arrival, Off Course, Anchor Drag, GPS Accuracy.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import types
import unittest
from pathlib import Path

from app.nav_alarms import NavAlarmManager

LAT, LON = 36.306, -86.563


def fix(lat=LAT, lon=LON, hdop=1.0, has_fix=True):
    return types.SimpleNamespace(lat=lat, lon=lon, hdop=hdop, has_fix=has_fix)


def nav(distance_nm=1.0, xte_nm=0.0):
    return {"bearing_deg": 0, "course_deg": 0, "distance_nm": distance_nm, "vmg_kn": None,
            "ete_s": None, "xte_nm": xte_nm, "turn_deg": None}


def ids(alerts):
    return [a["id"] for a in alerts]


class TestArrival(unittest.TestCase):
    def test_no_alert_while_far_away(self):
        mgr = NavAlarmManager()
        self.assertNotIn("arrival", ids(mgr.evaluate(fix(), nav(distance_nm=2.0))))

    def test_fires_once_on_arrival_not_every_tick(self):
        mgr = NavAlarmManager()
        first = mgr.evaluate(fix(), nav(distance_nm=0.05))
        second = mgr.evaluate(fix(), nav(distance_nm=0.04))
        self.assertIn("arrival", ids(first))
        self.assertNotIn("arrival", ids(second))

    def test_fires_again_after_leaving_and_re_arriving(self):
        mgr = NavAlarmManager()
        mgr.evaluate(fix(), nav(distance_nm=0.05))  # arrives
        mgr.evaluate(fix(), nav(distance_nm=2.0))   # leaves
        third = mgr.evaluate(fix(), nav(distance_nm=0.05))  # arrives again (e.g. a new waypoint)
        self.assertIn("arrival", ids(third))

    def test_disabled_never_fires(self):
        mgr = NavAlarmManager()
        mgr.update_settings(arrival_enabled=False)
        self.assertNotIn("arrival", ids(mgr.evaluate(fix(), nav(distance_nm=0.01))))

    def test_no_active_waypoint_means_no_arrival_check(self):
        mgr = NavAlarmManager()
        self.assertNotIn("arrival", ids(mgr.evaluate(fix(), None)))


class TestOffCourse(unittest.TestCase):
    def test_disabled_by_default(self):
        mgr = NavAlarmManager()
        self.assertNotIn("off_course", ids(mgr.evaluate(fix(), nav(xte_nm=5.0))))

    def test_fires_past_the_threshold(self):
        mgr = NavAlarmManager()
        mgr.update_settings(off_course_enabled=True, off_course_xte_nm=0.25)
        self.assertIn("off_course", ids(mgr.evaluate(fix(), nav(xte_nm=0.3))))

    def test_does_not_fire_within_the_threshold(self):
        mgr = NavAlarmManager()
        mgr.update_settings(off_course_enabled=True, off_course_xte_nm=0.25)
        self.assertNotIn("off_course", ids(mgr.evaluate(fix(), nav(xte_nm=0.1))))

    def test_fires_on_either_side_of_the_line(self):
        mgr = NavAlarmManager()
        mgr.update_settings(off_course_enabled=True, off_course_xte_nm=0.25)
        self.assertIn("off_course", ids(mgr.evaluate(fix(), nav(xte_nm=-0.3))))


class TestAnchorDrag(unittest.TestCase):
    def test_no_alert_when_anchor_is_not_dropped(self):
        mgr = NavAlarmManager()
        self.assertNotIn("anchor_drag", ids(mgr.evaluate(fix(), None)))

    def test_no_alert_right_after_dropping(self):
        mgr = NavAlarmManager()
        mgr.drop_anchor(LAT, LON)
        self.assertNotIn("anchor_drag", ids(mgr.evaluate(fix(LAT, LON), None)))

    def test_fires_once_dragged_past_the_radius(self):
        mgr = NavAlarmManager()
        mgr.update_settings(anchor_radius_ft=100)
        mgr.drop_anchor(LAT, LON)
        far = fix(LAT + 500 / 364000, LON)  # roughly 500 ft north
        self.assertIn("anchor_drag", ids(mgr.evaluate(far, None)))

    def test_raising_the_anchor_stops_the_alarm(self):
        mgr = NavAlarmManager()
        mgr.update_settings(anchor_radius_ft=100)
        mgr.drop_anchor(LAT, LON)
        self.assertTrue(mgr.raise_anchor())
        far = fix(LAT + 500 / 364000, LON)
        self.assertNotIn("anchor_drag", ids(mgr.evaluate(far, None)))

    def test_raise_anchor_when_not_dropped_returns_false(self):
        mgr = NavAlarmManager()
        self.assertFalse(mgr.raise_anchor())

    def test_anchor_dropped_property(self):
        mgr = NavAlarmManager()
        self.assertFalse(mgr.anchor_dropped)
        mgr.drop_anchor(LAT, LON)
        self.assertTrue(mgr.anchor_dropped)


class TestGpsAccuracy(unittest.TestCase):
    def test_disabled_by_default(self):
        mgr = NavAlarmManager()
        self.assertNotIn("gps_accuracy", ids(mgr.evaluate(fix(hdop=9.0), None)))

    def test_fires_past_the_threshold(self):
        mgr = NavAlarmManager()
        mgr.update_settings(gps_accuracy_enabled=True, gps_accuracy_hdop_max=4.0)
        self.assertIn("gps_accuracy", ids(mgr.evaluate(fix(hdop=5.0), None)))

    def test_does_not_fire_within_the_threshold(self):
        mgr = NavAlarmManager()
        mgr.update_settings(gps_accuracy_enabled=True, gps_accuracy_hdop_max=4.0)
        self.assertNotIn("gps_accuracy", ids(mgr.evaluate(fix(hdop=1.0), None)))

    def test_is_a_warning_not_an_alarm(self):
        mgr = NavAlarmManager()
        mgr.update_settings(gps_accuracy_enabled=True, gps_accuracy_hdop_max=4.0)
        alerts = mgr.evaluate(fix(hdop=5.0), None)
        self.assertEqual(alerts[0]["severity"], "warning")


class TestNoFix(unittest.TestCase):
    def test_no_fix_suppresses_everything(self):
        mgr = NavAlarmManager()
        mgr.update_settings(off_course_enabled=True, gps_accuracy_enabled=True)
        mgr.drop_anchor(LAT, LON)
        alerts = mgr.evaluate(fix(has_fix=False), nav(distance_nm=0.01, xte_nm=5.0))
        self.assertEqual(ids(alerts), [])


class TestSettingsPersistence(unittest.TestCase):
    def test_update_rejects_an_unknown_setting(self):
        mgr = NavAlarmManager()
        with self.assertRaises(ValueError):
            mgr.update_settings(warp_speed=True)

    def test_settings_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nav_alarms.json"
            mgr1 = NavAlarmManager(path)
            mgr1.update_settings(arrival_radius_nm=0.2, off_course_enabled=True)
            mgr2 = NavAlarmManager(path)
            self.assertEqual(mgr2.settings.arrival_radius_nm, 0.2)
            self.assertTrue(mgr2.settings.off_course_enabled)

    def test_a_damaged_settings_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nav_alarms.json"
            path.write_text("not json")
            mgr = NavAlarmManager(path)
            self.assertTrue(mgr.settings.arrival_enabled)
