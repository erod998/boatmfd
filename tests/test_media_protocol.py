"""Protocol tests for the NMEA 2000 / Fusion layer. Run from the project root:

    python -m unittest discover -s tests -t . -v

The virtual-bus tests need python-can (skipped otherwise). They verify our
framing and Fusion messages against canboat's published field layouts; they
cannot prove behavior against a real stereo.
"""
import json
import struct
import time
import unittest

from app import fusion
from app.n2k import (
    N2kNode, FastPacketAssembler, encode_name, fast_packet_frames, make_can_id, name_manufacturer,
    parse_can_id, PGN_ADDRESS_CLAIM, PGN_ISO_REQUEST,
)

try:
    import can
except ImportError:  # pragma: no cover
    can = None


def string_lz(text):
    raw = text.encode()
    return bytes([len(raw)]) + raw + b"\x00"


def status(message_id, body):
    return fusion.HEADER + struct.pack("<H", message_id) + body


class TestCanId(unittest.TestCase):
    def test_known_vector(self):
        self.assertEqual(make_can_id(7, 126720, 0x20, 0x30), 0x1DEF3020)

    def test_roundtrip(self):
        cases = [(126720, 0x30, 0x30), (130820, 0xFF, 0xFF), (PGN_ADDRESS_CLAIM, 0xFF, 0xFF), (PGN_ISO_REQUEST, 0x11, 0x11)]
        for pgn, dest, expected_dest in cases:
            with self.subTest(pgn=pgn):
                self.assertEqual(parse_can_id(make_can_id(6, pgn, 0x2A, dest)), (6, pgn, 0x2A, expected_dest))


class TestFastPacket(unittest.TestCase):
    def roundtrip(self, payload, seq=3):
        assembler, result = FastPacketAssembler(), None
        for frame in fast_packet_frames(seq, payload):
            self.assertEqual(len(frame), 8)
            result = assembler.feed(0x20, 126720, frame)
        return result

    def test_lengths(self):
        for n in (1, 6, 7, 13, 14, 20, 60, 223):
            with self.subTest(length=n):
                payload = bytes(range(n))
                self.assertEqual(self.roundtrip(payload), payload)

    def test_six_byte_payload_is_one_frame(self):
        payload = fusion.media_command(1, fusion.MEDIA_NEXT)
        self.assertEqual(fast_packet_frames(2, payload), [bytes([0x40, 6]) + payload])

    def test_missing_frame_is_dropped(self):
        frames = fast_packet_frames(1, bytes(range(30)))
        assembler = FastPacketAssembler()
        results = [assembler.feed(0x20, 126720, f) for i, f in enumerate(frames) if i != 2]
        self.assertTrue(all(r is None for r in results))

    def test_interleaved_sources(self):
        a, b = bytes(range(20)), bytes(range(100, 130))
        fa, fb = fast_packet_frames(0, a), fast_packet_frames(5, b)
        assembler, done = FastPacketAssembler(), {}
        for i in range(max(len(fa), len(fb))):
            if i < len(fa) and (r := assembler.feed(0x20, 130820, fa[i])):
                done[0x20] = r
            if i < len(fb) and (r := assembler.feed(0x30, 130820, fb[i])):
                done[0x30] = r
        self.assertEqual(done, {0x20: a, 0x30: b})


