"""The passive-tap sensor board, end to end through the sensor hub.

    python -m unittest discover -s tests -t . -v

A fake board stands in for the two ADS1115s. Its inputs behave like the real thing: each analog
gauge is a Thevenin source (a share of the gauge supply behind a resistance) driving its sender,
seen through the board's 49k/10k tap divider. The analog gauges are "still connected" -- nothing
here drives the senders, it only reads them.
"""
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import sender_tap
from app.sensors import ADS1115Pair, Calibration, SensorHub
from app.state import Settings

DIVIDER = 10_000 / (49_000 + 10_000)     # tap volts -> ADC volts


class GaugeBoard:
    """Eight ADC inputs wired as SENSOR_BOARD.md describes."""

    def __init__(self, supply=12.6, fuel_pct=50.0, trim_ohm=40.0, oil_ohm=10.0, temp_f=70.0):
        self.supply, self.fuel_pct, self.trim_ohm, self.oil_ohm = supply, fuel_pct, trim_ohm, oil_ohm
        self.battery = 12.7
        self.fuel_wire_on = True
        # Engine temperature: a thermistor sender on MerCruiser's published curve (about 134 ohm
        # at 140 F, B about 3890 K) driven by a gauge with a 120 ohm Thevenin resistance.
        self.temp_f = temp_f
        self.temp_beta, self.temp_r140, self.temp_r_th = 3890.0, 134.0, 120.0
        self.temp_wire_on = True

    def temp_sender_ohms(self):
        k = (self.temp_f - 32) * 5 / 9 + 273.15
        return self.temp_r140 * math.exp(self.temp_beta * (1 / k - 1 / 333.15))

    @staticmethod
    def s_terminal(supply, r_sender, r_th, k):
        return k * supply * r_sender / (r_sender + r_th)

    def read_volts(self, channel):
        if channel == 0:   # fuel: air-core gauge, 110 ohm Thevenin resistance, V_th = 0.92 x supply
            if not self.fuel_wire_on:
                return 0.0
            r = sender_tap.sender_ohms_at(self.fuel_pct, 240.0, 33.0)
            return self.s_terminal(self.supply, r, 110.0, 0.92) * DIVIDER
        if channel == 1:   # trim
            return self.s_terminal(self.supply, self.trim_ohm, 70.0, 0.95) * DIVIDER
        if channel == 2:   # battery, on its own 47k/10k divider
            return self.battery * 10 / 57
        if channel == 3:   # the gauges' I terminal
            return self.supply * DIVIDER
        if channel == 4:   # oil, on the second converter
            return self.s_terminal(self.supply, self.oil_ohm, 60.0, 0.9) * DIVIDER
        if channel == 5:   # engine temperature
            if not self.temp_wire_on:
                return 0.0
            return self.s_terminal(self.supply, self.temp_sender_ohms(), self.temp_r_th, 0.9) * DIVIDER
        raise OSError("input not wired")


def board_hub(tmp, board, w1=None, **settings):
    s = Settings(sensors="real", sender_wiring="tap", oil_sender=True, temp_sender=True, **settings)
    return SensorHub(s, Calibration(Path(tmp) / "calibration.json"), adc=board, tach=None, w1=w1)


class OneProbe:
    """A DS18B20 clamped to the thermostat housing."""

    def __init__(self, temp_f):
        self.temp_f = temp_f

    def devices(self):
        return ["28-000000000001"]

    def read_f(self, device):
        return self.temp_f


