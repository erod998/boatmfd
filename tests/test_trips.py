"""Trip logging: distance, speed, saved history, and fuel burned during a trip."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.trips import TripTracker


class TestTripTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "trips.json"

    def test_fuel_burned_is_integrated_from_gph(self):
        clock = [1000.0]
        with mock.patch("app.trips.time.time", side_effect=lambda: clock[0]):
            tracker = TripTracker(self.path)
            tracker.start(36.30, -86.56)
            for _ in range(3600):                        # one hour at 4.0 GPH, one tick per second
                clock[0] += 1.0
                tracker.tick(36.30, -86.56, 5.0, gph=4.0)
            self.assertAlmostEqual(tracker.current()["fuel_gal"], 4.0, delta=0.01)
            finished = tracker.stop()
        self.assertAlmostEqual(finished.fuel_gal, 4.0, delta=0.01)
        self.assertAlmostEqual(TripTracker(self.path).history()[0]["fuel_gal"], 4.0, delta=0.01)

    def test_no_fuel_data_means_no_fuel_counted(self):
        tracker = TripTracker(self.path)
        tracker.start(36.30, -86.56)
        tracker.tick(36.30, -86.56, 5.0, gph=None)
        tracker.tick(36.30, -86.56, 5.0)
        self.assertEqual(tracker.current()["fuel_gal"], 0.0)

    def test_trips_saved_before_fuel_tracking_still_load(self):
        old = [{"id": "abc12345", "start_time": 1.0, "end_time": 61.0, "start_lat": 1.0, "start_lon": 2.0,
                "end_lat": 1.0, "end_lon": 2.0, "distance_nm": 0.5, "max_speed_kn": 6.0, "duration_s": 60.0}]
        self.path.write_text(json.dumps(old))
        trips = TripTracker(self.path).history()
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0]["fuel_gal"], 0.0)


class TestTripDistance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "trips.json"

    def test_gps_wander_at_anchor_is_not_distance(self):
        # A GPS at rest still moves a few metres between fixes. An hour of that, once a second,
        # used to add miles to the trip.
        tracker = TripTracker(self.path)
        tracker.start(36.30, -86.56)
        for i in range(3600):
            wobble = 0.00002 * (1 if i % 2 else -1)          # ~2 m back and forth
            tracker.tick(36.30 + wobble, -86.56 - wobble, 0.1)
        self.assertLess(tracker.current()["distance_nm"], 0.001)

    def test_real_travel_still_counts(self):
        tracker = TripTracker(self.path)
        tracker.start(36.30, -86.56)
        for i in range(1, 101):
            tracker.tick(36.30 + i * 0.001, -86.56, 20.0)     # 0.1 deg north, at speed
        self.assertAlmostEqual(tracker.current()["distance_nm"], 6.0, delta=0.05)

    def test_resuming_travel_after_a_stop_does_not_count_the_drift(self):
        tracker = TripTracker(self.path)
        tracker.start(36.30, -86.56)
        tracker.tick(36.30, -86.56, 0.0)
        tracker.tick(36.31, -86.56, 0.2)                      # drifted 0.6 nm while stopped
        tracker.tick(36.31, -86.56, 10.0)                     # under way again from the new spot
        self.assertLess(tracker.current()["distance_nm"], 0.001)

    def test_one_glitched_fix_does_not_set_max_speed(self):
        tracker = TripTracker(self.path)
        tracker.start(36.30, -86.56)
        for sog in (20.0, 21.0, 88.0, 21.0, 22.0):
            tracker.tick(36.30, -86.56, sog)
        self.assertEqual(tracker.current()["max_speed_kn"], 21.0)

    def test_a_held_speed_does_count(self):
        tracker = TripTracker(self.path)
        tracker.start(36.30, -86.56)
        for sog in (20.0, 35.0, 35.0, 20.0):
            tracker.tick(36.30, -86.56, sog)
        self.assertEqual(tracker.current()["max_speed_kn"], 35.0)


class TestTripSurvivesPowerLoss(unittest.TestCase):
    """The battery switch, not the Stop button, is how a day on the boat actually ends."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "trips.json"
        self.clock = [100000.0]
        patcher = mock.patch("app.trips.time.time", side_effect=lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def drive(self, tracker, seconds):
        for i in range(seconds):
            self.clock[0] += 1.0
            tracker.tick(36.30 + i * 0.0001, -86.56, 15.0, gph=6.0)

    def test_a_short_interruption_resumes_the_trip(self):
        tracker = TripTracker(self.path)
        trip_id = tracker.start(36.30, -86.56).id
        self.drive(tracker, 120)
        # Power cut: no stop(), no flush(). An engine crank, say; back in two minutes.
        self.clock[0] += 120
        with mock.patch("builtins.print"):
            again = TripTracker(self.path)
        self.assertTrue(again.is_active)
        self.assertEqual(again.current()["id"], trip_id)
        self.assertGreater(again.current()["distance_nm"], 0.5)
        self.assertGreater(again.current()["fuel_gal"], 0.1)
        self.assertEqual(again.history(), [])

    def test_a_long_interruption_closes_the_trip_where_it_was(self):
        tracker = TripTracker(self.path)
        trip_id = tracker.start(36.30, -86.56).id
        self.drive(tracker, 120)
        saved_at = tracker._last_checkpoint
        self.clock[0] += 3 * 24 * 3600                        # found again next weekend
        with mock.patch("builtins.print"):
            again = TripTracker(self.path)
        self.assertFalse(again.is_active)
        [trip] = again.history()
        self.assertEqual(trip["id"], trip_id)
        self.assertEqual(trip["end_time"], saved_at)          # not next weekend
        self.assertGreater(trip["distance_nm"], 0.5)
        self.assertFalse(again.active_path.exists())

    def test_a_trip_stopped_just_before_the_power_went_is_not_doubled(self):
        tracker = TripTracker(self.path)
        tracker.start(36.30, -86.56)
        self.drive(tracker, 60)
        checkpoint = tracker.active_path.read_text()
        tracker.stop()
        tracker.active_path.write_text(checkpoint)            # stop saved, but the cleanup was lost
        again = TripTracker(self.path)
        self.assertEqual(len(again.history()), 1)
        self.assertFalse(again.is_active)
        self.assertFalse(again.active_path.exists())

    def test_stop_clears_the_checkpoint(self):
        tracker = TripTracker(self.path)
        tracker.start(36.30, -86.56)
        self.assertTrue(tracker.active_path.exists())
        tracker.stop()
        self.assertFalse(tracker.active_path.exists())


if __name__ == "__main__":
    unittest.main()
