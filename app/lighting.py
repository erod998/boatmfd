"""RGB lighting control.

The controller is just: power, brightness, a solid color (pick from
COLOR_PRESETS or any custom RGB value), or a rainbow effect.

Changes reach the lights at once: every setter wakes a small background loop
that pushes the new frame to the driver, and the loop keeps the rainbow moving
at 30 frames a second. (The lights used to update only when the 1 Hz telemetry
frame was built, so a tap took up to a second to show, and the rainbow stepped
once a second.)

MockDriver: in-memory pixel buffer, used whenever real hardware isn't
attached (default for the proof of concept) so the dashboard still shows a
live, working preview.

WS281xDriver: drives a real WS2812B/NeoPixel strip from a Raspberry Pi GPIO
pin via the rpi_ws281x library. Only imported/instantiated when explicitly
enabled, since it requires root and the library to be installed.

Pca9685RgbDriver: plain 12 V PWM RGB strips (the common 4-wire kind) through a
PCA9685 board and three MOSFETs.

LedBoardDriver: the LED board (hardware/led-board, LED_BOARD.md) -- four RGBW zones on
its PCA9685, four 12 V addressable strips run by its RP2040, and a current monitor that
keeps the whole board inside its 20 A.
"""
import colorsys
import threading
import time

COLOR_PRESETS = [
    {"name": "Red", "rgb": [255, 0, 0]},
    {"name": "Orange", "rgb": [255, 90, 0]},
    {"name": "Amber", "rgb": [255, 176, 0]},
    {"name": "Yellow", "rgb": [255, 240, 0]},
    {"name": "Green", "rgb": [0, 255, 0]},
    {"name": "Teal", "rgb": [0, 255, 170]},
    {"name": "Cyan", "rgb": [0, 255, 255]},
    {"name": "Blue", "rgb": [0, 60, 255]},
    {"name": "Purple", "rgb": [140, 0, 255]},
    {"name": "Magenta", "rgb": [255, 0, 200]},
    {"name": "Warm White", "rgb": [255, 180, 110]},
    {"name": "White", "rgb": [255, 255, 255]},
]

PRESET_NAMES = ["solid", "rainbow"]


def _solid(n, rgb):
    return [tuple(rgb)] * n


def _rainbow(n, t):
    colors = []
    for i in range(n):
        hue = ((i / max(1, n)) + t * 0.15) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        colors.append((int(r * 255), int(g * 255), int(b * 255)))
    return colors


