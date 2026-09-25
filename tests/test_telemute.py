"""Tests for the alarm-to-stereo TelMute-style ducking.

    python -m unittest discover -s tests -t . -v
"""
import json
import tempfile
import unittest
from pathlib import Path

from app.telemute import MUTE_SECONDS, AlarmMute


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeMedia:
    def __init__(self):
        self.calls = []

    def handle(self, action, value=None, zone=1):
        self.calls.append((action, value, zone))


def alarm(id_, severity="alarm"):
    return {"id": id_, "severity": severity}


class TestAlarmMute(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.media = FakeMedia()
        self.mute = AlarmMute(None, self.media, clock=self.clock)

    def test_enabled_by_default(self):
        self.assertTrue(self.mute.enabled)

    def test_a_new_alarm_mutes_the_stereo(self):
        self.mute.tick([alarm("coolant")])
        self.assertEqual(self.media.calls, [("mute", True, 1)])

    def test_the_same_ongoing_alarm_does_not_mute_again(self):
        self.mute.tick([alarm("coolant")])
        self.media.calls.clear()
        self.mute.tick([alarm("coolant")])
        self.assertEqual(self.media.calls, [])

    def test_auto_unmutes_after_the_window_once_the_alarm_clears(self):
        self.mute.tick([alarm("coolant")])
        self.media.calls.clear()
        self.clock.advance(MUTE_SECONDS + 0.1)
        self.mute.tick([])
        self.assertEqual(self.media.calls, [("mute", False, 1)])

    def test_a_second_alarm_before_the_window_ends_extends_it(self):
        self.mute.tick([alarm("coolant")])
        self.clock.advance(MUTE_SECONDS - 1)
        self.mute.tick([alarm("oil")])  # a new alarm id resets the countdown
        self.media.calls.clear()
        self.clock.advance(MUTE_SECONDS - 1)
        self.mute.tick([])  # still within the extended window
        self.assertEqual(self.media.calls, [])
        self.clock.advance(2)
        self.mute.tick([])
        self.assertEqual(self.media.calls, [("mute", False, 1)])

    def test_disabled_never_mutes(self):
        self.mute.set_enabled(False)
        self.mute.tick([alarm("coolant")])
        self.assertEqual(self.media.calls, [])

    def test_turning_off_while_muted_unmutes_immediately(self):
        self.mute.tick([alarm("coolant")])
        self.media.calls.clear()
        self.mute.set_enabled(False)
        self.assertEqual(self.media.calls, [("mute", False, 1)])

    def test_a_manual_unmute_is_not_fought(self):
        self.mute.tick([alarm("coolant")])
        self.media.calls.clear()
        self.mute.note_external_mute(False)  # the driver hit the Mute button themselves
        self.clock.advance(MUTE_SECONDS + 0.1)
        self.mute.tick([alarm("coolant")])  # same alarm still active, but we no longer think we're the one muting
        self.assertEqual(self.media.calls, [])

    def test_warnings_do_not_trigger_a_mute(self):
        self.mute.tick([alarm("coolant", severity="warning")])
        self.assertEqual(self.media.calls, [])

    def test_settings_persist_across_instances(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "telemute.json"
            m1 = AlarmMute(path, FakeMedia())
            m1.set_enabled(False)
            m2 = AlarmMute(path, FakeMedia())
            self.assertFalse(m2.enabled)

    def test_a_damaged_settings_file_falls_back_to_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "telemute.json"
            path.write_text("not json")
            m = AlarmMute(path, FakeMedia())
            self.assertTrue(m.enabled)


class BusDown(FakeMedia):
    """A stereo that can't be reached: the CAN send fails, as N2kNode reports it (OSError)."""

    down = False

    def handle(self, action, value=None, zone=1):
        if self.down:
            raise OSError("CAN send failed: Transmit buffer full")
        super().handle(action, value, zone)


class TestUnmuteWhenTheBusIsDown(unittest.TestCase):
    def test_a_failed_unmute_is_retried_not_forgotten_and_never_raises(self):
        clock, media = Clock(), BusDown()
        mute = AlarmMute(None, media, clock=clock)
        mute.tick([alarm("oil")])
        media.down = True
        clock.advance(MUTE_SECONDS + 1)
        for _ in range(25):                   # five seconds of fast frames: none may raise
            mute.tick([])
            clock.advance(0.2)
        media.down = False
        clock.advance(5.0)
        mute.tick([])
        self.assertEqual(media.calls[-1][:2], ("mute", False))   # the stereo is unmuted in the end


if __name__ == "__main__":
    unittest.main()
