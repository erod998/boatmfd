"""Tests for NMEA 2000 fuel data (fuel-flow sensors and tank-level adapters) and how the sensor hub uses it.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import time
import unittest
from pathlib import Path

from app.media import FusionMedia, OfflineMedia, SimulatedMedia, make_media_source
from app.n2k import N2kNode, encode_name, fast_packet_frames, make_can_id
from app.n2k_engine import (
    LITERS_PER_GALLON, N2kFuelData, PGN_ENGINE_DYNAMIC, PGN_FLUID_LEVEL, parse_engine_dynamic, parse_fluid_level,
)
from app.sensors import Calibration, SensorHub
from app.state import Settings, estimate_gph
from tests.test_sensors import FakeAdc, FakeTach

try:
    import can
except ImportError:  # pragma: no cover
    can = None


def engine_dynamic_payload(fuel_rate_raw, instance=0):
    payload = bytearray(26)
    payload[0] = instance
    payload[9:11] = fuel_rate_raw.to_bytes(2, "little", signed=True)
    return bytes(payload)


def fluid_level_payload(level_pct, capacity_l, instance=0, tank_type=0):
    level_raw = 0x7FFF if level_pct is None else round(level_pct / 0.004)
    capacity_raw = 0xFFFFFFFF if capacity_l is None else round(capacity_l / 0.1)
    return bytes([(tank_type << 4) | instance]) + level_raw.to_bytes(2, "little", signed=True) + capacity_raw.to_bytes(4, "little") + b"\xff"


class TestDecoders(unittest.TestCase):
    def test_fuel_rate_from_engine_dynamic(self):
        parsed = parse_engine_dynamic(engine_dynamic_payload(320, instance=1))     # 32.0 L/h
        self.assertEqual(parsed["instance"], 1)
        self.assertAlmostEqual(parsed["fuel_gph"], 32.0 / LITERS_PER_GALLON, places=3)
        self.assertAlmostEqual(parsed["fuel_gph"], 8.454, places=2)
        self.assertEqual(parse_engine_dynamic(engine_dynamic_payload(0))["fuel_gph"], 0.0)

    def test_not_available_error_and_short_payloads_are_none(self):
        for raw in (0x7FFF, 0x7FFE, 0x7FFD, -5):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_engine_dynamic(engine_dynamic_payload(raw))["fuel_gph"])
        self.assertIsNone(parse_engine_dynamic(bytes(8)))

    def test_fluid_level(self):
        parsed = parse_fluid_level(fluid_level_payload(62.5, 113.6, instance=2, tank_type=0))
        self.assertEqual((parsed["instance"], parsed["type"]), (2, 0))
        self.assertAlmostEqual(parsed["level_pct"], 62.5, places=2)
        self.assertAlmostEqual(parsed["capacity_gal"], 113.6 / LITERS_PER_GALLON, places=2)
        self.assertEqual(parse_fluid_level(fluid_level_payload(50, 60, tank_type=1))["type"], 1)   # a water tank

    def test_level_not_available(self):
        parsed = parse_fluid_level(fluid_level_payload(None, None))
        self.assertIsNone(parsed["level_pct"])
        self.assertIsNone(parsed["capacity_gal"])
        self.assertIsNone(parse_fluid_level(bytes(4)))
        self.assertEqual(parse_fluid_level(fluid_level_payload(100, 60))["level_pct"], 100.0)


class FakeNode:
    def __init__(self):
        self.listeners, self.subscribed = [], set()

    def add_listener(self, fn):
        self.listeners.append(fn)

    def subscribe(self, *pgns):
        self.subscribed.update(pgns)

    def send(self, pgn, source, payload):
        for fn in self.listeners:
            fn(pgn, source, payload)


class TestFuelData(unittest.TestCase):
    def make(self, **kwargs):
        self.now = [100.0]
        self.node = FakeNode()
        return N2kFuelData(self.node, clock=lambda: self.now[0], **kwargs)

    def test_subscribes_to_single_frame_level_messages(self):
        self.make()
        self.assertIn(PGN_FLUID_LEVEL, self.node.subscribed)

    def test_values_and_staleness(self):
        data = self.make()
        self.assertIsNone(data.fuel_gph())
        self.node.send(PGN_ENGINE_DYNAMIC, 35, engine_dynamic_payload(189))     # 18.9 L/h
        self.node.send(PGN_FLUID_LEVEL, 36, fluid_level_payload(40.0, 113.6))
        self.assertAlmostEqual(data.fuel_gph(), 18.9 / LITERS_PER_GALLON, places=3)
        self.assertAlmostEqual(data.fuel_level_pct(), 40.0, places=2)
        self.now[0] += 6.0                                                       # the sensors go quiet
        self.assertIsNone(data.fuel_gph())
        self.assertIsNone(data.fuel_level_pct())

    def test_only_the_configured_engine_and_fuel_tank_count(self):
        data = self.make(engine_instance=0, tank_instance=0)
        self.node.send(PGN_ENGINE_DYNAMIC, 35, engine_dynamic_payload(200, instance=1))       # the other engine
        self.node.send(PGN_FLUID_LEVEL, 36, fluid_level_payload(50, 60, instance=0, tank_type=1))   # a water tank
        self.node.send(PGN_FLUID_LEVEL, 36, fluid_level_payload(70, 60, instance=1, tank_type=0))   # fuel tank 2
        self.assertIsNone(data.fuel_gph())
        self.assertIsNone(data.fuel_level_pct())
        status = data.status()                        # ...but everything heard is listed for troubleshooting
        self.assertEqual(len(status["fuel_flow"]), 1)
        self.assertEqual(sorted(t["type"] for t in status["tank_levels"]), ["fuel", "water"])
        second = self.make(engine_instance=1, tank_instance=1)
        self.node.send(PGN_ENGINE_DYNAMIC, 35, engine_dynamic_payload(200, instance=1))
        self.node.send(PGN_FLUID_LEVEL, 36, fluid_level_payload(70, 60, instance=1, tank_type=0))
        self.assertIsNotNone(second.fuel_gph())
        self.assertAlmostEqual(second.fuel_level_pct(), 70.0, places=2)

    def test_not_available_values_are_never_used(self):
        data = self.make()
        self.node.send(PGN_ENGINE_DYNAMIC, 35, engine_dynamic_payload(0x7FFF))
        self.node.send(PGN_FLUID_LEVEL, 36, fluid_level_payload(None, None))
        self.assertIsNone(data.fuel_gph())
        self.assertIsNone(data.fuel_level_pct())


class FakeFuel:
    """Stands in for N2kEngineData with only fuel values set; every other reading is silent."""

    def __init__(self, gph=None, level=None):
        self.gph, self.level = gph, level

    def fuel_gph(self):
        return self.gph

    def fuel_level_pct(self):
        return self.level

    def rpm(self):
        return None

    trim_pct = oil_pressure_psi = engine_temp_f = alternator_v = rpm

    def heard_engine(self):
        return False

    def raw(self):
        return {"fuel_gph": self.gph, "fuel_level_pct": self.level}

    def status(self):
        return {"fuel_flow": [], "tank_levels": []}


class TestHubFuelPriority(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def hub(self, fuel, tach_rpm=3000.0, **settings_kwargs):
        cal = Calibration(Path(self.tmp.name) / "calibration.json")
        adc, tach = FakeAdc(), FakeTach(tach_rpm)
        return SensorHub(Settings(**settings_kwargs), cal, adc, tach, n2k=fuel), adc, cal

    def test_measured_flow_beats_the_rpm_estimate(self):
        hub, adc, cal = self.hub(FakeFuel(gph=4.2))
        hub.sample_once()
        engine = hub.engine()
        self.assertEqual(engine["fuel_gph"], 4.2)
        self.assertFalse(engine["fuel_gph_est"])

    def test_falls_back_to_the_estimate_when_the_sensor_goes_quiet(self):
        fuel = FakeFuel(gph=4.2)
        hub, adc, cal = self.hub(fuel)
        hub.sample_once()
        fuel.gph = None
        engine = hub.engine()
        self.assertTrue(engine["fuel_gph_est"])
        settings = Settings()
        self.assertAlmostEqual(engine["fuel_gph"], estimate_gph(3000, settings.engine_hp, settings.engine_wot_rpm), delta=0.1)

    def test_no_sensor_and_no_tach_means_no_data(self):
        hub, adc, cal = self.hub(FakeFuel(), tach_rpm=None)
        hub.tach = None
        hub.sample_once()
        self.assertIsNone(hub.engine()["fuel_gph"])

    def test_measured_flow_can_be_corrected_by_a_fill_up(self):
        fuel = FakeFuel(gph=5.0)
        now = [0.0]
        cal = Calibration(Path(self.tmp.name) / "calibration.json")
        hub = SensorHub(Settings(), cal, FakeAdc(), FakeTach(3000.0), n2k=fuel, clock=lambda: now[0])
        for _ in range(61):                                                  # an hour at 5 GPH
            hub.sample_once()
            now[0] += 60.0
        self.assertAlmostEqual(hub.status()["fuel_used_gal"], 5.0, delta=0.1)
        hub.capture("fuel_fill", 5.5)                                        # the pump says 10% more
        self.assertAlmostEqual(hub.engine()["fuel_gph"], 5.5, delta=0.06)

    def test_n2k_tank_level_beats_the_analog_sender(self):
        hub, adc, cal = self.hub(FakeFuel(level=73.0))
        adc.set_ohms(0, 136.5)                                               # the analog sender says 50%
        hub.sample_once()
        self.assertEqual(hub.engine()["fuel_pct"], 73.0)
        hub.n2k.level = None                                                 # adapter goes quiet: use the sender again
        self.assertAlmostEqual(hub.engine()["fuel_pct"], 50.0, places=1)

    def test_analog_fuel_sender_can_be_switched_off(self):
        hub, adc, cal = self.hub(FakeFuel(), fuel_sender=False)
        adc.set_ohms(0, 136.5)                                               # a floating input must never become a fuel level
        hub.sample_once()
        self.assertIsNone(hub.engine()["fuel_pct"])
        self.assertFalse(hub.status()["fuel_sender_enabled"])
        with self.assertRaises(ValueError):
            hub.capture("fuel_empty")
        hub.n2k.level = 61.0
        self.assertEqual(hub.engine()["fuel_pct"], 61.0)


class TestListenersAndFactories(unittest.TestCase):
    def test_a_failing_listener_does_not_stop_the_others(self):
        node = N2kNode(bus=None, name=1)
        heard = []
        node.add_listener(lambda pgn, src, payload: 1 / 0)
        node.add_listener(lambda pgn, src, payload: heard.append((pgn, src)))
        node._dispatch(127489, 35, b"\x00")
        self.assertEqual(heard, [(127489, 35)])

    def test_media_source_choice(self):
        self.assertIsInstance(make_media_source(Settings(can_channel=""), None), SimulatedMedia)
        self.assertIsInstance(make_media_source(Settings(can_channel="can0"), None), OfflineMedia)
        self.assertIsInstance(make_media_source(Settings(can_channel="can0", album_art=False), FakeNode()), FusionMedia)


@unittest.skipUnless(can, "python-can not installed")
class TestOverTheBus(unittest.TestCase):
    """A fake fuel sensor and tank-level adapter on a virtual CAN bus, talking to the real node and decoders."""

    def test_fuel_flow_and_tank_level_arrive_alongside_the_stereo_traffic(self):
        bus_a = can.Bus(channel="fuel-test", interface="virtual")
        sensor = can.Bus(channel="fuel-test", interface="virtual")
        node = N2kNode(bus_a, encode_name(9), address=41)
        fuel = N2kFuelData(node)
        node.start()
        try:
            time.sleep(0.4)
            payload = engine_dynamic_payload(250)                                # 25.0 L/h from device 0x23, fast-packet
            for frame in fast_packet_frames(3, payload):
                sensor.send(can.Message(arbitration_id=make_can_id(2, PGN_ENGINE_DYNAMIC, 0x23), is_extended_id=True, data=frame))
            sensor.send(can.Message(arbitration_id=make_can_id(6, PGN_FLUID_LEVEL, 0x24), is_extended_id=True,
                                    data=fluid_level_payload(55.0, 90.0)))       # single frame from device 0x24
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and (fuel.fuel_gph() is None or fuel.fuel_level_pct() is None):
                time.sleep(0.05)
            self.assertAlmostEqual(fuel.fuel_gph(), 25.0 / LITERS_PER_GALLON, places=3)
            self.assertAlmostEqual(fuel.fuel_level_pct(), 55.0, places=2)
            self.assertEqual({d["source"] for d in fuel.status()["fuel_flow"] + fuel.status()["tank_levels"]}, {0x23, 0x24})
        finally:
            node.shutdown()
            sensor.shutdown()


if __name__ == "__main__":
    unittest.main()