class TestSensorBoard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.board = GaugeBoard()
        self.hub = board_hub(self.tmp.name, self.board)

    def settle(self, n=200):
        for _ in range(n):
            self.hub.sample_once()

    def test_nothing_is_shown_before_calibration(self):
        self.settle()
        e = self.hub.engine()
        self.assertIsNone(e["fuel_pct"])
        self.assertIsNone(e["trim_pct"])
        self.assertIsNone(e["oil_pressure_psi"])
        self.assertIsNone(e["coolant_f"])

    def test_fuel_from_a_fill_up_and_one_reading_off_the_analog_gauge(self):
        self.board.fuel_pct = 100.0
        self.settle()
        self.hub.capture("fuel_full")                          # at the fuel dock
        self.board.fuel_pct = 50.0
        self.settle()
        self.hub.capture("fuel_point", 50.0)                   # a week later, gauge on 1/2
        for pct in (10.0, 30.0, 85.0):
            with self.subTest(pct=pct):
                self.board.fuel_pct = pct
                self.settle()
                self.assertAlmostEqual(self.hub.engine()["fuel_pct"], pct, delta=0.5)
        self.assertEqual(self.hub.status()["tap"]["fuel"]["method"], "divider")

    def test_starting_the_engine_does_not_move_the_fuel_level(self):
        self.board.fuel_pct = 100.0; self.settle(); self.hub.capture("fuel_full")
        self.board.fuel_pct = 50.0; self.settle(); self.hub.capture("fuel_point", 50.0)
        self.board.fuel_pct = 40.0
        self.board.supply = 12.4; self.settle()
        key_on = self.hub.engine()["fuel_pct"]
        self.board.supply = 14.3; self.settle()               # alternator charging
        self.assertAlmostEqual(self.hub.engine()["fuel_pct"], key_on, delta=0.3)

    def test_key_off_is_no_reading_like_the_analog_gauge(self):
        self.board.fuel_pct = 100.0; self.settle(); self.hub.capture("fuel_full")
        self.board.fuel_pct = 50.0; self.settle(); self.hub.capture("fuel_point", 50.0)
        self.board.supply = 0.0
        self.settle(4)
        self.assertIsNone(self.hub.engine()["fuel_pct"])
        self.assertFalse(self.hub.status()["tap"]["gauges_on"])

    def test_a_tap_wire_that_falls_off_is_no_reading_not_a_full_tank(self):
        self.board.fuel_pct = 100.0; self.settle(); self.hub.capture("fuel_full")
        self.board.fuel_pct = 20.0; self.settle(); self.hub.capture("fuel_point", 20.0)
        self.board.fuel_wire_on = False
        self.settle()
        self.assertIsNone(self.hub.engine()["fuel_pct"])

    def test_capturing_with_the_key_off_says_so(self):
        self.board.supply = 0.0
        self.settle(4)
        with self.assertRaisesRegex(ValueError, "key to ON"):
            self.hub.capture("fuel_full")

    def test_trim_from_fully_down_and_fully_up(self):
        self.board.trim_ohm = 5.0; self.settle(); self.hub.capture("trim_down")
        self.board.trim_ohm = 85.0; self.settle(); self.hub.capture("trim_up")
        self.assertAlmostEqual(self.hub.engine()["trim_pct"], 100.0, delta=0.5)
        self.board.trim_ohm = 5.0; self.settle()
        self.assertAlmostEqual(self.hub.engine()["trim_pct"], 0.0, delta=0.5)
        self.board.trim_ohm = 45.0; self.settle()
        self.assertTrue(0 < self.hub.engine()["trim_pct"] < 100)

    def test_oil_from_the_second_converter(self):
        self.board.oil_ohm = 10.0; self.settle(); self.hub.capture("oil_zero")          # key on, engine off
        self.board.oil_ohm = 100.0; self.settle(); self.hub.capture("oil_point", 40.0)  # gauge reads 40 at cruise
        self.board.oil_ohm = 55.0; self.settle()
        self.assertTrue(0 < self.hub.engine()["oil_pressure_psi"] < 40)

    def test_points_survive_a_restart(self):
        self.board.fuel_pct = 100.0; self.settle(); self.hub.capture("fuel_full")
        self.board.fuel_pct = 50.0; self.settle(); self.hub.capture("fuel_point", 50.0)
        again = board_hub(self.tmp.name, self.board)
        for _ in range(200):
            again.sample_once()
        self.assertAlmostEqual(again.engine()["fuel_pct"], 50.0, delta=0.5)

    def test_clearing_the_points(self):
        self.board.fuel_pct = 100.0; self.settle(); self.hub.capture("fuel_full")
        self.hub.capture("tap_clear", device="fuel")
        self.assertEqual(self.hub.status()["tap"]["fuel"]["points"], [])

    def test_switching_to_plain_volts_clears_points_in_the_old_units(self):
        self.board.fuel_pct = 100.0; self.settle(); self.hub.capture("fuel_full")
        self.board.fuel_pct = 50.0; self.settle(); self.hub.capture("fuel_point", 50.0)
        self.hub.capture("tap_ratiometric", 0)
        self.assertEqual(self.hub.status()["tap"]["fuel"]["points"], [])
        self.assertFalse(self.hub.status()["tap"]["ratiometric"])
        self.settle()
        self.hub.capture("fuel_point", 50.0)
        self.assertGreater(self.hub.status()["tap"]["fuel"]["points"][0][0], 1.0)   # volts now, not a share
        self.hub.capture("tap_ratiometric", 0)                                       # no change: keeps them
        self.assertEqual(len(self.hub.status()["tap"]["fuel"]["points"]), 1)

    def test_the_calibration_page_gets_what_it_shows(self):
        self.settle()
        tap = self.hub.status()["tap"]
        self.assertEqual(self.hub.status()["mode"], "tap")
        self.assertAlmostEqual(tap["gauge_v"], 12.6, places=2)
        for name in ("fuel", "trim", "oil", "temp"):
            self.assertIn("ratio", tap[name])
            self.assertIn("note", tap[name])
        self.assertAlmostEqual(self.hub.battery_volts(), 12.7, places=2)


