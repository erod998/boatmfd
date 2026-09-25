"""The NMEA 2000 receive loop has to outlive the bus.

    python -m unittest discover -s tests -t . -v

It used to die on the first interface error -- the backbone not powered yet at boot, a bus-off,
the link taken down -- and engine data then stayed at "--" until the service was restarted.
"""
import threading
import time
import unittest
from unittest import mock

try:
    import can
except ImportError:  # pragma: no cover
    can = None

from app.n2k import N2kNode, PGN_ADDRESS_CLAIM, encode_name, make_can_id, parse_can_id

PGN_TEST = 127505   # fluid level: a single-frame PGN


class FlakyBus:
    """recv/send fail while `down` is set; frames queued in `incoming` arrive once it is up."""

    def __init__(self):
        self.down = False
        self.send_down = False
        self.incoming = []
        self.sent = []
        self.lock = threading.Lock()

    def recv(self, timeout=None):
        if self.down:
            raise OSError("[Errno 100] Network is down")
        with self.lock:
            if self.incoming:
                return self.incoming.pop(0)
        time.sleep(0.01)
        return None

    def send(self, msg):
        if self.send_down:
            raise OSError("[Errno 105] No buffer space available")
        with self.lock:
            self.sent.append(msg)

    def shutdown(self):
        pass

    def claims_sent(self):
        with self.lock:
            return sum(1 for m in self.sent if parse_can_id(m.arbitration_id)[1] == PGN_ADDRESS_CLAIM)


def frame(pgn, source=11, data=b"\x00\x01\x02\x03\x04\x05\x06\x07"):
    return can.Message(arbitration_id=make_can_id(6, pgn, source), is_extended_id=True, data=data)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@unittest.skipIf(can is None, "python-can is only installed where CAN is used")
class TestReceiveLoopSurvives(unittest.TestCase):
    def setUp(self):
        self.bus = FlakyBus()
        self.got = []
        self.node = N2kNode(self.bus, encode_name(7), address=40, on_message=lambda p, s, d: self.got.append(p))
        self.node.subscribe(PGN_TEST)
        self.log = mock.patch("builtins.print")
        self.log.start()
        self.addCleanup(self.log.stop)
        self.addCleanup(self.node.shutdown)

    def test_keeps_receiving_after_the_interface_drops_out(self):
        self.node.start()
        self.bus.down = True
        time.sleep(0.3)                                     # errors while down: must not kill the thread
        self.bus.down = False
        self.bus.incoming.append(frame(PGN_TEST))
        self.assertTrue(wait_for(lambda: PGN_TEST in self.got), "the receive thread died with the bus")

    def test_claims_its_address_again_when_the_bus_comes_back(self):
        self.node.start()
        self.assertTrue(wait_for(lambda: self.bus.claims_sent() >= 1))
        before = self.bus.claims_sent()
        self.bus.down = True
        time.sleep(0.3)
        self.bus.down = False
        self.assertTrue(wait_for(lambda: self.bus.claims_sent() > before))

    def test_a_backbone_not_powered_at_boot_is_picked_up_later(self):
        self.bus.send_down = True                           # the very first address claim fails
        self.node.start()
        time.sleep(0.3)
        self.bus.send_down = False
        self.bus.incoming.append(frame(PGN_TEST))
        self.assertTrue(wait_for(lambda: PGN_TEST in self.got))
        self.assertTrue(wait_for(lambda: self.bus.claims_sent() >= 1))

    def test_a_bad_frame_from_another_device_is_skipped(self):
        self.node.start()
        with mock.patch.object(self.node, "_dispatch", side_effect=[ValueError("garbled"), None]) as dispatch:
            self.bus.incoming += [frame(PGN_TEST), frame(PGN_TEST)]
            self.assertTrue(wait_for(lambda: dispatch.call_count == 2), "one bad frame stopped the loop")


class TestSending(unittest.TestCase):
    """What a send does when the bus is not well, and when two threads send at once."""

    class Bus:
        def __init__(self, error=None):
            self.sent, self.error = [], error

        def send(self, msg):
            if self.error:
                raise self.error
            self.sent.append(bytes(msg.data))
            time.sleep(0.001)                 # a frame's time on the wire, with the GIL let go

        def recv(self, timeout=None):
            return None

        def shutdown(self):
            pass

    @unittest.skipIf(can is None, "python-can is only installed where CAN is used")
    def test_a_can_error_is_an_oserror_like_any_other_io_failure(self):
        # python-can raises CanOperationError ("Transmit buffer full", bus-off), which is not an
        # OSError; the callers' `except (ValueError, RuntimeError, OSError)` let it through.
        node = N2kNode(self.Bus(can.CanOperationError("Transmit buffer full")), encode_name(1))
        node._ready.set()
        with self.assertRaises(OSError):
            node.send_fast(126720, bytes(12))

    @unittest.skipIf(can is None, "python-can is only installed where CAN is used")
    def test_two_threads_sending_the_same_pgn_do_not_mix_their_frames(self):
        from app.n2k import FastPacketAssembler
        for _ in range(20):
            bus = self.Bus()
            node = N2kNode(bus, encode_name(1))
            node._ready.set()
            a, b = bytes([1] * 12), bytes([2] * 12)
            go = threading.Barrier(2)

            def send(payload):
                go.wait()
                node.send_fast(126720, payload)

            threads = [threading.Thread(target=send, args=(p,)) for p in (a, b)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            assembler = FastPacketAssembler()
            got = [m for f in bus.sent if (m := assembler.feed(42, 126720, f))]
            self.assertEqual(sorted(got), [a, b])


if __name__ == "__main__":
    unittest.main()
