"""Tests for engine data over NMEA 2000 (an engine converter such as the Matsutec CX5003) and how the
sensor hub uses it in BOAT_SENSORS=n2k mode.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import time
import unittest
from pathlib import Path

from app.n2k import N2kNode, encode_name, fast_packet_frames, make_can_id
from app.n2k_engine import (
    LITERS_PER_GALLON, N2kEngineData, PA_PER_PSI, PGN_ENGINE_DYNAMIC, PGN_ENGINE_RAPID, PGN_FLUID_LEVEL,
    parse_engine_dynamic, parse_engine_rapid,
)
from app.sensors import N2K_OIL_FULL_PSI, Calibration, SensorHub, make_sensor_sources
from app.state import Settings
from tests.test_n2k_fuel import FakeNode, fluid_level_payload
from tests.test_sensors import FakeAdc

try:
    import can
except ImportError:  # pragma: no cover
    can = None


def rapid_payload(rpm=None, trim=None, instance=0):
    rpm_raw = 0xFFFF if rpm is None else round(rpm / 0.25)
    trim_raw = 0x7F if trim is None else trim & 0xFF
    return bytes([instance]) + rpm_raw.to_bytes(2, "little") + b"\xff\xff" + bytes([trim_raw]) + b"\xff\xff"


def dynamic_payload(instance=0, oil_psi=None, oil_temp_c=None, engine_temp_c=None, alt_v=None, fuel_lph=None):
    """PGN 127489 with every field 'not available' unless given."""
    p = bytearray(b"\xff" * 26)
    p[0] = instance
    p[7:9] = (0x7FFF).to_bytes(2, "little")     # alternator: signed, not available
    p[9:11] = (0x7FFF).to_bytes(2, "little")    # fuel rate: signed, not available
    if oil_psi is not None:
        p[1:3] = round(oil_psi * PA_PER_PSI / 100).to_bytes(2, "little")
    if oil_temp_c is not None:
        p[3:5] = round((oil_temp_c + 273.15) / 0.1).to_bytes(2, "little")
    if engine_temp_c is not None:
        p[5:7] = round((engine_temp_c + 273.15) / 0.01).to_bytes(2, "little")
    if alt_v is not None:
        p[7:9] = round(alt_v / 0.01).to_bytes(2, "little", signed=True)
    if fuel_lph is not None:
        p[9:11] = round(fuel_lph / 0.1).to_bytes(2, "little", signed=True)
    return bytes(p)


class TestDecoders(unittest.TestCase):
    def test_engine_rapid(self):
        parsed = parse_engine_rapid(rapid_payload(rpm=2400, trim=47, instance=1))
        self.assertEqual(parsed, {"instance": 1, "rpm": 2400.0, "trim_pct": 47})
        self.assertEqual(parse_engine_rapid(rapid_payload(rpm=0, trim=0))["rpm"], 0.0)      # a stopped engine is a real 0, not "no data"
        self.assertEqual(parse_engine_rapid(rapid_payload(trim=-5))["trim_pct"], -5)
        empty = parse_engine_rapid(rapid_payload())
        self.assertIsNone(empty["rpm"])
        self.assertIsNone(empty["trim_pct"])
        self.assertIsNone(parse_engine_rapid(bytes(4)))

    def test_engine_rapid_error_codes(self):
        for code in (0xFFFD, 0xFFFE):
            payload = b"\x00" + code.to_bytes(2, "little") + b"\xff\xff\x7e\xff\xff"
            with self.subTest(code=code):
                parsed = parse_engine_rapid(payload)
                self.assertIsNone(parsed["rpm"])
                self.assertIsNone(parsed["trim_pct"])   # 0x7E is the error code for a signed byte

    def test_engine_dynamic_fields(self):
        parsed = parse_engine_dynamic(dynamic_payload(oil_psi=41, oil_temp_c=60, engine_temp_c=82, alt_v=13.9, fuel_lph=25))
        self.assertAlmostEqual(parsed["oil_pressure_pa"] / PA_PER_PSI, 41, delta=0.02)
        self.assertAlmostEqual(parsed["oil_temp_c"], 60, places=1)
        self.assertAlmostEqual(parsed["engine_temp_c"], 82, places=1)
        self.assertAlmostEqual(parsed["alternator_v"], 13.9, places=2)
        self.assertAlmostEqual(parsed["fuel_gph"], 25 / LITERS_PER_GALLON, places=3)

    def test_engine_dynamic_not_available_and_filler_values(self):
        blank = parse_engine_dynamic(dynamic_payload())
        self.assertTrue(all(v is None for k, v in blank.items() if k != "instance"), blank)
        filler = bytearray(26)                                    # a lazy device that sends zeros for what it doesn't measure
        parsed = parse_engine_dynamic(bytes(filler))
        self.assertEqual(parsed["oil_pressure_pa"], 0.0)          # zero pressure is a valid reading ...
        self.assertIsNone(parsed["engine_temp_c"])                # ... but 0 K and 0 V are not readings
        self.assertIsNone(parsed["oil_temp_c"])
        self.assertIsNone(parsed["alternator_v"])

    def test_short_payloads(self):
        self.assertIsNone(parse_engine_dynamic(bytes(10)))
        partial = parse_engine_dynamic(bytes(11))                 # too short for the later fields, not an error
        self.assertIsNotNone(partial)


class TestEngineData(unittest.TestCase):
    def make(self, **kwargs):
        self.now = [100.0]
        self.node = FakeNode()
        return N2kEngineData(self.node, clock=lambda: self.now[0], **kwargs)

    def test_subscribes_to_the_single_frame_messages(self):
        self.make()
        self.assertTrue({PGN_FLUID_LEVEL, PGN_ENGINE_RAPID} <= self.node.subscribed)

    def test_values_and_per_message_staleness(self):
        data = self.make()
        self.assertIsNone(data.rpm())
        self.node.send(PGN_ENGINE_RAPID, 48, rapid_payload(rpm=2400, trim=47))
        self.node.send(PGN_ENGINE_DYNAMIC, 48, dynamic_payload(oil_psi=41, engine_temp_c=82, alt_v=13.9))
        self.node.send(PGN_FLUID_LEVEL, 48, fluid_level_payload(62.5, 113.6))
        self.assertEqual(data.rpm(), 2400.0)
        self.assertEqual(data.trim_pct(), 47)
        self.assertAlmostEqual(data.oil_pressure_psi(), 41, delta=0.02)
        self.assertAlmostEqual(data.engine_temp_f(), 82 * 9 / 5 + 32, places=1)
        self.assertAlmostEqual(data.alternator_v(), 13.9, places=2)
        self.assertAlmostEqual(data.fuel_level_pct(), 62.5, places=2)
        self.now[0] += 2.5                                          # RPM (10 per second) has gone quiet, the slower ones have not
        self.assertIsNone(data.rpm())
        self.assertIsNone(data.trim_pct())
        self.assertIsNotNone(data.oil_pressure_psi())
        self.assertIsNotNone(data.fuel_level_pct())
        self.now[0] += 2.0                                          # 4.5 s: dynamic values are gone, the level (every 2.5 s) is not
        self.assertIsNone(data.oil_pressure_psi())
        self.assertIsNotNone(data.fuel_level_pct())
        self.now[0] += 1.0
        self.assertIsNone(data.fuel_level_pct())

    def test_not_available_replaces_an_older_value_at_once(self):
        data = self.make()
        self.node.send(PGN_ENGINE_DYNAMIC, 48, dynamic_payload(oil_psi=40, engine_temp_c=80))
        self.assertIsNotNone(data.oil_pressure_psi())
        self.node.send(PGN_ENGINE_DYNAMIC, 48, dynamic_payload(engine_temp_c=80))      # the oil sender just came off
        self.assertIsNone(data.oil_pressure_psi())
        self.assertIsNotNone(data.engine_temp_f())

    def test_only_the_configured_engine_counts_but_everything_is_listed(self):
        data = self.make(engine_instance=0)
        self.node.send(PGN_ENGINE_RAPID, 48, rapid_payload(rpm=900, instance=1))       # the converter's second channel
        self.assertIsNone(data.rpm())
        self.assertFalse(data.heard_engine())
        status = data.status()
        self.assertEqual([(e["source"], e["instance"], e["rpm"]) for e in status["engines"]], [(48, 1, 900.0)])
        self.assertEqual(self.make(engine_instance=1).rpm(), None)                     # a fresh store has heard nothing
        second = self.make(engine_instance=1)
        self.node.send(PGN_ENGINE_RAPID, 48, rapid_payload(rpm=900, instance=1))
        self.assertEqual(second.rpm(), 900.0)
        self.assertTrue(second.heard_engine())

    def test_a_converter_and_a_flow_sensor_share_one_engine(self):
        data = self.make()
        # The converter fills the fuel-rate field it doesn't measure with zero; the flow sensor measures it.
        self.node.send(PGN_ENGINE_DYNAMIC, 48, dynamic_payload(oil_psi=40, engine_temp_c=80, fuel_lph=0))
        self.node.send(PGN_ENGINE_DYNAMIC, 61, dynamic_payload(fuel_lph=0))
        self.assertIsNone(data.fuel_gph())                                             # zeros alone are not a measurement
        self.node.send(PGN_ENGINE_DYNAMIC, 61, dynamic_payload(fuel_lph=25))
        self.node.send(PGN_ENGINE_DYNAMIC, 48, dynamic_payload(oil_psi=40, engine_temp_c=80, fuel_lph=0))   # arrives after, must not win
        self.assertAlmostEqual(data.fuel_gph(), 25 / LITERS_PER_GALLON, places=3)
        self.assertAlmostEqual(data.oil_pressure_psi(), 40, delta=0.02)
        self.node.send(PGN_ENGINE_DYNAMIC, 61, dynamic_payload(fuel_lph=0))            # engine stopped: a proven sensor's zero is real
        self.assertEqual(data.fuel_gph(), 0.0)
        flow = {f["source"]: f["flow_sensor"] for f in data.status()["fuel_flow"]}
        self.assertEqual(flow, {48: False, 61: True})

    def test_zero_oil_pressure_from_an_unwired_input_is_not_a_reading(self):
        data = self.make()
        self.node.send(PGN_ENGINE_DYNAMIC, 48, dynamic_payload(oil_psi=0, engine_temp_c=80))    # the converter has no oil sender to read
        self.assertIsNone(data.oil_pressure_psi())
        self.assertEqual(data.status()["engines"][0]["oil_pressure_psi"], 0.0)                  # ...though the calibration page shows what it sends
        self.node.send(PGN_ENGINE_DYNAMIC, 48, dynamic_payload(oil_psi=41, engine_temp_c=80))   # the engine starts: a real reading
        self.assertAlmostEqual(data.oil_pressure_psi(), 41, delta=0.02)
        self.node.send(PGN_ENGINE_DYNAMIC, 48, dynamic_payload(oil_psi=0, engine_temp_c=80))    # and stops: now zero is real
        self.assertEqual(data.oil_pressure_psi(), 0.0)

    def test_which_temperature_is_the_coolant(self):
        payload = dynamic_payload(oil_temp_c=95, engine_temp_c=80)
        engine_field, oil_field = self.make(), None
        self.node.send(PGN_ENGINE_DYNAMIC, 48, payload)
        self.assertAlmostEqual(engine_field.engine_temp_f(), 176.0, places=1)
        oil_field = self.make(temp_field="oil")
        self.node.send(PGN_ENGINE_DYNAMIC, 48, payload)
        self.assertAlmostEqual(oil_field.engine_temp_f(), 203.0, delta=0.2)   # oil temperature is sent in 0.1 K steps

    def test_heard_engine_goes_false_when_the_converter_goes_quiet(self):
        data = self.make()
        self.node.send(PGN_ENGINE_RAPID, 48, rapid_payload(rpm=0))
        self.assertTrue(data.heard_engine())
        self.now[0] += 5.0
        self.assertFalse(data.heard_engine())

    def test_status_lists_engine_values_in_dashboard_units(self):
        data = self.make()
        self.node.send(PGN_ENGINE_RAPID, 48, rapid_payload(rpm=2400, trim=47))
        self.node.send(PGN_ENGINE_DYNAMIC, 48, dynamic_payload(oil_psi=41, oil_temp_c=60, engine_temp_c=82))
        entry = data.status()["engines"][0]
        self.assertEqual((entry["rpm"], entry["trim_pct"]), (2400.0, 47))
        self.assertAlmostEqual(entry["oil_pressure_psi"], 41, delta=0.1)
        self.assertAlmostEqual(entry["engine_temp_f"], 179.6, places=1)
        self.assertAlmostEqual(entry["oil_temp_f"], 140.0, delta=0.2)
        self.assertIsNone(entry["alternator_v"])


class FakeEngineBus:
    """Stands in for N2kEngineData with settable values."""

    def __init__(self, **values):
        self.v = {"rpm": None, "trim_pct": None, "oil_pressure_psi": None, "engine_temp_f": None,
                  "alternator_v": None, "fuel_gph": None, "fuel_level_pct": None}
        self.v.update(values)

    def __getattr__(self, name):
        if name in self.v:
            return lambda: self.v[name]
        raise AttributeError(name)

    def heard_engine(self):
        return any(x is not None for x in self.v.values())

    def raw(self):
        return dict(self.v)

    def status(self):
        return {"engines": [], "fuel_flow": [], "tank_levels": []}


class FakeProbes:
    def __init__(self, temps):
        self.temps = temps

    def devices(self):
        return sorted(self.temps)

    def read_f(self, device):
        return self.temps[device]


class TestHubOnTheBus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def hub(self, bus, adc=None, w1=None, **settings):
        cal = Calibration(Path(self.tmp.name) / "calibration.json")
        hub = SensorHub(Settings(sensors="n2k", **settings), cal, adc, None, w1, bus)
        hub.sample_once()
        return hub

    def test_the_pi_reads_no_analog_senders_in_n2k_mode(self):
        self.assertEqual(self.hub(FakeEngineBus()).channels, {})
        self.assertEqual(self.hub(FakeEngineBus(), battery_adc=True).channels, {"battery": 2})

    def test_nothing_heard_means_no_data_never_made_up_numbers(self):
        engine = self.hub(FakeEngineBus()).engine()
        for key in ("rpm", "oil_pressure_psi", "fuel_pct", "fuel_gph", "trim_pct", "coolant_f"):
            self.assertIsNone(engine[key], key)

    def test_engine_values_come_from_the_bus(self):
        hub = self.hub(FakeEngineBus(rpm=2400.0, oil_pressure_psi=N2K_OIL_FULL_PSI / 2, engine_temp_f=176.4, fuel_level_pct=62.5))
        engine = hub.engine()
        self.assertEqual(engine["rpm"], 2400)
        self.assertAlmostEqual(engine["oil_pressure_psi"], N2K_OIL_FULL_PSI / 2)        # as the converter reports it, until scaled
        self.assertEqual(engine["coolant_f"], 176)
        self.assertEqual(engine["fuel_pct"], 62.5)
        self.assertIsNone(engine["trim_pct"])                                             # needs the two end points first
        self.assertTrue(engine["fuel_gph_est"])                                          # no flow sensor: estimated from the bus RPM
        self.assertGreater(engine["fuel_gph"], 1.0)

    def test_trim_is_calibrated_from_the_bus_percentage(self):
        bus = FakeEngineBus(trim_pct=20)
        hub = self.hub(bus)
        self.assertIn("Saved", hub.capture("trim_down"))
        bus.v["trim_pct"] = 47
        hub.capture("trim_up")
        bus.v["trim_pct"] = 33.5
        self.assertAlmostEqual(hub.engine()["trim_pct"], 50.0, places=1)
        self.assertEqual(hub.status()["calibration"]["trim"]["down_pct"], 20.0)

    def test_oil_pressure_for_a_us_sender_on_a_10_bar_converter(self):
        bus = FakeEngineBus(oil_pressure_psi=141.7)            # what a 0-10 bar converter reads for an 80 psi sender at 80 psi
        hub = self.hub(bus)
        hub.capture("oil_full_scale", 80)
        self.assertAlmostEqual(hub.engine()["oil_pressure_psi"], 141.7 * 80 / N2K_OIL_FULL_PSI, places=2)
        with self.assertRaises(ValueError):
            hub.capture("oil_full_scale", 0)

    def test_fuel_level_can_be_stretched_between_the_real_empty_and_full(self):
        bus = FakeEngineBus(fuel_level_pct=6.0)
        hub = self.hub(bus)
        hub.capture("fuel_empty")
        bus.v["fuel_level_pct"] = 96.0
        hub.capture("fuel_full")
        bus.v["fuel_level_pct"] = 51.0
        self.assertAlmostEqual(hub.engine()["fuel_pct"], 50.0, places=1)

    def test_capture_says_so_when_nothing_is_heard(self):
        hub = self.hub(FakeEngineBus())
        for action in ("trim_down", "trim_up", "fuel_empty", "fuel_full"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                hub.capture(action)

    def test_tach_calibration_uses_the_bus_rpm(self):
        hub = self.hub(FakeEngineBus(rpm=2400.0))
        hub.capture("tach", 2500)
        self.assertEqual(hub.engine()["rpm"], 2500)
        with self.assertRaises(ValueError):
            self.hub(FakeEngineBus(rpm=0.0)).capture("tach", 2500)                       # engine not running

    def test_battery_prefers_the_adc_then_the_converters_alternator_volts(self):
        bus = FakeEngineBus(alternator_v=13.9)
        self.assertEqual(self.hub(bus).battery_volts(), 13.9)
        adc = FakeAdc()
        adc.volts[2] = 12.6 * 10000 / 57000                                              # 47k over 10k divider
        self.assertAlmostEqual(self.hub(bus, adc=adc, battery_adc=True).battery_volts(), 12.6, places=2)

    def test_the_bus_temperature_beats_a_probe_and_a_lone_probe_is_not_assumed_to_be_the_engine(self):
        probes = FakeProbes({"28-aaa": 66.0})
        self.assertIsNone(self.hub(FakeEngineBus(), w1=probes).engine()["coolant_f"])   # water probe, not coolant
        self.assertEqual(self.hub(FakeEngineBus(engine_temp_f=180.0), w1=probes).engine()["coolant_f"], 180)
        hub = self.hub(FakeEngineBus(), w1=probes)
        hub.capture("probe_engine", device="28-aaa")                                    # explicitly assigned: now it counts
        hub.sample_probes()
        self.assertEqual(hub.engine()["coolant_f"], 66)

    def test_status_carries_the_mode_and_raw_bus_values(self):
        status = self.hub(FakeEngineBus(trim_pct=33, rpm=900.0)).status()
        self.assertEqual(status["mode"], "n2k")
        self.assertEqual(status["n2k_raw"]["trim_pct"], 33)
        self.assertTrue(status["oil_enabled"])
        self.assertFalse(status["fuel_sender_enabled"])

    def test_engine_heard(self):
        self.assertTrue(self.hub(FakeEngineBus(rpm=0.0)).engine_heard())
        self.assertFalse(self.hub(FakeEngineBus()).engine_heard())


class TestFactory(unittest.TestCase):
    def test_n2k_mode_builds_a_hub_on_the_shared_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, boat, hub = make_sensor_sources(Settings(sensors="n2k"), tmp, FakeNode())
            try:
                self.assertIsInstance(hub.n2k, N2kEngineData)
                self.assertEqual(hub.channels, {})
                self.assertIsNone(engine.read(0)["rpm"])
                self.assertIsNone(boat.read(False)["battery_voltage"])
            finally:
                hub.stop()

    def test_n2k_mode_without_a_can_interface_shows_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, boat, hub = make_sensor_sources(Settings(sensors="n2k"), tmp, None)
            try:
                self.assertIsNone(hub.n2k)
                self.assertIsNone(hub.engine_heard())
                self.assertIsNone(engine.read(0)["oil_pressure_psi"])
            finally:
                hub.stop()


@unittest.skipUnless(can, "python-can not installed")
class TestOverTheBus(unittest.TestCase):
    """A fake CX5003-style converter on a virtual CAN bus, talking to the real node and decoders."""

    def test_rpm_trim_oil_temperature_and_fuel_level_arrive(self):
        bus_a = can.Bus(channel="engine-test", interface="virtual")
        converter = can.Bus(channel="engine-test", interface="virtual")
        node = N2kNode(bus_a, encode_name(9), address=41)
        data = N2kEngineData(node)
        node.start()
        try:
            time.sleep(0.4)
            source = 0x30
            converter.send(can.Message(arbitration_id=make_can_id(2, PGN_ENGINE_RAPID, source), is_extended_id=True,
                                       data=rapid_payload(rpm=2400, trim=47)))                        # single frame
            for frame in fast_packet_frames(2, dynamic_payload(oil_psi=41, engine_temp_c=82)):        # fast packet, 26 bytes
                converter.send(can.Message(arbitration_id=make_can_id(5, PGN_ENGINE_DYNAMIC, source), is_extended_id=True, data=frame))
            converter.send(can.Message(arbitration_id=make_can_id(6, PGN_FLUID_LEVEL, source), is_extended_id=True,
                                       data=fluid_level_payload(62.5, 113.6)))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and None in (data.rpm(), data.oil_pressure_psi(), data.fuel_level_pct()):
                time.sleep(0.05)
            self.assertEqual(data.rpm(), 2400.0)
            self.assertEqual(data.trim_pct(), 47)
            self.assertAlmostEqual(data.oil_pressure_psi(), 41, delta=0.02)
            self.assertAlmostEqual(data.engine_temp_f(), 82 * 9 / 5 + 32, places=1)
            self.assertAlmostEqual(data.fuel_level_pct(), 62.5, places=2)
            self.assertTrue(data.heard_engine())
        finally:
            node.shutdown()
            converter.shutdown()


if __name__ == "__main__":
    unittest.main()
