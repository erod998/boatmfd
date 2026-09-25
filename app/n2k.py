"""Minimal NMEA 2000 node (CAN 2.0B, 250 kbit/s) on top of python-can.

Just enough of the protocol to be a polite bus citizen and talk to a Fusion
stereo: 29-bit CAN identifiers, fast-packet framing (used by PGN 126720 and
the 130816-131071 proprietary range), and ISO address claiming (PGN 60928),
including answering ISO address-claim requests (PGN 59904).

Not a full N2K stack: no product-information PGN (126996), no heartbeat.
The codec functions are pure and importable without python-can; only
N2kNode needs a python-can Bus.

One node is shared by everything that talks NMEA 2000 (the Fusion stereo and
the fuel sensor): each registers a listener and, for single-frame PGNs it
wants, subscribes to them.
"""
import platform
import threading
import time
import zlib

PGN_ISO_REQUEST = 59904
PGN_ADDRESS_CLAIM = 60928
GLOBAL_ADDRESS = 255
NULL_ADDRESS = 254

# Multi-frame PGNs we reassemble: Fusion commands (126720), engine dynamic / trip (127489, 127497)
# and the proprietary range Fusion status uses (130816-131071).
FAST_PACKET_PGNS = {126720, 127489, 127497}


def is_fast_packet_pgn(pgn):
    return pgn in FAST_PACKET_PGNS or 130816 <= pgn <= 131071


def make_can_id(priority, pgn, source, dest=GLOBAL_ADDRESS):
    pf = (pgn >> 8) & 0xFF
    dp = (pgn >> 16) & 1
    ps = dest if pf < 240 else pgn & 0xFF  # PDU1 (addressed) vs PDU2 (broadcast)
    return (priority << 26) | (dp << 24) | (pf << 16) | (ps << 8) | source


def parse_can_id(can_id):
    """Return (priority, pgn, source, dest)."""
    priority = (can_id >> 26) & 0x7
    dp = (can_id >> 24) & 1
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    source = can_id & 0xFF
    if pf < 240:
        return priority, (dp << 16) | (pf << 8), source, ps
    return priority, (dp << 16) | (pf << 8) | ps, source, GLOBAL_ADDRESS


def fast_packet_frames(seq, payload):
    """Split a payload (<= 223 bytes) into 8-byte fast-packet CAN frames."""
    if len(payload) > 223:
        raise ValueError("fast-packet payload too long")
    frames = [(bytes([(seq << 5), len(payload)]) + payload[:6]).ljust(8, b"\xff")]
    rest = payload[6:]
    for i in range(0, len(rest), 7):
        counter = 1 + i // 7
        frames.append((bytes([(seq << 5) | counter]) + rest[i:i + 7]).ljust(8, b"\xff"))
    return frames


class FastPacketAssembler:
    """Reassembles fast-packet frames per (source, pgn); drops stale or out-of-order partials."""

    TIMEOUT_S = 1.0

    def __init__(self):
        self._partial = {}

    def feed(self, source, pgn, data, now=None):
        now = time.monotonic() if now is None else now
        if len(data) < 2:
            return None
        seq, counter = data[0] >> 5, data[0] & 0x1F
        key = (source, pgn)

        if counter == 0:
            total, buf = data[1], bytearray(data[2:8])
            if total <= len(buf):
                self._partial.pop(key, None)
                return bytes(buf[:total])
            self._partial[key] = {"seq": seq, "total": total, "buf": buf, "next": 1, "t": now}
            return None

        part = self._partial.get(key)
        if part is None or part["seq"] != seq or part["next"] != counter or now - part["t"] > self.TIMEOUT_S:
            self._partial.pop(key, None)
            return None
        part["buf"].extend(data[1:8])
        part["next"] += 1
        part["t"] = now
        if len(part["buf"]) >= part["total"]:
            del self._partial[key]
            return bytes(part["buf"][:part["total"]])
        return None


