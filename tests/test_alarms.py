"""Tests for the user-set alarms: thresholds, delay, hysteresis, warnings, acknowledging and saved settings.

    python -m unittest discover -s tests -t . -v
"""
import json
import tempfile
import unittest
from pathlib import Path

from app.alarms import ALARM_DEFS, AlarmManager, RUNNING_GRACE_S


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def run(manager, clock, engine=None, boat=None, seconds=0.0, step=0.2):
    """Feed the same readings for a while, the way the dashboard does (five times a second)."""
    engine = {"rpm": 0, "coolant_f": 170, "oil_pressure_psi": 40, "fuel_pct": 80, **(engine or {})}
    boat = {"battery_voltage": 13.0, "depth_ft": 20.0, **(boat or {})}
    manager.evaluate(engine, boat)
    elapsed = 0.0
    while elapsed < seconds - 1e-9:
        clock.advance(step)
        elapsed += step
        manager.evaluate(engine, boat)


def ids(manager, severity=None):
    return [a["id"] for a in manager.active() if severity in (None, a["severity"])]


class TestDefaults(unittest.TestCase):
    def test_everything_is_listed_and_only_depth_starts_off(self):
        manager = AlarmManager()
        rows = {r["id"]: r for r in manager.snapshot()["alarms"]}
        self.assertEqual(set(rows), {d["id"] for d in ALARM_DEFS})
        self.assertEqual({k for k, r in rows.items() if not r["enabled"]}, {"depth"})
        self.assertEqual((rows["coolant"]["level"], rows["coolant"]["side"], rows["coolant"]["unit"]), (210, "high", "°F"))
        self.assertEqual((rows["depth"]["level"], rows["depth"]["side"]), (5, "low"))
        self.assertEqual({r["group"] for r in rows.values()}, {"coolant", "oil", "battery", "fuel", "depth"})
        self.assertTrue(manager.sound)
        self.assertEqual(manager.active(), [])