class TestEngineTemperature(unittest.TestCase):
    """The temperature gauge's sender, tapped on J1.6."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.board = GaugeBoard()
        self.hub = board_hub(self.tmp.name, self.board)

    def at(self, temp_f, n=60):
        self.board.temp_f = temp_f
        for _ in range(n):
            self.hub.sample_once()

    def cold_and_warm(self):
        self.at(70.0); self.hub.capture("temp_point", 70.0)      # sat overnight: the air temperature
        self.at(160.0); self.hub.capture("temp_point", 160.0)    # warmed up, off the analog gauge

    def test_a_cold_and_a_warm_point_calibrate_the_whole_gauge_overheat_included(self):
        self.cold_and_warm()
        for temp in (90.0, 130.0, 180.0, 200.0, 220.0, 240.0):
            with self.subTest(temp=temp):
                self.at(temp)
                self.assertAlmostEqual(self.hub.engine()["coolant_f"], temp, delta=2.0)
        self.assertEqual(self.hub.status()["tap"]["temp"]["method"], "sender")

    def test_a_different_make_of_sender_still_reads_an_overheat_right(self):
        # A dual-station sender (about half the ohms) on a flatter curve, and a different gauge:
        # the scale folds into the fit, and B 3700 against the assumed 3950 costs a few degrees.
        self.board.temp_beta, self.board.temp_r140, self.board.temp_r_th = 3700.0, 70.0, 60.0
        self.cold_and_warm()
        self.at(230.0)
        self.assertAlmostEqual(self.hub.engine()["coolant_f"], 230.0, delta=5.0)

    def test_straight_lines_would_have_read_that_overheat_low(self):
        self.cold_and_warm()
        self.at(230.0)
        points = sender_tap.clean_points(self.hub.cal.get("tap", "temp_points"))
        ratio = self.hub.readings()["temp_ratio"]
        self.assertLess(sender_tap.table_value(ratio, points), 230.0 - 10)
        self.assertAlmostEqual(sender_tap.engine_temp(ratio, points)[0], 230.0, delta=2.0)

    def test_wide_points_off_the_published_curve_choose_their_own(self):
        self.board.temp_beta = 3400.0
        for temp in (60.0, 125.0, 190.0):
            self.at(temp); self.hub.capture("temp_point", temp)
        how = self.hub.status()["tap"]["temp"]
        self.assertEqual(how["method"], "sender")
        self.assertAlmostEqual(how["beta_k"], 3400.0, delta=100.0)
        self.at(225.0)
        self.assertAlmostEqual(self.hub.engine()["coolant_f"], 225.0, delta=3.0)

    def test_points_no_sender_curve_fits_fall_back_to_straight_lines_and_say_so(self):
        # Hotter reading as a higher voltage: not a thermistor to ground (a tap on the wrong terminal).
        value, how = sender_tap.engine_temp(0.45, [[0.3, 70.0], [0.5, 120.0], [0.6, 180.0]])
        self.assertEqual(how["method"], "table")
        self.assertIn("overheat", how["note"])
        self.assertTrue(70.0 < value < 180.0)

    def test_nothing_until_two_points_far_enough_apart(self):
        self.at(70.0); self.hub.capture("temp_point", 70.0)
        self.assertIsNone(self.hub.engine()["coolant_f"])
        self.at(90.0); self.hub.capture("temp_point", 90.0)
        self.assertIsNone(self.hub.engine()["coolant_f"])
        self.assertIn("too close", self.hub.status()["tap"]["temp"]["note"])

    def test_a_tap_wire_that_falls_off_is_no_reading_not_boiling(self):
        self.cold_and_warm()
        self.board.temp_wire_on = False
        self.at(160.0)
        self.assertIsNone(self.hub.engine()["coolant_f"])

    def test_the_gauge_sender_beats_the_clamp_on_probe_which_fills_in_with_the_key_off(self):
        probe = OneProbe(150.0)             # the outside of the housing reads low
        self.hub = board_hub(self.tmp.name, self.board, w1=probe)
        self.hub.sample_probes()
        self.cold_and_warm()
        self.at(170.0)
        self.assertAlmostEqual(self.hub.engine()["coolant_f"], 170.0, delta=2.0)
        self.board.supply = 0.0             # key off: the gauges are dark
        self.at(170.0, n=4)
        self.assertEqual(self.hub.engine()["coolant_f"], 150)

    def test_the_probe_offset_is_refused_while_the_gauge_is_the_source(self):
        self.cold_and_warm()
        with self.assertRaisesRegex(ValueError, "temperature point"):
            self.hub.capture("engine_temp_offset", 175.0)

    def test_clearing_and_units(self):
        self.at(70.0)
        self.assertIn("70 F", self.hub.capture("temp_point", 70.0))
        self.hub.capture("tap_clear", device="temp")
        self.assertEqual(self.hub.status()["tap"]["temp"]["points"], [])
        with self.assertRaisesRegex(ValueError, "from 32 to 260"):
            self.hub.capture("temp_point", 400.0)


class TestCapturesWaitForTheReadingToSettle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.board = GaugeBoard()
        self.t = [0.0]
        s = Settings(sensors="real", sender_wiring="tap", oil_sender=True)
        self.hub = SensorHub(s, Calibration(Path(self.tmp.name) / "calibration.json"), adc=self.board,
                             tach=None, clock=lambda: self.t[0])

    def run_for(self, seconds):
        for _ in range(int(seconds / 0.25)):
            self.t[0] += 0.25
            self.hub.sample_once()

    def test_a_reading_still_gliding_to_a_new_level_is_refused(self):
        self.board.fuel_pct = 100.0
        self.run_for(30)
        self.hub.capture("fuel_full")
        self.board.fuel_pct = 50.0
        self.run_for(8)                                  # fuel is smoothed against slosh: not there yet
        with self.assertRaisesRegex(ValueError, "settling"):
            self.hub.capture("fuel_point", 50.0)
        self.run_for(40)
        self.hub.capture("fuel_point", 50.0)             # settled: accepted
        self.board.fuel_pct = 12.0
        self.run_for(60)
        self.assertAlmostEqual(self.hub.engine()["fuel_pct"], 12.0, delta=0.5)

    def test_right_after_key_on_is_fine_at_once(self):
        # Key off clears the smoothing; the first reading after key on is the exact current value.
        self.board.supply = 0.0
        self.run_for(2)
        self.board.supply, self.board.fuel_pct = 12.5, 100.0
        self.run_for(0.5)
        self.hub.capture("fuel_full")


class TestBoardPair(unittest.TestCase):
    def test_inputs_four_to_seven_are_the_second_converter(self):
        first, second = mock.Mock(), mock.Mock()
        first.read_volts.return_value, second.read_volts.return_value = 1.0, 2.0
        pair = ADS1115Pair(first, second)
        self.assertEqual(pair.read_volts(3), 1.0)
        self.assertEqual(pair.read_volts(4), 2.0)
        second.read_volts.assert_called_with(0)

    def test_a_missing_second_converter_is_an_input_error_not_a_crash(self):
        with self.assertRaises(OSError):
            ADS1115Pair(mock.Mock()).read_volts(5)


if __name__ == "__main__":
    unittest.main()