def encode_name(unique_number, manufacturer=2046, function=130, device_class=120,
                ecu_instance=0, function_instance=0, system_instance=0,
                industry_group=4, arbitrary_address=True):
    """64-bit ISO NAME. Defaults: a marine (industry 4) Display, manufacturer 2046 (DIY/unassigned)."""
    return (
        (unique_number & 0x1FFFFF)
        | ((manufacturer & 0x7FF) << 21)
        | ((ecu_instance & 0x7) << 32)
        | ((function_instance & 0x1F) << 35)
        | ((function & 0xFF) << 40)
        | (1 << 48)
        | ((device_class & 0x7F) << 49)
        | ((system_instance & 0xF) << 56)
        | ((industry_group & 0x7) << 60)
        | ((1 if arbitrary_address else 0) << 63)
    )


def name_manufacturer(name):
    return (name >> 21) & 0x7FF


class N2kNode:
    """A single N2K device: claims an address, answers address requests, sends and receives fast packets."""

    def __init__(self, bus, name, address=42, on_message=None):
        self.bus = bus
        self.name = name
        self.address = address
        self.listeners = [on_message] if on_message else []  # each called as fn(pgn, source, payload)
        self.single_frame_pgns = set()  # single-frame PGNs delivered to listeners (fast-packet PGNs always are)
        self.devices = {}  # source address -> NAME seen in address claims
        self._assembler = FastPacketAssembler()
        self._seq = {}
        self._send_lock = threading.Lock()      # one CAN frame at a time
        self._message_lock = threading.Lock()   # one fast-packet message at a time: see send_fast
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._claimed_at = 0.0
        self._discovery_sent = False
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="n2k-rx", daemon=True)
        self._thread.start()

    def add_listener(self, listener):
        self.listeners.append(listener)

    def subscribe(self, *pgns):
        """Also deliver these single-frame PGNs (e.g. 127505 fluid level) to listeners."""
        self.single_frame_pgns.update(pgns)

    def shutdown(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.bus.shutdown()

    def _dispatch(self, pgn, source, payload):
        for listener in list(self.listeners):
            try:
                listener(pgn, source, payload)
            except Exception as exc:  # one misbehaving listener must not stop the receive thread
                print(f"[n2k] listener error on PGN {pgn}: {exc}")

    def send_fast(self, pgn, payload, dest=GLOBAL_ADDRESS, priority=7):
        # N2K requires waiting 250 ms after claiming an address before transmitting anything else.
        if not self._ready.wait(timeout=2):
            raise RuntimeError("NMEA 2000 address not claimed yet")
        # A message's frames go out together, and its sequence number is its own. Two threads
        # sending the same PGN at once (the volume boost and a tap on the volume slider) used to
        # interleave their frames, and the stereo lost or spliced one of the commands.
        with self._message_lock:
            seq = self._seq.get(pgn, 0)
            self._seq[pgn] = (seq + 1) & 0x7
            for frame in fast_packet_frames(seq, payload):
                self._send_frame(priority, pgn, dest, frame)

    def _send_frame(self, priority, pgn, dest, data):
        """Send one frame. A failure raises OSError, as every caller expects of I/O: python-can's
        own errors (CanOperationError, "Transmit buffer full" when nothing on the bus acknowledges,
        or the interface bus-off) are not OSErrors, and used to escape the callers' handlers --
        out of the telemetry loop, which then froze the gauges on their last values."""
        import can

        msg = can.Message(arbitration_id=make_can_id(priority, pgn, self.address, dest), is_extended_id=True, data=data)
        try:
            with self._send_lock:
                self.bus.send(msg)
        except can.CanError as exc:
            raise OSError(f"CAN send failed: {exc}") from exc

    def _send_address_claim(self):
        self._claimed_at = time.monotonic()
        self._send_frame(6, PGN_ADDRESS_CLAIM, GLOBAL_ADDRESS, self.name.to_bytes(8, "little"))

    def _claim_new_address(self):
        self._ready.clear()
        self.address = next((a for a in range(128, 248) if a not in self.devices), NULL_ADDRESS)
        self._send_address_claim()

    def _run(self):
        """The receive loop. It has to outlive the bus.

        Nothing in here used to be guarded, so the first error from the interface -- the backbone
        not powered yet when the Pi boots, a bus-off after a wiring fault or noise while cranking,
        `ip link set can0 down` -- killed this thread silently and for good: engine data went to
        "--" (correctly, it goes stale) and never came back until the service was restarted. Now an
        error is logged once, the loop waits a moment and carries on, and when the bus answers
        again the address is claimed afresh, since the other devices may have forgotten us.
        (Recovering from bus-off itself is the kernel's job: see `restart-ms` in the CAN bring-up.)
        """
        claim_needed = True
        failing = None
        while not self._stop.is_set():
            try:
                if claim_needed:
                    self._send_address_claim()
                    claim_needed = False
                if not self._ready.is_set() and self.address != NULL_ADDRESS and time.monotonic() - self._claimed_at > 0.25:
                    self._ready.set()
                if self._ready.is_set() and not self._discovery_sent:
                    self._discovery_sent = True
                    self._send_frame(6, PGN_ISO_REQUEST, GLOBAL_ADDRESS, PGN_ADDRESS_CLAIM.to_bytes(3, "little"))
                msg = self.bus.recv(timeout=0.1)
            except Exception as exc:
                if str(exc) != failing:
                    print(f"[n2k] CAN interface error ({exc}); retrying")
                    failing = str(exc)
                self._ready.clear()
                claim_needed = True
                self._discovery_sent = False
                self._stop.wait(1.0)
                continue
            if failing is not None:
                print("[n2k] CAN interface is back")
                failing = None
            if msg is not None and msg.is_extended_id:
                try:
                    self._handle(msg)
                except Exception as exc:   # one malformed frame from any device must not stop the rest
                    print(f"[n2k] ignored a frame from {msg.arbitration_id:#x} ({exc})")

    def _handle(self, msg):
        _, pgn, source, dest = parse_can_id(msg.arbitration_id)
        data = bytes(msg.data)

        if pgn == PGN_ADDRESS_CLAIM and len(data) >= 8:
            their_name = int.from_bytes(data[:8], "little")
            self.devices[source] = their_name
            if source == self.address and their_name != self.name:
                if self.name < their_name:
                    self._send_address_claim()  # lower NAME wins: defend our address
                else:
                    self._claim_new_address()
        elif pgn == PGN_ISO_REQUEST and dest in (GLOBAL_ADDRESS, self.address) and len(data) >= 3:
            if int.from_bytes(data[:3], "little") == PGN_ADDRESS_CLAIM and self.address != NULL_ADDRESS:
                self._send_address_claim()
        elif is_fast_packet_pgn(pgn) and dest in (GLOBAL_ADDRESS, self.address):
            payload = self._assembler.feed(source, pgn, data)
            if payload is not None:
                self._dispatch(pgn, source, payload)
        elif pgn in self.single_frame_pgns and dest in (GLOBAL_ADDRESS, self.address):
            self._dispatch(pgn, source, data)


def make_n2k_node(settings):
    """Open the CAN interface and start the one shared NMEA 2000 node.

    Returns None if no CAN channel is configured (BOAT_CAN) or it can't be opened.
    """
    if not settings.can_channel:
        return None
    try:
        import can

        bus = can.Bus(channel=settings.can_channel, interface=settings.can_interface)
    except Exception as exc:  # pragma: no cover - hardware-dependent
        print(f"[n2k] could not open CAN channel {settings.can_channel} ({exc})")
        return None
    unique = zlib.crc32(platform.node().encode()) & 0x1FFFFF
    node = N2kNode(bus, encode_name(unique), address=settings.n2k_address)
    node.start()
    return node
