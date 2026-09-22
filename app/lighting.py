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
            return {
                "preset": self.preset,
                "solid_color": self.solid_color,
                "brightness": self.brightness,
                "on": self.on,
                "pixel_count": self.pixel_count,
                "frame": list(self._frame),
            }


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
