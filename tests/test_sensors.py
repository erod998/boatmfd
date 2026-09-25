"""Tests for the real-sensor layer: ADC math, senders, tach, temperature probes, fuel burn,
calibration and the PWM light driver. All hardware is faked (no I2C, GPIO or 1-Wire needed).

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import unittest
from pathlib import Path

from app.gps import NoFixGPS
from app.lighting import LightingController, Pca9685RgbDriver
from app.media import OfflineMedia
from app.sensors import (
    ADS1115, Calibration, SensorHub, TachCounter, W1Probes, estimate_gph, linear_percent,
    make_sensor_sources, ohms_from_divider,
)
from app.state import BoatInfo, EngineInfo, Settings

RAIL, R_REF = 3.3, 240.0


def volts_for_ohms(ohms):
    return RAIL * ohms / (R_REF + ohms)


class FakeAdc:
    def __init__(self):
        self.volts = {}

    def set_ohms(self, channel, ohms):
        self.volts[channel] = volts_for_ohms(ohms)

    def read_volts(self, channel):
        if self.volts.get(channel) is None:
            raise OSError("no device")
        return self.volts[channel]


class FakeTach:
    def __init__(self, rpm=0.0):
        self._rpm = rpm

    def rpm(self):
        return self._rpm


class FakeAdsBus:
    def __init__(self, raw_by_channel):
        self.raw = raw_by_channel
        self.configs = []
        self._channel = None

    def write_i2c_block_data(self, address, register, data):
        if register == 0x01:
            config = (data[0] << 8) | data[1]
            self.configs.append(config)
            self._channel = ((config >> 12) & 7) - 4

    def read_i2c_block_data(self, address, register, length):
        if register == 0x01:
            return [0x80, 0x83]  # conversion-complete bit set
        return list(self.raw[self._channel].to_bytes(2, "big", signed=True))


class FakePwmBus:
    def __init__(self):
        self.writes = []

    def read_i2c_block_data(self, address, register, length):
        return [0x00]

    def write_i2c_block_data(self, address, register, data):
        self.writes.append((register, list(data)))


def make_hub(tmp, **settings_kwargs):
    settings = Settings(**settings_kwargs)
    cal = Calibration(Path(tmp) / "calibration.json")
    adc, tach = FakeAdc(), FakeTach()
    hub = SensorHub(settings, cal, adc, tach)
    return hub, adc, tach, cal


class TestConversions(unittest.TestCase):
    def test_divider_roundtrip_and_open_circuit(self):
        for ohms in (0.0, 33.0, 100.0, 240.0, 400.0):
            with self.subTest(ohms=ohms):
                self.assertAlmostEqual(ohms_from_divider(volts_for_ohms(ohms), RAIL, R_REF), ohms, places=6)
        self.assertIsNone(ohms_from_divider(3.3, RAIL, R_REF))  # disconnected sender floats to the rail

    def test_linear_percent_either_direction(self):
        self.assertEqual(linear_percent(240, 240, 33), 0.0)      # fuel: empty 240 ohm ...
        self.assertEqual(linear_percent(33, 240, 33), 100.0)     # ... full 33 ohm
        self.assertAlmostEqual(linear_percent(136.5, 240, 33), 50.0)
        self.assertEqual(linear_percent(500, 240, 33), 0.0)      # clamped
        self.assertEqual(linear_percent(10, 0, 90), 11.11111111111111)
        self.assertIsNone(linear_percent(5, 7, 7))

    def test_ads1115_config_word_and_scaling(self):
        bus = FakeAdsBus({0: 8000, 2: -16384})
        adc = ADS1115(bus, sleep=lambda s: None)
        self.assertAlmostEqual(adc.read_volts(0), 1.0)
        self.assertEqual(bus.configs[-1], 0xC383)
        self.assertAlmostEqual(adc.read_volts(2), -2.048)
        self.assertEqual(bus.configs[-1], 0xE383)


class TestTach(unittest.TestCase):
    def feed(self, tach, rpm, seconds=3.0, ppr=2, ring_after_s=None, start_ns=10_000_000_000):
        period = 60.0 / (rpm * ppr)
        t, end = 0.0, seconds
        while t < end:
            tach.edge(start_ns + int(t * 1e9))
            if ring_after_s is not None:
                tach.edge(start_ns + int((t + ring_after_s) * 1e9))  # ring-down edge after the spark
            t += period

    def test_rpm_from_ignition_pulses(self):
        for rpm in (800, 2500, 4500):
            with self.subTest(rpm=rpm):
                tach = TachCounter(ppr=2)
                self.feed(tach, rpm)
                self.assertAlmostEqual(tach.rpm(), rpm, delta=rpm * 0.02)

    def test_ring_down_edges_are_ignored(self):
        cases = ((800, 0.006), (2500, 0.0015), (4500, 0.0018))  # (rpm, delay of the spurious edge after the spark)
        for rpm, ring in cases:
            with self.subTest(rpm=rpm):
                tach = TachCounter(ppr=2)
                self.feed(tach, rpm, ring_after_s=ring)
                self.assertAlmostEqual(tach.rpm(), rpm, delta=rpm * 0.03)

    def test_goes_to_zero_when_pulses_stop(self):
        now = [0.0]
        tach = TachCounter(ppr=2, clock=lambda: now[0])
        for i in range(8):
            now[0] = i * 0.02
            tach.edge(int(i * 0.02 * 1e9))
        self.assertGreater(tach.rpm(), 0)
        now[0] += 3.0
        self.assertEqual(tach.rpm(), 0.0)

    def test_restart_after_stop_recovers(self):
        now = [0.0]
        tach = TachCounter(ppr=2, clock=lambda: now[0])
        for i in range(5):
            now[0] = i * 0.02
            tach.edge(int(i * 0.02 * 1e9))
        now[0] += 5.0
        base = int(now[0] * 1e9)
        for i in range(6):
            now[0] += 0.02
            tach.edge(base + int((i + 1) * 0.02 * 1e9))
        self.assertAlmostEqual(tach.rpm(), 1500, delta=50)


class TestFuelBurnEstimate(unittest.TestCase):
    def test_curve_shape(self):
        self.assertEqual(estimate_gph(0, 135, 4600), 0.0)
        self.assertEqual(estimate_gph(400, 135, 4600), 0.0)
        idle, cruise, wot = (estimate_gph(r, 135, 4600) for r in (750, 3000, 4600))
        self.assertTrue(0.5 < idle < 1.2)
        self.assertTrue(2.5 < cruise < 5.0)
        self.assertTrue(8.0 < wot < 13.0)
        self.assertLess(idle, cruise)
        self.assertLess(cruise, wot)


class TestSensorHub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_fuel_battery_and_uncalibrated_trim(self):
        hub, adc, tach, cal = make_hub(self.tmp.name)
        adc.set_ohms(0, 136.5)           # fuel, halfway between 240 and 33
        adc.set_ohms(1, 40.0)            # trim sender, not yet calibrated
        adc.volts[2] = 13.8 / 5.7        # battery divider (47k / 10k) at 13.8 V
        hub.sample_once()
        engine = hub.engine()
        self.assertAlmostEqual(engine["fuel_pct"], 50.0, places=1)
        self.assertIsNone(engine["trim_pct"])
        self.assertAlmostEqual(hub.battery_volts(), 13.8, places=2)

    def test_trim_calibration_capture(self):
        hub, adc, tach, cal = make_hub(self.tmp.name)
        adc.set_ohms(1, 5.0)
        hub.sample_once()
        hub.capture("trim_down")
        adc.set_ohms(1, 85.0)
        hub._smoothed.clear()
        hub.sample_once()
        hub.capture("trim_up")
        adc.set_ohms(1, 45.0)
        hub._smoothed.clear()
        hub.sample_once()
        self.assertAlmostEqual(hub.engine()["trim_pct"], 50.0, delta=0.5)
        self.assertEqual(Calibration(Path(self.tmp.name) / "calibration.json").get("trim", "down_ohm"), 5.0)

    def test_a_failing_read_shows_no_data_instead_of_freezing_and_recovers(self):
        # One exception used to end the sampling thread and leave the last readings on the gauges
        # as if they were live.
        import time as _time
        from unittest import mock
        hub, adc, tach, cal = make_hub(self.tmp.name)
        hub.SAMPLE_S = hub.PROBE_S = 0.01
        adc.set_ohms(0, 136.5)
        broken = mock.Mock(side_effect=OSError("GPIO fault"))
        with mock.patch("builtins.print"):
            hub.start()
            self.addCleanup(hub.stop)
            deadline = _time.monotonic() + 3
            while hub.engine()["fuel_pct"] is None and _time.monotonic() < deadline:
                _time.sleep(0.01)
            self.assertIsNotNone(hub.engine()["fuel_pct"])
            real_rpm, tach.rpm = tach.rpm, broken
            deadline = _time.monotonic() + 3
            while hub.engine()["fuel_pct"] is not None and _time.monotonic() < deadline:
                _time.sleep(0.01)
            self.assertIsNone(hub.engine()["fuel_pct"], "a failing read left the old value on the gauge")
            tach.rpm = real_rpm
            deadline = _time.monotonic() + 3
            while hub.engine()["fuel_pct"] is None and _time.monotonic() < deadline:
                _time.sleep(0.01)
            self.assertIsNotNone(hub.engine()["fuel_pct"], "the sampling thread did not survive")

    def test_disconnected_senders_and_missing_adc_show_no_data(self):
        hub, adc, tach, cal = make_hub(self.tmp.name)
        adc.volts[0] = 3.3               # open circuit floats to the rail
        hub.sample_once()
        self.assertIsNone(hub.engine()["fuel_pct"])
        self.assertIsNone(hub.battery_volts())  # channel 2 raises OSError

        bare = SensorHub(Settings(), Calibration(Path(self.tmp.name) / "c2.json"), adc=None, tach=None)
        bare.sample_once()
        engine = bare.engine()
        self.assertTrue(all(engine[k] is None for k in ("rpm", "fuel_pct", "trim_pct", "oil_pressure_psi", "fuel_gph", "coolant_f")))

    def test_battery_and_tach_calibration(self):
        hub, adc, tach, cal = make_hub(self.tmp.name)
        adc.volts[2] = 13.8 / 5.7 * 0.97   # uncalibrated divider reads 3% low
        tach._rpm = 2900.0
        hub.sample_once()
        hub.capture("battery", 13.8)
        hub.capture("tach", 3000)
        self.assertAlmostEqual(hub.battery_volts(), 13.8, places=2)
        self.assertEqual(hub.engine()["rpm"], 3000)
        with self.assertRaises(ValueError):
            hub.capture("battery")
        with self.assertRaises(ValueError):
            hub.capture("bogus")

    def test_oil_sender_only_when_enabled(self):
        hub, adc, tach, cal = make_hub(self.tmp.name, oil_sender=True)
        adc.set_ohms(3, 95.0)            # halfway between 10 and 180 ohm = 40 psi
        hub.sample_once()
        self.assertAlmostEqual(hub.engine()["oil_pressure_psi"], 40.0, delta=0.5)
        off_hub, off_adc, _, _ = make_hub(self.tmp.name + "/x")
        off_adc.set_ohms(3, 95.0)
        off_hub.sample_once()
        self.assertIsNone(off_hub.engine()["oil_pressure_psi"])

    def test_fuel_used_accumulates_and_fill_up_rescales_estimate(self):
        now = [1000.0]
        settings = Settings()
        cal = Calibration(Path(self.tmp.name) / "calibration.json")
        tach = FakeTach(3000.0)
        hub = SensorHub(settings, cal, FakeAdc(), tach, clock=lambda: now[0])
        for _ in range(61):              # one hour at 3000 RPM, sampled once a minute
            hub.sample_once()
            now[0] += 60.0
        expected_gph = estimate_gph(3000, settings.engine_hp, settings.engine_wot_rpm)
        self.assertAlmostEqual(hub.status()["fuel_used_gal"], expected_gph, delta=0.1)

        estimated = hub.status()["fuel_used_gal"]
        message = hub.capture("fuel_fill", estimated * 1.2)   # the pump says we really burned 20% more
        self.assertIn("scaled", message)
        self.assertAlmostEqual(cal.get("fuel_burn", "scale"), 1.2, places=2)
        self.assertEqual(hub.status()["fuel_used_gal"], 0.0)
        self.assertAlmostEqual(hub.engine()["fuel_gph"], expected_gph * 1.2, delta=0.1)

        self.assertIn("too little", hub.capture("fuel_fill", 5))  # nothing burned since the last fill
        self.assertAlmostEqual(cal.get("fuel_burn", "scale"), 1.2, places=2)


class TestTemperatureProbes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w1 = Path(self.tmp.name) / "w1"
        self.w1.mkdir()

    def add_probe(self, name, celsius_milli=None, w1_slave=None):
        folder = self.w1 / name
        folder.mkdir()
        if celsius_milli is not None:
            (folder / "temperature").write_text(f"{celsius_milli}\n")
        if w1_slave is not None:
            (folder / "w1_slave").write_text(w1_slave)

    def test_reads_fahrenheit_and_rejects_bad_values(self):
        self.add_probe("28-aaa", 71000)                                    # 71 C = 159.8 F
        self.add_probe("28-bbb", 85000)                                    # power-on default
        self.add_probe("28-ccc", w1_slave="72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=23125\n")
        self.add_probe("28-ddd", w1_slave="72 01 4b 46 7f ff 0e 10 57 : crc=57 NO\n72 01 4b 46 7f ff 0e 10 57 t=23125\n")
        (self.w1 / "w1_bus_master1").mkdir()
        probes = W1Probes(self.w1)
        self.assertEqual(probes.devices(), ["28-aaa", "28-bbb", "28-ccc", "28-ddd"])
        self.assertAlmostEqual(probes.read_f("28-aaa"), 159.8, places=1)
        self.assertIsNone(probes.read_f("28-bbb"))
        self.assertAlmostEqual(probes.read_f("28-ccc"), 73.625, places=2)
        self.assertIsNone(probes.read_f("28-ddd"))
        self.assertIsNone(probes.read_f("28-missing"))
        self.assertEqual(W1Probes(self.w1 / "nope").devices(), [])

    def hub(self):
        cal = Calibration(Path(self.tmp.name) / "calibration.json")
        hub = SensorHub(Settings(), cal, FakeAdc(), FakeTach(), W1Probes(self.w1))
        hub.sample_probes()
        return hub, cal

    def test_single_probe_is_the_engine_and_water_needs_assignment(self):
        self.add_probe("28-aaa", 80000)                                    # 176 F
        hub, cal = self.hub()
        self.assertEqual(hub.engine()["coolant_f"], 176)
        self.assertIsNone(hub.water_temp_f())

    def test_assigning_probes_and_offset(self):
        self.add_probe("28-aaa", 80000)                                    # 176 F
        self.add_probe("28-bbb", 20000)                                    # 68 F
        hub, cal = self.hub()
        hub.capture("probe_engine", device="28-aaa")
        hub.capture("probe_water", device="28-bbb")
        self.assertEqual(hub.engine()["coolant_f"], 176)
        self.assertEqual(round(hub.water_temp_f()), 68)
        hub.capture("engine_temp_offset", 180.0)                           # infrared thermometer says 180 F
        self.assertEqual(hub.engine()["coolant_f"], 180)
        hub.capture("probe_water", device="")
        self.assertIsNone(hub.water_temp_f())

    def test_auto_engine_probe_skips_the_water_probe(self):
        self.add_probe("28-aaa", 20000)
        self.add_probe("28-bbb", 80000)
        hub, cal = self.hub()
        hub.capture("probe_water", device="28-aaa")
        self.assertEqual(hub.engine()["coolant_f"], 176)


class TestCalibrationFile(unittest.TestCase):
    def test_persists_and_ignores_unknown_or_corrupt_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "calibration.json"
            cal = Calibration(path)
            self.assertEqual(cal.get("fuel", "empty_ohm"), 240.0)
            cal.set("fuel", "full_ohm", 31.5)
            self.assertEqual(Calibration(path).get("fuel", "full_ohm"), 31.5)
            path.write_text('{"fuel": {"empty_ohm": 250, "bogus": 1}, "nope": {}}')
            reloaded = Calibration(path)
            self.assertEqual(reloaded.get("fuel", "empty_ohm"), 250)
            self.assertNotIn("bogus", reloaded.snapshot()["fuel"])
            path.write_text("not json")
            self.assertEqual(Calibration(path).get("fuel", "empty_ohm"), 240.0)


class TestPwmRgbDriver(unittest.TestCase):
    def test_setup_and_channel_writes(self):
        bus = FakePwmBus()
        driver = Pca9685RgbDriver(bus=bus, address=0x40, channels=(0, 1, 2), freq_hz=1000, gamma=2.2, sleep=lambda s: None)
        self.assertIn((0xFE, [5]), bus.writes)                       # prescale for 1 kHz
        bus.writes.clear()
        driver.show([(255, 0, 128)])
        writes = dict(bus.writes)
        self.assertEqual(writes[0x06], [0, 0x10, 0, 0])               # red full on
        self.assertEqual(writes[0x0A], [0, 0, 0, 0x10])               # green full off
        blue = writes[0x0E]
        self.assertEqual(blue[:2], [0, 0])
        self.assertEqual((blue[3] << 8) | blue[2], round((128 / 255) ** 2.2 * 4095))

    def test_wakes_a_chip_that_has_just_powered_up(self):
        # Power-up MODE1 is 0x11: SLEEP set. Left set, the oscillator is off and every output dark.
        class FreshChip(FakePwmBus):
            mode1 = 0x11

            def read_i2c_block_data(self, address, register, length):
                return [self.mode1]

            def write_i2c_block_data(self, address, register, data):
                super().write_i2c_block_data(address, register, data)
                if register == 0x00:
                    self.mode1 = data[0] & 0x7F   # RESTART clears itself

        chip = FreshChip()
        Pca9685RgbDriver(bus=chip, sleep=lambda s: None)
        self.assertFalse(chip.mode1 & 0x10, "left asleep")
        self.assertTrue(chip.mode1 & 0x20, "auto-increment off")
        self.assertIn((0xFE, [5]), chip.writes)

    def test_works_with_the_lighting_controller(self):
        bus = FakePwmBus()
        driver = Pca9685RgbDriver(bus=bus, sleep=lambda s: None)
        lights = LightingController(1, driver=driver)
        bus.writes.clear()
        lights.set_power(True)
        lights.set_solid_color(0, 255, 0)
        lights.set_brightness(1.0)
        lights.tick()
        self.assertEqual(dict(bus.writes)[0x0A], [0, 0x10, 0, 0])     # green full on
        lights.set_power(False)
        lights.tick()
        self.assertEqual(dict(bus.writes)[0x0A], [0, 0, 0, 0x10])     # everything off


class TestLedBoardBus(unittest.TestCase):
    def test_the_pwm_driver_opens_the_led_boards_own_i2c_bus(self):
        # The sensor board puts the LED board on a bus of its own (i2c-gpio on GPIO5/6), apart
        # from the converters on bus 1.
        from unittest import mock

        from app import main
        with mock.patch.object(main, "Pca9685RgbDriver") as driver:
            main.make_led_driver(Settings(led_driver="pwm", i2c_bus=1, led_i2c_bus=7))
        self.assertEqual(driver.call_args.kwargs["bus_number"], 7)


class TestNoFakeDataWhenHardwareIsConfigured(unittest.TestCase):
    def test_no_fix_gps_and_offline_media(self):
        self.assertFalse(NoFixGPS().read().has_fix)
        media = OfflineMedia()
        self.assertFalse(media.state()["connected"])
        with self.assertRaises(RuntimeError):
            media.handle("play")

    def test_sensor_factory_uses_simulators_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, boat, hub = make_sensor_sources(Settings(sensors="sim"), tmp)
            self.assertIsInstance(engine, EngineInfo)
            self.assertIsInstance(boat, BoatInfo)
            self.assertIsNone(hub)


if __name__ == "__main__":
    unittest.main()