class TestFusionCommands(unittest.TestCase):
    def test_exact_bytes(self):
        self.assertEqual(fusion.request_status(), bytes.fromhex("a3990100"))
        self.assertEqual(fusion.set_source(2), bytes.fromhex("a399020002"))
        self.assertEqual(fusion.media_command(3, fusion.MEDIA_NEXT), bytes.fromhex("a3990300" "0304"))
        self.assertEqual(fusion.media_command(3, fusion.MEDIA_PREV), bytes.fromhex("a3990300" "0306"))
        self.assertEqual(fusion.media_command(3, fusion.MEDIA_PLAY), bytes.fromhex("a3990300" "0301"))
        self.assertEqual(fusion.media_command(3, fusion.MEDIA_PAUSE), bytes.fromhex("a3990300" "0302"))
        self.assertEqual(fusion.set_mute(True), bytes.fromhex("a399110001"))
        self.assertEqual(fusion.set_mute(False), bytes.fromhex("a399110002"))
        self.assertEqual(fusion.set_zone_volume(1, 10), bytes.fromhex("a3991800" "000a"))   # Zone 1 is wire index 0
        self.assertEqual(fusion.set_zone_volume(2, 8), bytes.fromhex("a3991800" "0108"))    # Zone 2 is wire index 1
        self.assertEqual(fusion.set_power(True), bytes.fromhex("a399" "1c00" "01"))
        self.assertEqual(fusion.set_power(False), bytes.fromhex("a399" "1c00" "02"))

    def test_header_encodes_manufacturer_419_marine(self):
        value = struct.unpack("<H", fusion.HEADER)[0]
        self.assertEqual(value & 0x7FF, 419)
        self.assertEqual(value >> 13, 4)


class TestFusionStatus(unittest.TestCase):
    def test_power_mute_volumes(self):
        self.assertEqual(fusion.parse_status(status(fusion.ST_POWER, bytes([1]))), {"power": True})
        self.assertEqual(fusion.parse_status(status(fusion.ST_POWER, bytes([2]))), {"power": False})
        self.assertEqual(fusion.parse_status(status(fusion.ST_MUTE, bytes([1]))), {"muted": True})
        self.assertEqual(fusion.parse_status(status(fusion.ST_VOLUME, bytes([10, 8, 0, 3]))), {"volumes": [10, 8, 0, 3]})

    def test_source(self):
        parsed = fusion.parse_status(status(fusion.ST_SOURCE, bytes([2, 1, 5, 0]) + string_lz("USB")))
        self.assertEqual(parsed, {"source_entry": {"id": 2, "name": "USB", "type": "USB"}, "source_id": 1})

    def test_media(self):
        body = bytes([1]) + struct.pack("<H", 1) + struct.pack("<IIII", 3, 12, 214000, 42500)
        parsed = fusion.parse_status(status(fusion.ST_MEDIA, body))
        self.assertEqual(parsed, {"source_id": 1, "play_status": "playing", "track": 3, "track_count": 12,
                                  "length_s": 214.0, "position_s": 42.5})

    def test_track_strings_and_progress(self):
        for message_id, key in ((fusion.ST_TRACK_TITLE, "title"), (fusion.ST_TRACK_ARTIST, "artist"), (fusion.ST_TRACK_ALBUM, "album")):
            body = bytes([1]) + struct.pack("<I", 3) + string_lz("Nine Knots")
            self.assertEqual(fusion.parse_status(status(message_id, body)), {key: "Nine Knots"})
        self.assertEqual(fusion.parse_status(status(fusion.ST_TRACK_PROGRESS, bytes([1]) + (7500).to_bytes(3, "little"))), {"position_s": 7.5})

    def test_zone_names_limits_and_volume_slots(self):
        self.assertEqual(fusion.parse_status(status(fusion.ST_ZONE_NAME, bytes([0]) + string_lz("Cockpit"))),
                         {"zone_name": {"zone": 1, "name": "Cockpit"}})          # wire index 0 is Zone 1
        self.assertEqual(fusion.parse_status(status(fusion.ST_ZONE_NAME, bytes([1]) + string_lz("Cabin"))),
                         {"zone_name": {"zone": 2, "name": "Cabin"}})
        self.assertIsNone(fusion.parse_status(status(fusion.ST_ZONE_NAME, bytes([1]) + string_lz(""))))
        self.assertEqual(fusion.parse_status(status(fusion.ST_VOLUME_LIMITS, bytes([24, 20, 0, 0]))), {"volume_limits": [24, 20, 0, 0]})

    def test_ignores_other_manufacturers_and_garbage(self):
        self.assertIsNone(fusion.parse_status(bytes.fromhex("0102030405")))
        self.assertIsNone(fusion.parse_status(status(fusion.ST_MEDIA, b"\x01")))  # truncated
        self.assertIsNone(fusion.parse_status(status(99999 & 0xFFFF, b"\x00\x00\x00\x00")))


