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


if __name__ == "__main__":
    unittest.main()