class LightingController:
    ANIMATION_HZ = 30
    IDLE_WAKE_S = 0.5       # the loop checks in this often when nothing is animating
    REFRESH_S = 2.0         # an unchanged colour is sent to the driver again this often, to recover from an I2C glitch

    def __init__(self, pixel_count=30, driver=None):
        self.pixel_count = pixel_count
        self.preset = "solid"
        self.solid_color = tuple(COLOR_PRESETS[6]["rgb"])  # Cyan
        self.brightness = 0.6
        self.on = False
        self._t0 = time.time()
        self.driver = driver or MockDriver(pixel_count)
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._frame = [(0, 0, 0)] * pixel_count   # what the lights show now
        self._shown = None                        # what was last sent to the driver
        self._shown_at = 0.0

    # ---- setters: change the state, then have the loop apply it straight away ----
    def set_preset(self, preset):
        if preset not in PRESET_NAMES:
            raise ValueError(f"unknown preset '{preset}'")
        with self._lock:
            self.preset = preset
        self._wake.set()

    def set_solid_color(self, r, g, b):
        with self._lock:
            self.solid_color = tuple(max(0, min(255, int(v))) for v in (r, g, b))
            self.preset = "solid"
        self._wake.set()

    def set_brightness(self, value):
        with self._lock:
            self.brightness = max(0.0, min(1.0, float(value)))
        self._wake.set()

    def set_power(self, on: bool):
        with self._lock:
            self.on = bool(on)
        self._wake.set()

    def _raw_frame(self):
        if self.preset == "rainbow":
            return _rainbow(self.pixel_count, time.time() - self._t0)
        return _solid(self.pixel_count, self.solid_color)

    @property
    def animating(self):
        return self.on and self.preset == "rainbow"

    def tick(self):
        """Compute the current frame (respecting on/off + brightness) and push it to the driver if it changed."""
        with self._lock:
            frame = self._raw_frame() if self.on else _solid(self.pixel_count, (0, 0, 0))
            b = self.brightness
            frame = [(int(r * b), int(g * b), int(bl * b)) for r, g, bl in frame]
            now = time.monotonic()
            if frame != self._shown or now - self._shown_at >= self.REFRESH_S:
                if hasattr(self.driver, "set_effect"):   # a driver that runs effects itself (the LED board's RP2040)
                    self.driver.set_effect(self.preset, self.solid_color, self.brightness, self.on)
                self.driver.show(frame)
                self._shown, self._shown_at = frame, now
            self._frame = frame
            return frame

    # ---- the background loop ----
    def start(self):
        """Run the loop that applies changes immediately and animates the rainbow. Idempotent."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lighting", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()  # before ticking, so a change made while we tick wakes the wait below instead of being lost
            try:
                self.tick()
            except Exception as exc:  # a failing driver must not kill the loop; it is retried on the next pass
                print(f"[lighting] driver error: {exc}")
            self._wake.wait(1.0 / self.ANIMATION_HZ if self.animating else self.IDLE_WAKE_S)

    def state(self):
        with self._lock:
            out = {
                "preset": self.preset,
                "solid_color": self.solid_color,
                "brightness": self.brightness,
                "on": self.on,
                "pixel_count": self.pixel_count,
                "frame": list(self._frame),
            }
            if hasattr(self.driver, "telemetry"):
                out["board"] = self.driver.telemetry()
            return out


class MockDriver:
    """No real hardware — just remembers the last frame for the API/UI to read back."""

    def __init__(self, pixel_count):
        self.pixel_count = pixel_count
        self.last_frame = [(0, 0, 0)] * pixel_count

    def show(self, frame):
        self.last_frame = frame


class Pca9685RgbDriver:
    """Plain 12 V RGB strip (4-wire, one color at a time) via a PCA9685 PWM board.

    Each PCA9685 output drives a logic-level N-channel MOSFET that switches one
    color channel to ground (common-anode strip: +12 V shared, R/G/B switched
    low-side). Every LED shows the same color, so only the first pixel of the
    frame is used (the controller is created with a single pixel).
    Requires: pip install smbus2
    """

    REG_MODE1 = 0x00
    REG_PRESCALE = 0xFE
    REG_LED0_ON_L = 0x06

    def __init__(self, bus=None, address=0x40, channels=(0, 1, 2), freq_hz=1000, gamma=2.2, bus_number=1, sleep=time.sleep):
        if bus is None:
            from smbus2 import SMBus  # noqa: local import, hardware-only dependency

            bus = SMBus(bus_number)
        self.bus = bus
        self.address = address
        self.channels = channels
        self.gamma = gamma
        prescale = round(25_000_000 / (4096 * freq_hz)) - 1
        old = self.bus.read_i2c_block_data(address, self.REG_MODE1, 1)[0]
        self.bus.write_i2c_block_data(address, self.REG_MODE1, [(old & 0x7F) | 0x10])  # sleep so the prescaler can be set
        self.bus.write_i2c_block_data(address, self.REG_PRESCALE, [prescale])
        self.bus.write_i2c_block_data(address, self.REG_MODE1, [old])
        sleep(0.005)
        self.bus.write_i2c_block_data(address, self.REG_MODE1, [old | 0xA1])  # restart, auto-increment

    def _duty(self, value):
        return round((max(0, min(255, value)) / 255.0) ** self.gamma * 4095)

    def _set_duty(self, channel, duty):
        if duty <= 0:
            data = [0, 0, 0, 0x10]      # full off
        elif duty >= 4095:
            data = [0, 0x10, 0, 0]      # full on
        else:
            data = [0, 0, duty & 0xFF, duty >> 8]
        self.bus.write_i2c_block_data(self.address, self.REG_LED0_ON_L + 4 * channel, data)

    def show(self, frame):
        for channel, value in zip(self.channels, frame[0]):
            self._set_duty(channel, self._duty(value))

    def set_duties(self, duties):
        """Every output at once, 0-4095 each from output 0: eight outputs to a write (the chip's
        registers run on from one output to the next)."""
        for start in range(0, len(duties), 8):
            data = []
            for duty in duties[start:start + 8]:
                if duty <= 0:
                    data += [0, 0, 0, 0x10]
                elif duty >= 4095:
                    data += [0, 0x10, 0, 0]
                else:
                    data += [0, 0, duty & 0xFF, duty >> 8]
            self.bus.write_i2c_block_data(self.address, self.REG_LED0_ON_L + 4 * start, data)


class LedBoardDriver:
    """The LED board: four RGBW zones on its PCA9685 (outputs 4(z-1) + R 0, G 1, B 2, W 3), four
    12 V addressable strips run by its RP2040, and an INA226 measuring its 12 V input -- all on
    one I2C bus, the sensor board's link to it (BOAT_LED_I2C_BUS). See LED_BOARD.md.

    Every zone shows the controller's colour, its white LEDs taking the colour's white part (so
    a warm white is lit mostly by the white LEDs, not by red, green and blue together). Every
    addressable strip shows the same colour, or runs the rainbow itself: the RP2040 draws the
    frames, so the bus only carries a few bytes when something changes.

    The board is fed for 20 A, and eight strips at full white would draw about 60, so every
    frame is held inside max_amps before it's sent: the strips' current is worked out from what
    each output is asked to show (zone_amps is one zone's full-white draw, pixel_ma one pixel's)
    and everything is dimmed together if it's over. The INA226's reading backs that up: if the
    board still draws more than max_amps (longer strips than configured, say), it dims further
    until it doesn't.
    Requires: pip install smbus2
    """

    ZONES = 4
    COLOURS = 4                        # R, G, B, W
    PIXEL_OUTPUTS = 4
    MCU_ID = [ord("L"), ord("B")]
    REG_MCU_LIMIT = 0x08
    REG_MCU_OUTPUT = 0x10
    MODES = {"off": 0, "solid": 1, "rainbow": 2}
    ORDERS = ["GRB", "RGB", "BRG", "RBG", "GBR", "BGR"]
    INA_CONFIG, INA_CURRENT, INA_BUS_V, INA_CALIBRATION = 0x00, 0x04, 0x02, 0x05
    SHUNT_OHMS = 0.001
    CURRENT_LSB = 0.001                # amps per count, with the calibration below
    MEASURE_S = 0.2
    RAINBOW_LOAD = 0.5                 # a rainbow's pixels average half of full white

    def __init__(self, bus=None, bus_number=7, pca_address=0x40, mcu_address=0x30, ina_address=0x45,
                 pixel_counts=(300, 300, 300, 300), color_order="GRB", max_amps=20.0, zone_amps=8.0,
                 pixel_ma=25.0, white=True, sleep=time.sleep, clock=time.monotonic):
        if bus is None:
            from smbus2 import SMBus  # noqa: local import, hardware-only dependency

            bus = SMBus(bus_number)
        self.bus = bus
        self.pwm = Pca9685RgbDriver(bus=bus, address=pca_address, channels=tuple(range(16)), sleep=sleep)
        self.mcu_address, self.ina_address = mcu_address, ina_address
        self.pixel_counts = [max(0, min(600, int(n))) for n in pixel_counts][:self.PIXEL_OUTPUTS]
        self.pixel_counts += [0] * (self.PIXEL_OUTPUTS - len(self.pixel_counts))
        self.order = self.ORDERS.index(color_order.upper())
        self.max_amps, self.zone_amps, self.pixel_ma = max_amps, zone_amps, pixel_ma
        self.white = white
        self.clock = clock
        self.effect = None                 # (preset, colour, brightness, on), from the controller
        self.scale = 1.0                   # the budget's dimming, 1 = none
        self.trim = 1.0                    # the measurement's extra dimming, on top
        self.amps = self.volts = None
        self.estimate = 0.0
        self._measured_at = None
        self._mcu_sent = None
        self.mcu_ok = self._mcu_present()
        self.ina_ok = self._ina_init()

    # ---- the parts
    def _mcu_present(self):
        try:
            return self.bus.read_i2c_block_data(self.mcu_address, 0x00, 2) == self.MCU_ID
        except OSError:
            return False

    def _ina_init(self):
        try:
            # Averaging 16, 1.1 ms conversions, shunt and bus continuously; 1 mA per count.
            cal = round(0.00512 / (self.CURRENT_LSB * self.SHUNT_OHMS))
            self.bus.write_i2c_block_data(self.ina_address, self.INA_CONFIG, [0x45, 0x27])
            self.bus.write_i2c_block_data(self.ina_address, self.INA_CALIBRATION, [cal >> 8, cal & 0xFF])
            return True
        except OSError:
            return False

    def _measure(self):
        now = self.clock()
        if not self.ina_ok or (self._measured_at is not None and now - self._measured_at < self.MEASURE_S):
            return
        self._measured_at = now
        try:
            hi, lo = self.bus.read_i2c_block_data(self.ina_address, self.INA_CURRENT, 2)
            raw = (hi << 8) | lo
            self.amps = (raw - 65536 if raw & 0x8000 else raw) * self.CURRENT_LSB
            hi, lo = self.bus.read_i2c_block_data(self.ina_address, self.INA_BUS_V, 2)
            self.volts = ((hi << 8) | lo) * 0.00125
        except OSError:
            return
        # Over budget although the estimate says not: dim a step at a time until it isn't,
        # and come back up slowly once there's room.
        if self.amps > self.max_amps:
            self.trim = max(0.1, self.trim * min(0.9, self.max_amps / self.amps))
        elif self.amps < 0.85 * self.max_amps and self.trim < 1.0:
            self.trim = min(1.0, self.trim * 1.05)

    # ---- what the controller calls
    def set_effect(self, preset, color, brightness, on):
        self.effect = (preset, tuple(color), float(brightness), bool(on))

    def _rgbw(self, rgb):
        r, g, b = (max(0, min(255, int(v))) for v in rgb)
        if not self.white:
            return r, g, b, 0
        w = min(r, g, b)
        return r - w, g - w, b - w, w

    def show(self, frame):
        self._measure()
        preset, color, brightness, on = self.effect or ("solid", frame[0], 1.0, any(frame[0]))
        rgbw = self._rgbw(frame[0])
        duties = [self.pwm._duty(v) for v in rgbw]
        # What the strips would draw, unscaled: each zone colour's share of zone_amps by its duty,
        # each pixel strip by its brightness and how much of full white its colour is.
        zone_load = sum(d / 4095 for d in duties) / self.COLOURS * self.zone_amps * self.ZONES
        mode = "off" if not on else ("rainbow" if preset == "rainbow" else "solid")
        whiteness = self.RAINBOW_LOAD if mode == "rainbow" else sum(color) / 765
        pixel_load = 0.0 if mode == "off" else sum(self.pixel_counts) * self.pixel_ma / 1000 * brightness * whiteness
        self.estimate = zone_load + pixel_load
        self.scale = min(1.0, self.max_amps / self.estimate) if self.estimate > 0 else 1.0
        k = self.scale * self.trim
        zone = [self.pwm._duty(v * k) for v in rgbw]
        self.pwm.set_duties(zone * self.ZONES)
        self._send_mcu(mode, color, brightness, k)

    def _send_mcu(self, mode, color, brightness, k):
        if not self.mcu_ok:
            self.mcu_ok = self._mcu_present()
            if not self.mcu_ok:
                return
        level = max(0, min(255, round(brightness * 255)))
        regs = []
        for n in self.pixel_counts:
            # mode, R, G, B, brightness, speed (turns a minute: the app's rainbow), order, flags, count
            regs.append([self.MODES[mode], *color, level, 9, self.order, 0, n & 0xFF, n >> 8])
        limit = max(0, min(255, round(255 * k)))
        sent = (limit, regs)
        now = self.clock()
        if sent == (self._mcu_sent[0] if self._mcu_sent else None) and now - self._mcu_sent[1] < 2.0:
            return
        self.bus.write_i2c_block_data(self.mcu_address, self.REG_MCU_LIMIT, [limit])
        for i, data in enumerate(regs):
            self.bus.write_i2c_block_data(self.mcu_address, self.REG_MCU_OUTPUT + 0x10 * i, data)
        self._mcu_sent = (sent, now)

    def telemetry(self):
        return {"amps": None if self.amps is None else round(self.amps, 2),
                "volts": None if self.volts is None else round(self.volts, 2),
                "estimated_amps": round(self.estimate, 1), "max_amps": self.max_amps,
                "dimmed_to": round(self.scale * self.trim, 2), "pixels_ok": self.mcu_ok, "monitor_ok": self.ina_ok}


class WS281xDriver:
    """Real WS2812B strip on a Raspberry Pi GPIO pin (default GPIO18 / PWM0).

    Requires: pip install rpi_ws281x, and running with root privileges.
    """

    def __init__(self, pixel_count, gpio_pin=18, freq_hz=800_000, dma=10, invert=False, channel=0):
        from rpi_ws281x import PixelStrip  # noqa: local import, hardware-only dependency

        self.pixel_count = pixel_count
        self._strip = PixelStrip(pixel_count, gpio_pin, freq_hz, dma, invert, 255, channel)
        self._strip.begin()

    def show(self, frame):
        for i, (r, g, b) in enumerate(frame):
            self._strip.setPixelColorRGB(i, r, g, b)
        self._strip.show()