class TestThresholds(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.m = AlarmManager(clock=self.clock)

    def test_high_alarm_needs_the_delay_then_clears_with_hysteresis(self):
        run(self.m, self.clock, {"coolant_f": 190}, seconds=1)
        self.assertEqual(ids(self.m), [])
        run(self.m, self.clock, {"coolant_f": 197}, seconds=1)                 # inside the 15 F warning band
        self.assertEqual(ids(self.m, "warning"), ["coolant"])
        run(self.m, self.clock, {"coolant_f": 212}, seconds=2.6)
        self.assertEqual(ids(self.m, "alarm"), [])                             # past the level, but not for 3 s yet
        run(self.m, self.clock, {"coolant_f": 212}, seconds=0.6)
        alarm = self.m.active()[0]
        self.assertEqual((alarm["id"], alarm["severity"], alarm["acked"]), ("coolant", "alarm", False))
        self.assertIn("high", alarm["message"])
        self.assertIn("212°F", alarm["message"])
        self.assertIn("210°F", alarm["message"])
        run(self.m, self.clock, {"coolant_f": 208}, seconds=1)                 # below the level but inside the 3 F hysteresis
        self.assertEqual(ids(self.m, "alarm"), ["coolant"])
        run(self.m, self.clock, {"coolant_f": 206}, seconds=1)
        self.assertEqual(ids(self.m, "alarm"), [])
        self.assertEqual(ids(self.m, "warning"), ["coolant"])                  # still warm, so still a warning
        run(self.m, self.clock, {"coolant_f": 170}, seconds=1)
        self.assertEqual(self.m.active(), [])

    def test_a_brief_excursion_never_fires(self):
        run(self.m, self.clock, {"coolant_f": 215}, seconds=2.0)
        run(self.m, self.clock, {"coolant_f": 170}, seconds=0.4)
        run(self.m, self.clock, {"coolant_f": 215}, seconds=2.0)               # the delay starts over
        self.assertEqual(ids(self.m, "alarm"), [])

    def test_low_alarm_for_depth_once_enabled(self):
        run(self.m, self.clock, {}, {"depth_ft": 4.0}, seconds=5)
        self.assertEqual(self.m.active(), [])                                  # off by default
        self.m.update("depth", enabled=True)
        run(self.m, self.clock, {}, {"depth_ft": 7.5}, seconds=1)
        self.assertEqual(ids(self.m, "warning"), ["depth"])                    # within 3 ft of the 5 ft level
        run(self.m, self.clock, {}, {"depth_ft": 4.0}, seconds=2.4)
        self.assertEqual(ids(self.m, "alarm"), ["depth"])
        self.assertIn("4.0 ft", self.m.active()[0]["message"])
        run(self.m, self.clock, {}, {"depth_ft": 5.3}, seconds=1)              # inside the 0.5 ft hysteresis
        self.assertEqual(ids(self.m, "alarm"), ["depth"])
        run(self.m, self.clock, {}, {"depth_ft": 5.8}, seconds=1)
        self.assertEqual(ids(self.m, "alarm"), [])

    def test_no_data_never_alarms_and_clears_an_active_alarm(self):
        run(self.m, self.clock, {"coolant_f": None, "oil_pressure_psi": None, "fuel_pct": None}, {"battery_voltage": None, "depth_ft": None}, seconds=10)
        self.assertEqual(self.m.active(), [])
        run(self.m, self.clock, {"coolant_f": 220}, seconds=3.4)
        self.assertEqual(ids(self.m, "alarm"), ["coolant"])
        run(self.m, self.clock, {"coolant_f": None}, seconds=0.4)              # the sender was unplugged: that is not a reading
        self.assertEqual(self.m.active(), [])

    def test_a_disabled_alarm_is_silent(self):
        self.m.update("coolant", enabled=False)
        run(self.m, self.clock, {"coolant_f": 250}, seconds=10)
        self.assertEqual(self.m.active(), [])

    def test_battery_has_a_low_and_a_high_alarm(self):
        run(self.m, self.clock, {}, {"battery_voltage": 15.2}, seconds=5.4)
        self.assertEqual(ids(self.m, "alarm"), ["battery_high"])
        run(self.m, self.clock, {}, {"battery_voltage": 11.0}, seconds=5.4)
        self.assertEqual(ids(self.m, "alarm"), ["battery_low"])
        self.assertIn("11.0 V", self.m.active()[0]["message"])

    def test_oil_pressure_is_only_watched_once_the_engine_has_been_running(self):
        run(self.m, self.clock, {"rpm": 0, "oil_pressure_psi": 0}, seconds=10)
        self.assertEqual(self.m.active(), [])                                  # engine off: 0 psi is normal
        run(self.m, self.clock, {"rpm": 900, "oil_pressure_psi": 5}, seconds=RUNNING_GRACE_S - 1)
        self.assertEqual(self.m.active(), [])                                  # still building pressure after the start
        run(self.m, self.clock, {"rpm": 900, "oil_pressure_psi": 5}, seconds=4.2)
        self.assertEqual(ids(self.m, "alarm"), ["oil"])
        run(self.m, self.clock, {"rpm": 0, "oil_pressure_psi": 0}, seconds=0.4)  # shut down: the alarm goes with it
        self.assertEqual(self.m.active(), [])


class TestAcknowledge(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.m = AlarmManager(clock=self.clock)

    def test_acknowledging_silences_it_until_it_clears_and_returns(self):
        run(self.m, self.clock, {"coolant_f": 220}, seconds=3.4)
        self.assertEqual(self.m.acknowledge(), 1)
        self.assertTrue(self.m.active()[0]["acked"])
        self.assertEqual(self.m.acknowledge(), 0)                              # nothing new to acknowledge
        run(self.m, self.clock, {"coolant_f": 220}, seconds=10)
        self.assertTrue(self.m.active()[0]["acked"])                           # it stays acknowledged while it persists
        run(self.m, self.clock, {"coolant_f": 170}, seconds=1)
        self.assertEqual(self.m.active(), [])
        run(self.m, self.clock, {"coolant_f": 220}, seconds=3.4)
        self.assertFalse(self.m.active()[0]["acked"])                          # a new alarm needs its own acknowledgement

    def test_a_second_alarm_is_not_covered_by_an_earlier_acknowledgement(self):
        run(self.m, self.clock, {"coolant_f": 220}, seconds=3.4)
        self.m.acknowledge()
        run(self.m, self.clock, {"coolant_f": 220, "fuel_pct": 5}, seconds=5.4)
        acked = {a["id"]: a["acked"] for a in self.m.active() if a["severity"] == "alarm"}
        self.assertEqual(acked, {"coolant": True, "fuel": False})

    def test_warnings_are_never_acknowledged_or_loud(self):
        run(self.m, self.clock, {"coolant_f": 198}, seconds=2)
        self.assertEqual(self.m.acknowledge(), 0)
        self.assertEqual(ids(self.m, "alarm"), [])


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "alarms.json"
        self.clock = Clock()

    def test_validation(self):
        m = AlarmManager(clock=self.clock)
        with self.assertRaises(ValueError):
            m.update("nonsense", enabled=True)
        with self.assertRaises(ValueError):
            m.update("coolant", level=400)
        with self.assertRaises(ValueError):
            m.update("depth", level=0.5)
        m.update("coolant", level=200)
        self.assertEqual(m.config()["coolant"]["level"], 200)

    def test_changing_a_level_rearms_the_alarm(self):
        m = AlarmManager(clock=self.clock)
        run(m, self.clock, {"coolant_f": 205}, seconds=1)
        m.update("coolant", level=200)
        run(m, self.clock, {"coolant_f": 205}, seconds=2.8)
        self.assertEqual(ids(m, "alarm"), [])
        run(m, self.clock, {"coolant_f": 205}, seconds=0.6)
        self.assertEqual(ids(m, "alarm"), ["coolant"])                          # fires against the new, lower level
        m.update("coolant", level=230)                                          # raised above the reading: clears at once
        self.assertEqual(ids(m, "alarm"), [])

    def test_settings_survive_a_restart(self):
        m = AlarmManager(self.path, clock=self.clock)
        m.update("coolant", level=195)
        m.update("depth", enabled=True, level=6)
        m.set_sound(False)
        again = AlarmManager(self.path, clock=self.clock)
        self.assertEqual(again.config()["coolant"], {"enabled": True, "level": 195})
        self.assertEqual(again.config()["depth"], {"enabled": True, "level": 6})
        self.assertFalse(again.sound)
        self.assertGreater(m.revision, 0)

    def test_a_damaged_or_hostile_file_falls_back_to_defaults(self):
        self.path.write_text("{not json")
        self.assertEqual(AlarmManager(self.path).config()["coolant"], {"enabled": True, "level": 210})
        self.path.write_text(json.dumps({"sound": "loud", "alarms": {"coolant": {"enabled": "yes", "level": 9999},
                                                                    "mystery": {"enabled": True, "level": 1}, "oil": 5}}))
        m = AlarmManager(self.path)
        self.assertEqual(m.config()["coolant"], {"enabled": True, "level": 210})   # out-of-range and mistyped values are ignored
        self.assertNotIn("mystery", m.config())
        self.assertTrue(m.sound)

    def test_the_file_is_written_in_one_piece(self):
        m = AlarmManager(self.path, clock=self.clock)
        m.update("coolant", level=200)
        self.assertEqual(json.loads(self.path.read_text())["alarms"]["coolant"]["level"], 200)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["alarms.json"])   # no temp files left behind


if __name__ == "__main__":
    unittest.main()
