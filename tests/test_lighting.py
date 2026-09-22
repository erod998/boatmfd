"""Tests for the lighting controller: changes reach the lights at once, the rainbow animates,
and the driver is only written to when something changed.

    python -m unittest discover -s tests -t . -v
"""
import threading
import time
import unittest

from app.lighting import LightingController


class RecordingDriver:
    def __init__(self):
        self.frames = []          # (time, frame) for every show()
        self.lock = threading.Lock()

    def show(self, frame):
        with self.lock:
            self.frames.append((time.monotonic(), list(frame)))

    def count(self):
        with self.lock:
            return len(self.frames)

    def last(self):
        with self.lock:
            return self.frames[-1][1] if self.frames else None


def wait_for(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class TestApplyingChanges(unittest.TestCase):
    def setUp(self):
        self.driver = RecordingDriver()
        self.lights = LightingController(3, driver=self.driver)
        self.addCleanup(self.lights.stop)

    def test_a_change_reaches_the_driver_without_waiting_for_anything_else(self):
        self.lights.start()
        wait_for(lambda: self.driver.count() >= 1)                      # the loop's first pass
        began = time.monotonic()
        self.lights.set_power(True)
        self.lights.set_brightness(1.0)
        self.lights.set_solid_color(255, 0, 0)
        self.assertTrue(wait_for(lambda: self.driver.last() == [(255, 0, 0)] * 3, timeout=1.0))
        self.assertLess(time.monotonic() - began, 0.5)                  # it used to take up to a second (the telemetry tick)

    def test_tick_applies_immediately_for_the_api_handler(self):
        self.lights.set_power(True)
        self.lights.set_brightness(0.5)
        self.lights.set_solid_color(200, 100, 0)
        frame = self.lights.tick()
        self.assertEqual(frame, [(100, 50, 0)] * 3)
        self.assertEqual(self.driver.last(), frame)
        self.assertEqual(self.lights.state()["frame"], frame)

    def test_off_means_black_whatever_the_colour(self):
        self.lights.set_solid_color(255, 255, 255)
        self.assertEqual(self.lights.tick(), [(0, 0, 0)] * 3)

    def test_an_unchanged_frame_is_not_rewritten_until_the_refresh_is_due(self):
        self.lights.REFRESH_S = 0.15
        self.lights.set_power(True)
        self.lights.tick()
        before = self.driver.count()
        for _ in range(20):
            self.lights.tick()
        self.assertEqual(self.driver.count(), before)                   # nothing changed, nothing sent
        time.sleep(0.2)
        self.lights.tick()
        self.assertEqual(self.driver.count(), before + 1)               # but it is sent again now and then, to recover from a glitch

    def test_the_state_carries_the_frame_and_the_settings(self):
        self.lights.set_power(True)
        self.lights.set_solid_color(0, 0, 255)
        self.lights.tick()
        state = self.lights.state()
        self.assertEqual((state["on"], state["preset"], list(state["solid_color"])), (True, "solid", [0, 0, 255]))
        self.assertEqual(len(state["frame"]), 3)

    def test_unknown_presets_are_refused(self):
        with self.assertRaises(ValueError):
            self.lights.set_preset("strobe")


class TestRainbow(unittest.TestCase):
    def test_the_rainbow_moves_smoothly_and_a_solid_colour_stops_it(self):
        driver = RecordingDriver()
        lights = LightingController(1, driver=driver)
        lights.start()
        self.addCleanup(lights.stop)
        lights.set_power(True)
        lights.set_preset("rainbow")
        time.sleep(1.0)
        rainbow_frames = driver.count()
        self.assertGreater(rainbow_frames, 12)                          # it stepped once a second before
        colours = {tuple(f[0]) for _, f in driver.frames[-10:]}
        self.assertGreater(len(colours), 3)                              # and the colour really is changing
        lights.set_solid_color(0, 255, 0)
        self.assertTrue(wait_for(lambda: driver.last() == [(0, 153, 0)]))          # green at the default 60% brightness
        time.sleep(0.3)
        settled = driver.count()
        time.sleep(0.4)
        self.assertLessEqual(driver.count() - settled, 1)                # a solid colour is not streamed

    def test_a_failing_driver_does_not_stop_the_loop(self):
        class Flaky(RecordingDriver):
            failures = 2

            def show(self, frame):
                if self.failures:
                    self.failures -= 1
                    raise OSError("i2c hiccup")
                super().show(frame)

        driver = Flaky()
        lights = LightingController(1, driver=driver)
        lights.start()
        self.addCleanup(lights.stop)
        lights.set_power(True)
        lights.set_solid_color(255, 0, 0)
        lights.set_brightness(1.0)
        self.assertTrue(wait_for(lambda: driver.last() == [(255, 0, 0)], timeout=3.0))


if __name__ == "__main__":
    unittest.main()