class RecordingNode:
    """Just enough of an N2kNode for FusionMedia: records what it sends and lets a test deliver status messages."""

    def __init__(self):
        self.listeners, self.sent = [], []

    def add_listener(self, fn):
        self.listeners.append(fn)

    def send_fast(self, pgn, payload, dest=255, priority=7):
        self.sent.append(payload)

    def deliver(self, payload, source=0x20):
        for fn in self.listeners:
            fn(fusion.PGN_STATUS, source, payload)

    def volumes_sent(self):
        return [(p[4], p[5]) for p in self.sent if p[2:4] == bytes([fusion.MSG_SET_ZONE_VOLUME, 0])]


class TestZones(unittest.TestCase):
    def fusion_media(self, zones=2):
        from app.media import FusionMedia

        node = RecordingNode()
        media = FusionMedia(node, max_volume=24, zones=zones)
        node.deliver(status(fusion.ST_VOLUME, bytes([12, 6, 0, 0])))
        return node, media

    def test_a_zone_is_sent_as_its_zero_based_wire_index(self):
        node, media = self.fusion_media()
        media.handle("volume", 9, zone=1)
        media.handle("volume", 4, zone=2)
        self.assertEqual(node.volumes_sent(), [(0, 9), (1, 4)])

    def test_zone_state_carries_names_volumes_and_limits(self):
        node, media = self.fusion_media()
        node.deliver(status(fusion.ST_ZONE_NAME, bytes([0]) + string_lz("Cockpit")))
        node.deliver(status(fusion.ST_VOLUME_LIMITS, bytes([24, 18, 0, 0])))
        zones = media.state()["zones"]
        self.assertEqual([(z["id"], z["name"], z["volume"], z["limit"]) for z in zones],
                         [(1, "Cockpit", 12, 24), (2, "Zone 2", 6, 18)])         # unnamed zones fall back to "Zone N"
        self.assertEqual(media.state()["volume"], 12)                              # the old single-volume field still means Zone 1

    def test_only_the_zones_the_stereo_has_are_offered_and_accepted(self):
        node, media = self.fusion_media(zones=2)
        self.assertEqual(len(media.state()["zones"]), 2)
        for zone in (0, 3, 5):
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                media.handle("volume", 5, zone=zone)
        with self.assertRaises(ValueError):
            media.handle("volume", 25, zone=1)                                     # above the maximum
        self.assertEqual(node.volumes_sent(), [])
        self.assertEqual(len(self.fusion_media(zones=4)[1].state()["zones"]), 4)

    def test_muting_a_zone_turns_it_to_zero_and_unmuting_restores_it(self):
        node, media = self.fusion_media()
        media.handle("zone_mute", 1, zone=2)
        node.deliver(status(fusion.ST_VOLUME, bytes([12, 0, 0, 0])))               # the stereo reports the new level
        self.assertEqual(node.volumes_sent(), [(1, 0)])
        self.assertTrue(media.state()["zones"][1]["muted"])
        self.assertFalse(media.state()["zones"][0]["muted"])
        media.handle("zone_mute", 0, zone=2)
        self.assertEqual(node.volumes_sent()[-1], (1, 6))                          # back to where it was
        node.deliver(status(fusion.ST_VOLUME, bytes([12, 6, 0, 0])))
        self.assertFalse(media.state()["zones"][1]["muted"])

    def test_a_zone_turned_up_on_the_stereo_is_no_longer_muted(self):
        node, media = self.fusion_media()
        media.handle("zone_mute", 1, zone=1)
        node.deliver(status(fusion.ST_VOLUME, bytes([0, 6, 0, 0])))
        self.assertTrue(media.state()["zones"][0]["muted"])
        node.deliver(status(fusion.ST_VOLUME, bytes([7, 6, 0, 0])))                # someone used the stereo's own dial
        self.assertFalse(media.state()["zones"][0]["muted"])
        media.handle("zone_mute", 0, zone=1)                                       # nothing remembered: a sensible default level
        self.assertEqual(node.volumes_sent()[-1], (0, 8))

    def test_master_volume_shifts_every_zone_by_the_same_amount_to_hit_the_target(self):
        node, media = self.fusion_media()  # starts at [12, 6]
        media.handle("master_volume", 15)
        self.assertEqual(node.volumes_sent(), [(0, 15), (1, 9)])       # +3 to both: the loudest zone lands exactly on target

    def test_master_volume_respects_each_zones_own_limit(self):
        node, media = self.fusion_media()  # starts at [12, 6]
        node.deliver(status(fusion.ST_VOLUME_LIMITS, bytes([24, 8, 0, 0])))
        media.handle("master_volume", 15)
        self.assertEqual(node.volumes_sent(), [(0, 15), (1, 8)])       # zone 2 would be 9 but its limit is 8

    def test_master_volume_does_not_wake_up_a_zone_someone_silenced(self):
        node, media = self.fusion_media()  # starts at [12, 6]
        media.handle("zone_mute", 1, zone=2)
        node.deliver(status(fusion.ST_VOLUME, bytes([12, 0, 0, 0])))
        node.sent.clear()
        media.handle("master_volume", 20)
        self.assertEqual(node.volumes_sent(), [(0, 20)])               # zone 2 is left alone, still muted

    def test_master_volume_can_bring_everything_down_to_zero(self):
        node, media = self.fusion_media()  # starts at [12, 6]
        media.handle("master_volume", 0)
        self.assertEqual(node.volumes_sent(), [(0, 0), (1, 0)])

    def test_master_volume_rejects_out_of_range(self):
        node, media = self.fusion_media()
        with self.assertRaises(ValueError):
            media.handle("master_volume", 25)
        self.assertEqual(node.volumes_sent(), [])

    def test_the_simulator_behaves_the_same_way(self):
        from app.media import SimulatedMedia

        media = SimulatedMedia(24, zones=2)
        state = media.state()
        self.assertEqual([(z["id"], z["name"]) for z in state["zones"]], [(1, "Cockpit"), (2, "Cabin")])
        media.handle("volume", 15, zone=2)
        self.assertEqual(media.state()["zones"][1]["volume"], 15)
        media.handle("zone_mute", 1, zone=2)
        self.assertEqual((media.state()["zones"][1]["volume"], media.state()["zones"][1]["muted"]), (0, True))
        media.handle("zone_mute", 0, zone=2)
        self.assertEqual(media.state()["zones"][1]["volume"], 15)
        with self.assertRaises(ValueError):
            media.handle("volume", 5, zone=3)
        self.assertEqual(media.state()["volume"], media.state()["zones"][0]["volume"])

    def test_the_simulators_master_volume_behaves_the_same_way(self):
        from app.media import SimulatedMedia

        media = SimulatedMedia(24, zones=2)  # starts at [10, 6]
        media.handle("master_volume", 16)
        self.assertEqual([z["volume"] for z in media.state()["zones"]], [16, 12])  # +6 to both
        media.handle("zone_mute", 1, zone=2)
        media.handle("master_volume", 20)
        zones = media.state()["zones"]
        self.assertEqual([z["volume"] for z in zones], [20, 0])                    # muted zone left alone
        self.assertTrue(zones[1]["muted"])
        with self.assertRaises(ValueError):
            media.handle("master_volume", 25)


