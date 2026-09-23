"""Saving on a boat, where the power goes off without warning.

    python -m unittest discover -s tests -t . -v

The case these exist for: the battery switch goes off mid-save, a file is left truncated, the
next boot cannot read it, and the next save writes defaults over it. Before app/storage.py that
last step destroyed whatever was left, silently. Each test here pins one link of that chain.
"""
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from app import storage
from app.nav_alarms import NavAlarmManager
from app.waypoints import SavedWaypoints


def quiet():
    return mock.patch("app.storage._log")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def aside(self, name):
        return sorted(p.name for p in self.dir.glob(f"{name}.corrupt-*"))


class TestAtomicWrite(Tmp):
    def test_writes_and_reads_back(self):
        path = self.dir / "a.json"
        self.assertTrue(storage.write_json(path, {"x": 1}))
        self.assertEqual(storage.read_json(path, None), {"x": 1})

    def test_creates_the_directory(self):
        path = self.dir / "deep" / "er" / "a.json"
        self.assertTrue(storage.write_json(path, [1]))
        self.assertEqual(json.loads(path.read_text()), [1])

    def test_a_failed_write_keeps_the_old_file_and_reports_it(self):
        path = self.dir / "a.json"
        storage.write_json(path, {"old": True})
        with quiet(), mock.patch("app.storage.os.replace", side_effect=OSError("disk full")):
            self.assertFalse(storage.write_json(path, {"new": True}))
        self.assertEqual(storage.read_json(path, None), {"old": True})

    def test_a_failed_write_leaves_no_temp_file_behind(self):
        path = self.dir / "a.json"
        with quiet(), mock.patch("app.storage.os.replace", side_effect=OSError("disk full")):
            storage.write_json(path, {"x": 1})
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_the_data_is_fsynced_before_the_rename(self):
        # Without this, ext4 can persist the rename before the data and leave an empty file.
        calls = []
        real_fsync, real_replace = os.fsync, os.replace
        with mock.patch("app.storage.os.fsync", side_effect=lambda fd: (calls.append("fsync"), real_fsync(fd))), \
             mock.patch("app.storage.os.replace", side_effect=lambda a, b: (calls.append("replace"), real_replace(a, b))):
            storage.write_json(self.dir / "a.json", {"x": 1})
        self.assertLess(calls.index("fsync"), calls.index("replace"))


class TestReadingDamage(Tmp):
    def test_missing_file_is_just_the_default(self):
        self.assertEqual(storage.read_json(self.dir / "nope.json", {"d": 1}), {"d": 1})
        self.assertEqual(self.aside("nope.json"), [])

    def test_a_truncated_file_is_set_aside_not_lost(self):
        path = self.dir / "a.json"
        path.write_text('[{"lat": 36.3, "lon": -8')          # what a power cut mid-write leaves
        with quiet():
            self.assertEqual(storage.read_json(path, []), [])
        self.assertFalse(path.exists())
        kept = self.aside("a.json")
        self.assertEqual(len(kept), 1)
        self.assertEqual((self.dir / kept[0]).read_text(), '[{"lat": 36.3, "lon": -8')

    def test_bad_bytes_are_set_aside_too(self):
        path = self.dir / "a.json"
        path.write_bytes(b"\xff\xfe\x00garbage")
        with quiet():
            self.assertEqual(storage.read_json(path, {}), {})
        self.assertEqual(len(self.aside("a.json")), 1)

    def test_read_dict_rejects_the_wrong_shape(self):
        path = self.dir / "s.json"
        path.write_text("[1, 2, 3]")
        with quiet():
            self.assertEqual(storage.read_dict(path), {})
        self.assertEqual(len(self.aside("s.json")), 1)


@dataclass
class Rec:
    a: int
    b: str = "x"


class TestRecords(Tmp):
    def test_good_records_survive_a_bad_one(self):
        path = self.dir / "r.json"
        path.write_text(json.dumps([{"a": 1}, {"nope": 2}, {"a": 3, "b": "y"}]))
        with quiet():
            got = storage.read_records(path, Rec)
        self.assertEqual(got, [Rec(1), Rec(3, "y")])
        # The original is copied aside, and stays in place for the good records.
        self.assertTrue(path.exists())
        self.assertEqual(len(self.aside("r.json")), 1)

    def test_a_non_list_is_set_aside(self):
        path = self.dir / "r.json"
        path.write_text('{"a": 1}')
        with quiet():
            self.assertEqual(storage.read_records(path, Rec), [])
        self.assertEqual(len(self.aside("r.json")), 1)

    def test_all_good_touches_nothing(self):
        path = self.dir / "r.json"
        path.write_text(json.dumps([{"a": 1}]))
        self.assertEqual(storage.read_records(path, Rec), [Rec(1)])
        self.assertEqual(self.aside("r.json"), [])


class TestTheWholeChain(Tmp):
    """Truncated by a power cut -> boot -> next save. The waypoints must still be recoverable."""

    def test_saving_after_a_damaged_load_does_not_destroy_the_damaged_file(self):
        path = self.dir / "waypoints.json"
        original = '[{"id": "a1", "name": "Dock", "lat": 36.3, "lon": -86.5, "created_at": 1.0}, {"id": "b2", "na'
        path.write_text(original)
        with quiet():
            wps = SavedWaypoints(path)                      # boot
            wps.create(36.31, -86.51, "Fuel dock")          # the next save
        kept = self.aside("waypoints.json")
        self.assertEqual(len(kept), 1, "the damaged file was overwritten instead of kept")
        self.assertEqual((self.dir / kept[0]).read_text(), original)
        self.assertEqual([w["name"] for w in SavedWaypoints(path).list()], ["Fuel dock"])


class TestNavAlarmSettingsFromDisk(Tmp):
    """A readable file with the wrong types used to break every telemetry frame, once a second."""

    def test_wrong_types_fall_back_to_defaults(self):
        path = self.dir / "nav_alarms.json"
        path.write_text(json.dumps({"arrival_radius_nm": "abc", "off_course_enabled": "yes",
                                    "anchor_radius_ft": float("nan"), "gps_accuracy_hdop_max": -3}))
        mgr = NavAlarmManager(path)
        self.assertEqual(mgr.settings.arrival_radius_nm, 0.1)
        self.assertFalse(mgr.settings.off_course_enabled)
        self.assertEqual(mgr.settings.anchor_radius_ft, 100.0)
        self.assertEqual(mgr.settings.gps_accuracy_hdop_max, 4.0)

    def test_good_values_still_load(self):
        path = self.dir / "nav_alarms.json"
        path.write_text(json.dumps({"arrival_radius_nm": 0.3, "off_course_enabled": True}))
        mgr = NavAlarmManager(path)
        self.assertEqual(mgr.settings.arrival_radius_nm, 0.3)
        self.assertTrue(mgr.settings.off_course_enabled)


if __name__ == "__main__":
    unittest.main()
