"""The real serial GPS reader, against a fake port.

    python -m unittest discover -s tests -t . -v

Nothing else exercises this code: the dashboard runs on the simulator everywhere except the
boat. Each test here is a way a real GPS module behaves that the old reader got wrong, and that
a chartplotter must never get wrong -- above all, showing a position it no longer has.
"""
import unittest
from unittest import mock

try:
    import pynmea2  # noqa: F401 -- optional dependency, only needed on real hardware
except ImportError:  # pragma: no cover
    pynmea2 = None

from app.gps import COURSE_MIN_SOG_KN, FIX_STALE_S, REOPEN_S, SerialGPS


def nmea(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"${body}*{c:02X}\r\n".encode("ascii")


def rmc(lat="3618.360", lon="08633.780", sog="022.4", course="084.4", status="A"):
    return nmea(f"GPRMC,123519,{status},{lat},N,{lon},W,{sog},{course},230394,,,A")


def gga(quality=1, sats=9, hdop="0.9", lat="3618.360", lon="08633.780"):
    if quality == 0:
        return nmea(f"GPGGA,123519,,,,,0,00,99.99,,,,,,")
    return nmea(f"GPGGA,123519,{lat},N,{lon},W,{quality},{sats:02d},{hdop},180.0,M,-33.0,M,,")


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakePort:
    """Hands out queued lines; an empty queue behaves like the real 1 s read timeout."""

    def __init__(self, clock):
        self.clock = clock
        self.lines = []
        self.fail_with = None
        self.closed = False

    def readline(self):
        if self.fail_with:
            raise self.fail_with
        if self.lines:
            self.clock.t += 0.05
            return self.lines.pop(0)
        self.clock.t += 1.0
        return b""

    def close(self):
        self.closed = True


@unittest.skipIf(pynmea2 is None, "pynmea2 is only installed on real hardware")
class TestSerialGPS(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.port = FakePort(self.clock)
        self.opens = 0

        def opener():
            self.opens += 1
            if isinstance(self.port, Exception):
                raise self.port
            return self.port

        self.opener = opener
        self.log = mock.patch("builtins.print")
        self.log.start()
        self.addCleanup(self.log.stop)

    def gps(self):
        return SerialGPS("/dev/fake", opener=self.opener, clock=self.clock)

    def test_a_good_fix(self):
        gps = self.gps()
        self.port.lines = [gga(), rmc()]
        fix = gps.read()
        self.assertTrue(fix.has_fix)
        self.assertAlmostEqual(fix.lat, 36.306, places=3)
        self.assertAlmostEqual(fix.lon, -86.563, places=3)
        self.assertAlmostEqual(fix.sog_kn, 22.4)
        self.assertAlmostEqual(fix.cog_deg, 84.4)
        self.assertEqual(fix.satellites, 9)

    def test_a_silent_gps_stops_being_a_fix(self):
        # The old reader kept the last position on screen as current, forever.
        gps = self.gps()
        self.port.lines = [gga(), rmc()]
        self.assertTrue(gps.read().has_fix)
        for _ in range(int(FIX_STALE_S) + 1):
            last = gps.read()                                   # nothing arrives
        self.assertFalse(last.has_fix)

    def test_a_void_rmc_never_moves_the_boat_to_zero_zero(self):
        gps = self.gps()
        self.port.lines = [gga(), rmc()]
        gps.read()
        self.port.lines = [gga(), rmc(lat="", lon="", sog="", course="", status="V")]
        fix = gps.read()
        self.assertFalse(fix.has_fix)
        self.assertNotAlmostEqual(fix.lat, 0.0)                 # the last real position, flagged lost
        self.assertNotAlmostEqual(fix.lon, 0.0)

    def test_a_no_fix_gga_ends_the_fix_at_once(self):
        gps = self.gps()
        self.port.lines = [gga(), rmc()]
        gps.read()
        self.port.lines = [gga(quality=0)]
        self.assertFalse(gps.read().has_fix)

    def test_line_noise_is_dropped_not_fatal(self):
        gps = self.gps()
        self.port.lines = [b"$GPRMC,12\\x00\\xff9,A,36\r\n", b"\xff\xfe\x00junk\r\n", gga(), rmc()]
        self.assertTrue(gps.read().has_fix)

    def test_a_sentence_without_its_checksum_is_not_trusted(self):
        gps = self.gps()
        self.port.lines = [b"$GPRMC,123519,A,0000.000,N,00000.000,E,022.4,084.4,230394,,,A\r\n"]
        self.assertFalse(gps.read().has_fix)

    def test_a_corrupted_checksum_is_rejected(self):
        gps = self.gps()
        good = rmc()
        self.port.lines = [good.replace(b"3618.360", b"3618.960")]   # a flipped digit, checksum unchanged
        self.assertFalse(gps.read().has_fix)

    def test_unplugging_reports_no_fix_and_does_not_raise(self):
        gps = self.gps()
        self.port.lines = [gga(), rmc()]
        gps.read()
        self.port.fail_with = OSError("device disconnected")
        fix = gps.read()                                        # must not raise into the frame loop
        self.assertTrue(self.port.closed)
        self.clock.t += FIX_STALE_S
        self.assertFalse(gps.read().has_fix)

    def test_plugging_back_in_picks_the_gps_up_again(self):
        gps = self.gps()
        self.port.fail_with = OSError("device disconnected")
        gps.read()
        fresh = FakePort(self.clock)
        fresh.lines = [gga(), rmc()]
        self.port = fresh
        self.clock.t += REOPEN_S
        self.assertTrue(gps.read().has_fix)
        self.assertEqual(self.opens, 2)

    def test_missing_at_boot_is_no_fix_then_found(self):
        self.port, waiting = OSError("no such device"), self.port
        gps = self.gps()
        self.assertFalse(gps.read().has_fix)
        waiting.lines = [gga(), rmc()]
        self.port = waiting
        self.clock.t += REOPEN_S
        self.assertTrue(gps.read().has_fix)

    def test_course_holds_at_rest_instead_of_spinning(self):
        gps = self.gps()
        self.port.lines = [gga(), rmc(sog="015.0", course="084.4")]
        gps.read()
        still = f"{COURSE_MIN_SOG_KN / 4:05.1f}"
        self.port.lines = [gga(), rmc(sog=still, course="271.0"), rmc(sog=still, course="133.0")]
        fix = gps.read()
        self.assertAlmostEqual(fix.cog_deg, 84.4)
        self.assertAlmostEqual(fix.heading_deg, 84.4)

    def test_an_rmc_only_receiver_still_has_a_fix(self):
        gps = self.gps()
        self.port.lines = [rmc()]
        self.assertTrue(gps.read().has_fix)

    def test_each_read_is_its_own_object(self):
        # The frame loop keeps the last fix while the next read runs on another thread.
        gps = self.gps()
        self.port.lines = [gga(), rmc()]
        first = gps.read()
        self.port.lines = [gga(), rmc(lat="3620.000")]
        gps.read()
        self.assertAlmostEqual(first.lat, 36.306, places=3)


if __name__ == "__main__":
    unittest.main()