@unittest.skipUnless(can, "python-can not installed")
class TestOnVirtualBus(unittest.TestCase):
    channel_counter = 0

    def setUp(self):
        type(self).channel_counter += 1
        channel = "boat-test-%d" % self.channel_counter
        self.our_bus = can.Bus(channel=channel, interface="virtual")
        self.peer = can.Bus(channel=channel, interface="virtual")
        self.assembler = FastPacketAssembler()

    def tearDown(self):
        self.peer.shutdown()

    def wait_for(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.peer.recv(timeout=0.1)
            if msg is not None:
                _, pgn, source, dest = parse_can_id(msg.arbitration_id)
                found = predicate(pgn, source, dest, bytes(msg.data))
                if found:
                    return found
        self.fail("expected frame never arrived")

    def wait_fast_payload(self, pgn_wanted, match=lambda payload: True):
        def check(pgn, source, dest, data):
            if pgn != pgn_wanted:
                return None
            payload = self.assembler.feed(source, pgn, data)
            return payload if payload is not None and match(payload) else None
        return self.wait_for(check)

    def stereo_sends(self, pgn, payload, source=0x30, dest=255):
        for frame in fast_packet_frames(0, payload):
            self.peer.send(can.Message(arbitration_id=make_can_id(7, pgn, source, dest), is_extended_id=True, data=frame))

    def test_claims_address_and_sends_addressed_fast_packet(self):
        node = N2kNode(self.our_bus, encode_name(7), address=40)
        node.start()
        try:
            claim = self.wait_for(lambda pgn, s, d, data: data if pgn == PGN_ADDRESS_CLAIM and s == 40 else None)
            self.assertEqual(int.from_bytes(claim, "little"), node.name)
            self.assertEqual(name_manufacturer(node.name), 2046)

            payload = fusion.media_command(1, fusion.MEDIA_NEXT)
            node.send_fast(fusion.PGN_COMMAND, payload, dest=0x30)
            got = self.wait_fast_payload(fusion.PGN_COMMAND, lambda p: p == payload)
            self.assertEqual(got, payload)
        finally:
            node.shutdown()

    def test_answers_iso_address_claim_request(self):
        node = N2kNode(self.our_bus, encode_name(7), address=40)
        node.start()
        try:
            self.wait_for(lambda pgn, s, d, data: pgn == PGN_ADDRESS_CLAIM and s == 40)
            request_id = make_can_id(6, PGN_ISO_REQUEST, 0x30, 255)
            self.peer.send(can.Message(arbitration_id=request_id, is_extended_id=True, data=PGN_ADDRESS_CLAIM.to_bytes(3, "little")))
            self.wait_for(lambda pgn, s, d, data: pgn == PGN_ADDRESS_CLAIM and s == 40)
        finally:
            node.shutdown()

    def test_loses_address_conflict_and_moves(self):
        node = N2kNode(self.our_bus, encode_name(7), address=40)
        node.start()
        try:
            self.wait_for(lambda pgn, s, d, data: pgn == PGN_ADDRESS_CLAIM and s == 40)
            rival = make_can_id(6, PGN_ADDRESS_CLAIM, 40, 255)
            self.peer.send(can.Message(arbitration_id=rival, is_extended_id=True, data=encode_name(1).to_bytes(8, "little")))
            moved = self.wait_for(lambda pgn, s, d, data: s if pgn == PGN_ADDRESS_CLAIM and s != 40 else None)
            self.assertTrue(128 <= moved <= 247)
        finally:
            node.shutdown()

    def test_fusion_media_end_to_end(self):
        from app.media import FusionMedia

        node = N2kNode(self.our_bus, encode_name(7), address=40)
        node.start()
        media = FusionMedia(node, max_volume=24)
        try:
            self.wait_for(lambda pgn, s, d, data: pgn == PGN_ADDRESS_CLAIM and s == 40)
            time.sleep(0.4)  # let the node finish its 250 ms claim window

            self.assertFalse(media.state()["connected"])
            for payload in (
                status(fusion.ST_POWER, bytes([1])),
                status(fusion.ST_SOURCE, bytes([1, 1, 10, 0]) + string_lz("Bluetooth")),
                status(fusion.ST_SOURCE, bytes([2, 1, 5, 0]) + string_lz("USB")),
                status(fusion.ST_MEDIA, bytes([1]) + struct.pack("<H", 1) + struct.pack("<IIII", 3, 12, 214000, 42500)),
                status(fusion.ST_TRACK_TITLE, bytes([1]) + struct.pack("<I", 3) + string_lz("Harbor Lights")),
                status(fusion.ST_VOLUME, bytes([10, 8, 0, 0])),
            ):
                self.stereo_sends(fusion.PGN_STATUS, payload)

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and media.state()["title"] != "Harbor Lights":
                time.sleep(0.05)
            state = media.state()
            self.assertTrue(state["connected"] and state["power"] and state["playing"])
            self.assertEqual((state["source_id"], state["title"], state["volume"]), (1, "Harbor Lights", 10))
            self.assertEqual([s["name"] for s in state["sources"]], ["Bluetooth", "USB"])

            media.handle("next")
            self.wait_fast_payload(fusion.PGN_COMMAND, lambda p: p == fusion.media_command(1, fusion.MEDIA_NEXT))
            media.handle("volume", 14, zone=1)
            self.wait_fast_payload(fusion.PGN_COMMAND, lambda p: p == fusion.set_zone_volume(1, 14))
            with self.assertRaises(ValueError):
                media.handle("volume", 99)
        finally:
            node.shutdown()


class TestArtworkFinder(unittest.TestCase):
    def wait_until(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not predicate():
            time.sleep(0.01)
        return predicate()

    def test_lookup_is_async_then_cached(self):
        from app.artwork import ArtworkFinder

        calls = []
        finder = ArtworkFinder(lookup=lambda artist, album, title: calls.append((artist, album, title)) or "https://x.mzstatic.com/a.jpg")
        self.assertIsNone(finder.get("Ketch & Yawl", "Weather Helm", "Nine Knots"))  # first call never blocks
        self.assertTrue(self.wait_until(lambda: finder.get("Ketch & Yawl", "Weather Helm", "Nine Knots")))
        self.assertEqual(finder.get("ketch & yawl", "weather helm", "Fair Winds"), "https://x.mzstatic.com/a.jpg")
        self.assertEqual(len(calls), 1)

    def test_needs_artist_and_something_to_search(self):
        from app.artwork import ArtworkFinder

        finder = ArtworkFinder(lookup=lambda *a: self.fail("should not look up"))
        self.assertIsNone(finder.get("", "Album", "Title"))
        self.assertIsNone(finder.get("Artist", "", ""))

    def test_errors_are_retried_later_not_cached_forever(self):
        from app.artwork import ArtworkFinder, ERROR_TTL_S

        now = [0.0]
        attempts = []

        def flaky(artist, album, title):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("offline")
            return "https://x.mzstatic.com/b.jpg"

        finder = ArtworkFinder(lookup=flaky, clock=lambda: now[0])
        finder.get("A", "B", "C")
        self.assertTrue(self.wait_until(lambda: len(attempts) == 1 and not finder._pending))
        self.assertIsNone(finder.get("A", "B", "C"))  # still within the error back-off
        self.assertEqual(len(attempts), 1)
        now[0] += ERROR_TTL_S + 1
        finder.get("A", "B", "C")
        self.assertTrue(self.wait_until(lambda: finder.get("A", "B", "C") == "https://x.mzstatic.com/b.jpg"))

    def test_itunes_lookup_only_accepts_apple_cdn_urls(self):
        import io
        from unittest import mock
        from app import artwork

        def response(art):
            body = json.dumps({"results": [{"artworkUrl100": art}]}).encode()
            return mock.MagicMock(__enter__=lambda s: io.BytesIO(body), __exit__=lambda *a: False)

        with mock.patch("urllib.request.urlopen", return_value=response("https://is1-ssl.mzstatic.com/x/100x100bb.jpg")):
            self.assertEqual(artwork.itunes_lookup("a", "b", "c"), "https://is1-ssl.mzstatic.com/x/400x400bb.jpg")
        with mock.patch("urllib.request.urlopen", return_value=response("https://evil.example.com/100x100bb.jpg")):
            self.assertIsNone(artwork.itunes_lookup("a", "b", "c"))


if __name__ == "__main__":
    unittest.main()
