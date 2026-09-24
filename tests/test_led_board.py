"""Tests for the LED board driver (BOAT_LED_DRIVER=ledboard): the zones' PWM, the RP2040's
registers, and the 20 A budget -- against a fake I2C bus that answers as the board's three chips.

    python -m unittest discover -s tests -t . -v
"""
import unittest

from app.lighting import LedBoardDriver, LightingController

PCA, MCU, INA = 0x40, 0x30, 0x45


class FakeBoard:
    """The board's bus: records every write; the RP2040 answers its ID, the INA226 a current."""

    def __init__(self, amps=0.0, mcu=True):
        self.writes = []            # (address, register, [bytes])
        self.amps = amps
        self.mcu = mcu
        self.pca_mode1 = 0x11

    def read_i2c_block_data(self, address, register, length):
        if address == PCA:
            return [self.pca_mode1]
        if address == MCU:
            if not self.mcu:
                raise OSError("no RP2040")
            return [ord("L"), ord("B")][:length]
        if address == INA:
            if register == 0x04:
                raw = round(self.amps / 0.001) & 0xFFFF
                return [raw >> 8, raw & 0xFF]
            if register == 0x02:
                raw = round(12.8 / 0.00125)
                return [raw >> 8, raw & 0xFF]
        raise OSError(f"nothing at 0x{address:02x}")

    def write_i2c_block_data(self, address, register, data):
        self.writes.append((address, register, list(data)))

    # What the chips were last told.
    def duties(self):
        """PCA9685 output -> duty (0-4095), from the block writes of all 16 outputs."""
        out = {}
        for address, register, data in self.writes:
            if address != PCA or register < 0x06 or register >= 0x46:
                continue
            first = (register - 0x06) // 4
            for k in range(len(data) // 4):
                on_l, on_h, off_l, off_h = data[4 * k:4 * k + 4]
                out[first + k] = 4095 if on_h & 0x10 else 0 if off_h & 0x10 else off_l | (off_h << 8)
        return out

    def mcu_register(self, register):
        value = None
        for address, start, data in self.writes:
            if address == MCU and start <= register < start + len(data):
                value = data[register - start]
        return value


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(board=None, **kw):
    board = board or FakeBoard()
    clock = Clock()
    driver = LedBoardDriver(bus=board, sleep=lambda s: None, clock=clock, **kw)
    return board, driver, clock


class TestZones(unittest.TestCase):
    # No addressable strips here, so the zones have the whole budget to themselves.
    def test_every_zone_shows_the_colour_with_the_white_leds_taking_its_white_part(self):
        board, driver, _ = make(pixel_counts=())
        driver.show([(255, 180, 110)])            # warm white
        d = board.duties()
        for z in range(4):
            r, g, b, w = (d[4 * z + k] for k in range(4))
            self.assertEqual(w, driver.pwm._duty(110))
            self.assertEqual((r, g, b), tuple(driver.pwm._duty(v) for v in (145, 70, 0)))

    def test_without_white_extraction_an_rgb_strip_gets_the_whole_colour(self):
        board, driver, _ = make(pixel_counts=(), white=False)
        driver.show([(255, 180, 110)])
        d = board.duties()
        self.assertEqual((d[0], d[1], d[2], d[3]), (4095, driver.pwm._duty(180), driver.pwm._duty(110), 0))

    def test_zone_1_red_is_output_0_as_the_pwm_driver_default(self):
        board, driver, _ = make(pixel_counts=())
        driver.show([(255, 0, 0)])
        d = board.duties()
        self.assertEqual(d[0], 4095)
        self.assertEqual([d[k] for k in (1, 2, 3)], [0, 0, 0])


class TestPixels(unittest.TestCase):
    def test_the_rp2040_runs_the_rainbow_itself(self):
        board, driver, _ = make(pixel_counts=(300, 150, 0, 60))
        driver.set_effect("rainbow", (0, 255, 255), 0.6, True)
        driver.show([(10, 20, 30)])
        for n, count in enumerate((300, 150, 0, 60)):
            base = 0x10 + 0x10 * n
            self.assertEqual(board.mcu_register(base), 2)                 # rainbow
            self.assertEqual(board.mcu_register(base + 4), 153)           # 60 % brightness
            self.assertEqual(board.mcu_register(base + 8) | board.mcu_register(base + 9) << 8, count)

    def test_a_solid_colour_and_off(self):
        board, driver, _ = make()
        driver.set_effect("solid", (255, 90, 0), 1.0, True)
        driver.show([(255, 90, 0)])
        self.assertEqual([board.mcu_register(0x10 + k) for k in range(4)], [1, 255, 90, 0])
        driver.set_effect("solid", (255, 90, 0), 1.0, False)
        driver.show([(0, 0, 0)])
        self.assertEqual(board.mcu_register(0x10), 0)

    def test_unchanged_settings_are_not_resent_until_the_refresh(self):
        board, driver, clock = make()
        driver.set_effect("rainbow", (0, 0, 0), 0.5, True)
        driver.show([(1, 2, 3)])
        sent = len([w for w in board.writes if w[0] == MCU])
        clock.t += 0.5
        driver.show([(4, 5, 6)])                  # the zones' rainbow moves; the RP2040's settings don't
        self.assertEqual(len([w for w in board.writes if w[0] == MCU]), sent)
        clock.t += 2.0
        driver.show([(7, 8, 9)])
        self.assertGreater(len([w for w in board.writes if w[0] == MCU]), sent)

    def test_a_board_without_its_rp2040_still_runs_the_zones(self):
        board, driver, _ = make(FakeBoard(mcu=False))
        driver.show([(255, 0, 0)])
        self.assertFalse(driver.mcu_ok)
        self.assertEqual(board.duties()[0], 4095)


class TestBudget(unittest.TestCase):
    def test_everything_at_full_white_is_dimmed_to_20_amps(self):
        board, driver, _ = make()
        driver.set_effect("solid", (255, 255, 255), 1.0, True)
        driver.show([(255, 255, 255)])
        # The zones' white is their white LEDs alone, a quarter of each zone's 8 A: 4 x 2 A, and
        # 1200 pixels x 25 mA = 30 A: 38 A asked for, 20 A allowed.
        self.assertAlmostEqual(driver.estimate, 38.0, places=1)
        self.assertAlmostEqual(driver.scale, 20 / 38, places=3)
        self.assertEqual(board.mcu_register(0x08), round(255 * 20 / 38))
        self.assertLess(board.duties()[3], 4095)

    def test_zones_at_full_rgb_with_no_white_leds(self):
        board, driver, _ = make(pixel_counts=(), white=False)
        driver.set_effect("solid", (255, 255, 255), 1.0, True)
        driver.show([(255, 255, 255)])
        self.assertAlmostEqual(driver.estimate, 24.0, places=1)       # R, G and B of four zones: 3/4 of 32 A
        self.assertAlmostEqual(driver.scale, 20 / 24, places=3)

    def test_a_dim_scene_is_left_alone(self):
        board, driver, _ = make()
        driver.set_effect("solid", (0, 60, 255), 0.3, True)
        driver.show([(0, 18, 76)])
        self.assertEqual(driver.scale, 1.0)
        self.assertEqual(board.mcu_register(0x08), 255)

    def test_the_measured_current_dims_further_when_the_estimate_is_short(self):
        board, driver, clock = make(FakeBoard(amps=26.0))
        driver.set_effect("solid", (255, 0, 0), 0.5, True)
        driver.show([(128, 0, 0)])
        self.assertLess(driver.trim, 1.0)
        first = driver.trim
        clock.t += 0.3
        driver.show([(128, 0, 0)])
        self.assertLess(driver.trim, first)                 # still over: dims again
        board.amps = 10.0
        for _ in range(50):
            clock.t += 0.3
            driver.show([(128, 0, 0)])
        self.assertEqual(driver.trim, 1.0)                  # and comes back once there's room
        self.assertEqual(driver.telemetry()["amps"], 10.0)
        self.assertEqual(driver.telemetry()["volts"], 12.8)


class TestWithTheController(unittest.TestCase):
    def test_the_controller_tells_the_board_its_effect(self):
        board, driver, _ = make()
        lights = LightingController(1, driver=driver)
        lights.set_power(True)
        lights.set_preset("rainbow")
        lights.tick()
        self.assertEqual(board.mcu_register(0x10), 2)
        self.assertIn("board", lights.state())


class TestSettings(unittest.TestCase):
    def test_the_ledboard_driver_opens_the_led_boards_bus_with_its_settings(self):
        from unittest import mock

        from app import main
        from app.state import Settings
        with mock.patch.object(main, "LedBoardDriver") as driver:
            _, pixels = main.make_led_driver(Settings(led_driver="ledboard", led_i2c_bus=7, led_board_pixels="300,240,0,60",
                                                      led_board_max_amps=15.0))
        kw = driver.call_args.kwargs
        self.assertEqual((kw["bus_number"], kw["pixel_counts"], kw["max_amps"]), (7, [300, 240, 0, 60], 15.0))
        self.assertEqual(pixels, 1)          # the app shows one colour; the strips run their effects themselves


if __name__ == "__main__":
    unittest.main()
