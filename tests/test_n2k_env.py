"""Tests for depth and sea temperature over NMEA 2000 (a transducer such as an Airmar DST800/DST810)
and how the sensor hub uses them.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import time
import unittest
from pathlib import Path

from app.n2k import N2kNode, encode_name, make_can_id
from app.n2k_env import (N2kEnvData, PGN_ENVIRONMENTAL, PGN_ENVIRONMENTAL_OLD, PGN_TEMPERATURE, PGN_TEMPERATURE_EXT,
                         PGN_WATER_DEPTH, parse_environmental, parse_water_depth)
from app.sensors import Calibration, SensorHub, make_sensor_sources
from app.state import Settings
from tests.test_n2k_fuel import FakeNode

try:
    import can
except ImportError:  # pragma: no cover
    can = None


def depth_payload(depth_m, offset_m=None, sid=0xFF):
    """PGN 128267 as canboat lays it out: SID, depth (0.01 m), offset (0.001 m), range."""
    depth_raw = 0xFFFFFFFF if depth_m is None else round(depth_m / 0.01)
    offset_raw = -32768 if offset_m is None else round(offset_m / 0.001)
    return bytes([sid]) + depth_raw.to_bytes(4, "little") + offset_raw.to_bytes(2, "little", signed=True) + b"\xff"


def env_payload(temp_c, source=0, sid=0xFF):
    temp_raw = 0xFFFF if temp_c is None else round((temp_c + 273.15) / 0.01)
    return bytes([sid, source & 0x3F]) + temp_raw.to_bytes(2, "little") + b"\xff\xff\xff\xff"


class TestDecoders(unittest.TestCase):
    def test_water_depth(self):
        parsed = parse_water_depth(depth_payload(4.2, offset_m=0.3, sid=7))
        self.assertEqual(parsed["sid"], 7)
        self.assertAlmostEqual(parsed["depth_m"], 4.2, places=2)
        self.assertAlmostEqual(parsed["offset_m"], 0.3, places=3)

    def test_negative_offset_to_the_keel(self):
        parsed = parse_water_depth(depth_payload(4.2, offset_m=-0.5))
        self.assertAlmostEqual(parsed["offset_m"], -0.5, places=3)

    def test_depth_without_an_offset_field_still_parses(self):
        parsed = parse_water_depth(depth_payload(4.2, offset_m=None)[:5])  # some devices send only 5 bytes
        self.assertAlmostEqual(parsed["depth_m"], 4.2, places=2)
        self.assertIsNone(parsed["offset_m"])

    def test_not_available_and_short_payloads(self):
        self.assertIsNone(parse_water_depth(depth_payload(None))["depth_m"])
        self.assertIsNone(parse_water_depth(depth_payload(4.2, offset_m=None))["offset_m"])
        self.assertIsNone(parse_water_depth(bytes(3)))

    def test_environmental_keeps_only_sea_temperature(self):
        parsed = parse_environmental(env_payload(18.5, source=0))
        self.assertAlmostEqual(parsed["water_temp_c"], 18.5, places=1)
        self.assertIsNone(parse_environmental(env_payload(21.0, source=1)))  # outside air temperature: not ours
        self.assertIsNone(parse_environmental(env_payload(4.0, source=3)))   # engine room temperature: not ours

    def test_environmental_not_available_and_implausible(self):
        self.assertIsNone(parse_environmental(env_payload(None)))
        self.assertIsNone(parse_environmental(bytes(2)))

    def test_a_filler_zero_is_not_a_sea_temperature(self):
        payload = bytes([0, 0]) + (0).to_bytes(2, "little") + b"\xff\xff\xff\xff"  # 0 K: absolute zero, obviously filler
        self.assertIsNone(parse_environmental(payload))


class TestEnvData(unittest.TestCase):
    def make(self, **kwargs):
        self.now = [100.0]
        self.node = FakeNode()
        return N2kEnvData(self.node, clock=lambda: self.now[0], **kwargs)

    def test_subscribes_to_the_single_frame_messages(self):
        self.make()
        self.assertTrue({PGN_WATER_DEPTH, PGN_ENVIRONMENTAL} <= self.node.subscribed)

    def test_depth_combines_the_devices_own_offset(self):
        data = self.make()
        self.assertIsNone(data.depth_ft())
        self.node.send(PGN_WATER_DEPTH, 48, depth_payload(4.0, offset_m=0.3))  # 4.3 m below the surface
        self.assertAlmostEqual(data.depth_ft(), 4.3 / 0.3048, places=2)

    def test_calibration_offset_is_added_on_top(self):
        data = self.make()
        self.node.send(PGN_WATER_DEPTH, 48, depth_payload(4.0, offset_m=0.3))
        plain = data.depth_ft()
        self.assertAlmostEqual(data.depth_ft(extra_offset_ft=-1.5), plain - 1.5, places=2)

    def test_the_sequence_id_is_not_an_instance(self):
        # Byte 0 of PGN 128267 is a SID: 0xFF from a transducer that doesn't use it, or counting
        # 0-252 from one that does. Neither may hide the depth (it used to be matched against an
        # "instance" 0, which showed no depth at all, or one second in 253).
        data = self.make()
        for sid in (0xFF, 0, 1, 2, 137, 252):
            self.node.send(PGN_WATER_DEPTH, 48, depth_payload(4.0, sid=sid))
            self.assertAlmostEqual(data.depth_ft(), 4.0 / 0.3048, places=2)

    def test_a_chosen_transducer_by_its_source_address(self):
        data = self.make(depth_source=49)
        self.node.send(PGN_WATER_DEPTH, 48, depth_payload(4.0))
        self.assertIsNone(data.depth_ft())
        self.node.send(PGN_WATER_DEPTH, 49, depth_payload(6.0))
        self.assertAlmostEqual(data.depth_ft(), 6.0 / 0.3048, places=2)

    def test_depth_goes_stale(self):
        data = self.make()
        self.node.send(PGN_WATER_DEPTH, 48, depth_payload(4.0))
        self.assertIsNotNone(data.depth_ft())
        self.now[0] += 4.0
        self.assertIsNone(data.depth_ft())

    def test_not_available_clears_an_earlier_depth_at_once(self):
        data = self.make()
        self.node.send(PGN_WATER_DEPTH, 48, depth_payload(4.0))
        self.assertIsNotNone(data.depth_ft())
        self.node.send(PGN_WATER_DEPTH, 48, depth_payload(None))  # the transducer lost bottom
        self.assertIsNone(data.depth_ft())

    def test_sea_temperature_and_staleness(self):
        data = self.make()
        self.assertIsNone(data.water_temp_f())
        self.node.send(PGN_ENVIRONMENTAL, 48, env_payload(20.0, source=0))
        self.assertAlmostEqual(data.water_temp_f(), 20.0 * 9 / 5 + 32, places=1)
        self.now[0] += 5.0
        self.assertIsNone(data.water_temp_f())

    def test_heard(self):
        data = self.make()
        self.assertFalse(data.heard())
        self.node.send(PGN_WATER_DEPTH, 48, depth_payload(4.0))
        self.assertTrue(data.heard())

    def test_status_lists_devices_and_ages(self):
        data = self.make()
        self.node.send(PGN_WATER_DEPTH, 48, depth_payload(4.0, offset_m=0.3))
        self.node.send(PGN_ENVIRONMENTAL, 48, env_payload(20.0))
        status = data.status()
        self.assertEqual(len(status["depths"]), 1)
        self.assertAlmostEqual(status["depths"][0]["depth_ft"], 4.0 / 0.3048, places=2)
        self.assertAlmostEqual(status["depths"][0]["offset_ft"], 0.3 / 0.3048, places=2)
        self.assertEqual(len(status["temps"]), 1)
        self.assertAlmostEqual(status["temps"][0]["water_temp_f"], 68.0, places=0)


class FakeEnv:
    def __init__(self, depth=None, temp=None):
        self.depth, self.temp = depth, temp

    def depth_ft(self, extra_offset_ft=0.0):
        return None if self.depth is None else self.depth + extra_offset_ft

    def water_temp_f(self):
        return self.temp

    def status(self):
        return {"depths": [], "temps": [], "depth_source": None}


class FakeProbes:
    def __init__(self, temps):
        self.temps = temps

    def devices(self):
        return sorted(self.temps)

    def read_f(self, device):
        return self.temps[device]


class TestHubDepth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def hub(self, env, w1=None, **settings):
        cal = Calibration(Path(self.tmp.name) / "calibration.json")
        return SensorHub(Settings(sensors="n2k", **settings), cal, w1=w1, env=env), cal

    def test_no_transducer_means_no_data(self):
        hub, cal = self.hub(None)
        self.assertIsNone(hub.depth_ft())

    def test_depth_comes_from_the_transducer(self):
        hub, cal = self.hub(FakeEnv(depth=12.3))
        self.assertAlmostEqual(hub.depth_ft(), 12.3)

    def test_calibration_offset_is_applied(self):
        hub, cal = self.hub(FakeEnv(depth=12.3))
        hub.capture("depth_offset", 13.0)  # the true depth is 0.7 ft more than the transducer says
        self.assertAlmostEqual(hub.depth_ft(), 13.0, places=2)
        self.assertAlmostEqual(cal.get("depth", "offset_ft"), 0.7, places=2)

    def test_depth_offset_needs_a_transducer_reading(self):
        hub, cal = self.hub(None)
        with self.assertRaises(ValueError):
            hub.capture("depth_offset", 10.0)
        hub2, cal2 = self.hub(FakeEnv(depth=None))
        with self.assertRaises(ValueError):
            hub2.capture("depth_offset", 10.0)

    def test_transducer_sea_temperature_beats_the_probe(self):
        probes = FakeProbes({"28-aaa": 60.0})
        hub, cal = self.hub(FakeEnv(temp=71.0), w1=probes)
        hub.capture("probe_water", device="28-aaa")
        hub.sample_probes()
        self.assertEqual(hub.water_temp_f(), 71.0)

    def test_probe_is_used_when_the_transducer_sends_no_temperature(self):
        probes = FakeProbes({"28-aaa": 65.0})
        hub, cal = self.hub(FakeEnv(temp=None), w1=probes)
        hub.capture("probe_water", device="28-aaa")
        hub.sample_probes()
        self.assertEqual(hub.water_temp_f(), 65.0)

    def test_status_carries_depth_and_env(self):
        hub, cal = self.hub(FakeEnv(depth=8.0))
        status = hub.status()
        self.assertAlmostEqual(status["depth_ft"], 8.0)
        self.assertIn("env", status)


class TestFactory(unittest.TestCase):
    def test_n2k_mode_builds_a_depth_reader_on_the_shared_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, boat, hub = make_sensor_sources(Settings(sensors="n2k"), tmp, FakeNode())
            try:
                self.assertIsNotNone(hub.env)
                self.assertIsNone(boat.read(False)["depth_ft"])  # nothing heard yet: no data, not a made-up number
            finally:
                hub.stop()

    def test_no_can_interface_means_no_depth_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, boat, hub = make_sensor_sources(Settings(sensors="n2k"), tmp, None)
            try:
                self.assertIsNone(hub.env)
            finally:
                hub.stop()


@unittest.skipUnless(can, "python-can not installed")
class TestOverTheBus(unittest.TestCase):
    """A fake depth/temperature transducer on a virtual CAN bus, talking to the real node and decoders."""

    def test_depth_and_sea_temperature_arrive(self):
        bus_a = can.Bus(channel="env-test", interface="virtual")
        transducer = can.Bus(channel="env-test", interface="virtual")
        node = N2kNode(bus_a, encode_name(9), address=41)
        data = N2kEnvData(node)
        node.start()
        try:
            time.sleep(0.4)
            source = 0x33
            transducer.send(can.Message(arbitration_id=make_can_id(3, PGN_WATER_DEPTH, source), is_extended_id=True,
                                        data=depth_payload(5.5, offset_m=0.25)))
            transducer.send(can.Message(arbitration_id=make_can_id(6, PGN_ENVIRONMENTAL, source), is_extended_id=True,
                                        data=env_payload(19.0, source=0)))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and (data.depth_ft() is None or data.water_temp_f() is None):
                time.sleep(0.05)
            self.assertAlmostEqual(data.depth_ft(), 5.75 / 0.3048, places=1)
            self.assertAlmostEqual(data.water_temp_f(), 19.0 * 9 / 5 + 32, places=0)
            self.assertTrue(data.heard())
        finally:
            node.shutdown()
            transducer.shutdown()


def env_old_payload(temp_c, sid=0xFF):
    """PGN 130310: SID, water temperature (0.01 K), outside air (0.01 K), pressure (100 Pa)."""
    raw = 0xFFFF if temp_c is None else round((temp_c + 273.15) / 0.01)
    return bytes([sid]) + raw.to_bytes(2, "little") + b"\xff\xff\xff\xff\xff"


def temperature_payload(temp_c, source=0, sid=0xFF, instance=0):
    """PGN 130312: SID, instance, source, actual temperature (0.01 K), set temperature."""
    raw = 0xFFFF if temp_c is None else round((temp_c + 273.15) / 0.01)
    return bytes([sid, instance, source]) + raw.to_bytes(2, "little") + b"\xff\xff\xff"


def temperature_ext_payload(temp_c, source=0, sid=0xFF, instance=0):
    """PGN 130316: SID, instance, source, temperature (24 bits, 0.001 K), set temperature."""
    raw = 0xFFFFFF if temp_c is None else round((temp_c + 273.15) / 0.001)
    return bytes([sid, instance, source]) + raw.to_bytes(3, "little") + b"\xff\xff"


class TestEverySeaTemperatureMessage(unittest.TestCase):
    """Garmin's displays take water temperature from 130310, 130311 or 130312, and a Garmin
    transducer (the GDT 43) sends one of them; 130316 is 130312's successor. Only 130311 used
    to be read, so a transducer using another showed its depth and no water temperature."""

    def setUp(self):
        self.now = [100.0]
        self.node = FakeNode()
        self.data = N2kEnvData(self.node, clock=lambda: self.now[0])

    def test_all_four_are_listened_for(self):
        self.assertTrue({PGN_ENVIRONMENTAL_OLD, PGN_ENVIRONMENTAL, PGN_TEMPERATURE, PGN_TEMPERATURE_EXT} <= self.node.subscribed)

    def test_each_gives_the_sea_temperature(self):
        for pgn, payload in ((PGN_ENVIRONMENTAL_OLD, env_old_payload(18.5)),
                             (PGN_TEMPERATURE, temperature_payload(18.5)),
                             (PGN_TEMPERATURE_EXT, temperature_ext_payload(18.5))):
            with self.subTest(pgn=pgn):
                data = N2kEnvData(FakeNode(), clock=lambda: 100.0)
                data._on_message(pgn, 35, payload)
                self.assertAlmostEqual(data.water_temp_f(), 18.5 * 9 / 5 + 32, places=1)

    def test_other_sources_and_not_available_are_ignored(self):
        self.node.send(PGN_TEMPERATURE, 35, temperature_payload(4.0, source=3))       # engine room
        self.node.send(PGN_TEMPERATURE_EXT, 35, temperature_ext_payload(30.0, source=1))  # outside air
        self.node.send(PGN_TEMPERATURE, 35, temperature_payload(None))
        self.node.send(PGN_ENVIRONMENTAL_OLD, 35, env_old_payload(None))
        self.assertIsNone(self.data.water_temp_f())

    def test_goes_stale(self):
        self.node.send(PGN_TEMPERATURE, 35, temperature_payload(18.5))
        self.assertIsNotNone(self.data.water_temp_f())
        self.now[0] += 5.0
        self.assertIsNone(self.data.water_temp_f())


if __name__ == "__main__":
    unittest.main()
