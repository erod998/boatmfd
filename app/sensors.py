"""Real boat sensors on a Raspberry Pi: engine data from NMEA 2000, or straight from analog senders.

Enabled with BOAT_SENSORS=n2k or BOAT_SENSORS=real; otherwise the simulators in
state.py are used. When real sensors are enabled, anything that can't be read
shows as no data (None): the dashboard never falls back to made-up numbers.

BOAT_SENSORS=n2k: an engine-data converter on the NMEA 2000 bus (for example the
Matsutec CX5003, which reads the boat's analog senders) supplies RPM, trim, oil
pressure, coolant temperature and fuel level. Battery voltage comes from an
ADS1115 on the Pi (BOAT_BATTERY_ADC=true) or the converter's alternator volts.

BOAT_SENSORS=real: the Pi reads the senders itself.
- ADS1115 (I2C 16-bit ADC) reads the resistive senders (fuel, trim, optional
  oil pressure) through a divider fed from the 3.3 V rail, and the battery
  through a resistor divider.
- The tach is an edge counter on a GPIO fed by a conditioned copy of the
  ignition coil's negative (tach) signal.
- DS18B20 probes on the Pi's 1-Wire bus give engine (coolant) temperature and,
  optionally, water temperature.

In both modes a NMEA 2000 fuel-flow sensor gives measured GPH (otherwise GPH is
estimated from RPM and corrected against real fill-ups), and a value heard on
the bus beats the same value read locally. A NMEA 2000 depth transducer (see
n2k_env.py) works the same way in both modes: depth, and sea temperature if the
transducer sends it, come from the bus whenever one is present.

Calibration is captured on the boat from /calibrate and stored in
data/calibration.json.
"""
import json
import threading
import time
from collections import deque
from pathlib import Path

from . import sender_tap
from .n2k_engine import N2kEngineData
from .n2k_env import N2kEnvData
from .state import BoatInfo, EngineInfo, estimate_gph
from .storage import read_dict, write_json

N2K_OIL_FULL_PSI = 145.04  # engine converters such as the CX5003 read oil senders as 0-10 bar (10-184 ohm)


class ADS1115:
    """Single-shot reads of a single-ended input, +/-4.096 V range, 128 SPS."""

    REG_CONVERSION = 0x00
    REG_CONFIG = 0x01
    FULL_SCALE_V = 4.096

    def __init__(self, bus, address=0x48, sleep=time.sleep):
        self.bus = bus
        self.address = address
        self._sleep = sleep

    def read_volts(self, channel):
        # OS=1 (start), MUX=100+channel (AINx vs GND), PGA=001 (+/-4.096 V), MODE=1 (single shot),
        # DR=100 (128 SPS), comparator off. Channel 0 gives the well-known 0xC383.
        config = 0x8000 | ((4 + channel) << 12) | 0x0200 | 0x0100 | 0x0080 | 0x0003
        self.bus.write_i2c_block_data(self.address, self.REG_CONFIG, [config >> 8, config & 0xFF])
        self._sleep(0.009)
        for _ in range(10):  # wait for the conversion-complete bit
            if self.bus.read_i2c_block_data(self.address, self.REG_CONFIG, 2)[0] & 0x80:
                break
            self._sleep(0.002)
        raw = int.from_bytes(bytes(self.bus.read_i2c_block_data(self.address, self.REG_CONVERSION, 2)), "big", signed=True)
        return raw * self.FULL_SCALE_V / 32768.0


def ohms_from_divider(v_node, v_rail, r_ref):
    """Sender to ground, r_ref up to the rail: v_node = v_rail * r_sender / (r_ref + r_sender)."""
    if v_node >= v_rail * 0.98:
        return None  # sender open or disconnected
    return r_ref * max(v_node, 0.0) / (v_rail - v_node)


class ADS1115Pair:
    """The sensor board's two converters as one eight-input device: 0-3 on the first, 4-7 on the second."""

    def __init__(self, first, second=None):
        self.first, self.second = first, second

    def read_volts(self, channel):
        if channel >= 4:
            if self.second is None:
                raise OSError("no second ADS1115 on this board")
            return self.second.read_volts(channel - 4)
        return self.first.read_volts(channel)


def linear_percent(value, at_0, at_100):
    span = at_100 - at_0
    if span == 0:
        return None
    return max(0.0, min(100.0, (value - at_0) / span * 100.0))


class TachCounter:
    """Turns ignition pulses into RPM.

    Count one edge per spark: the rising edge when the coil fires. The ring-down
    right after each spark can make extra edges, so after an accepted edge all
    edges are ignored for max(min_interval_s, half the recent interval).
    ppr is pulses per crankshaft revolution: 2 for a 4-cylinder, 4-stroke engine
    with a distributor (one spark per cylinder every two revolutions).
    """

    def __init__(self, ppr=2, min_interval_s=0.0025, stale_s=1.5, window=4, clock=time.monotonic):
        self.ppr = ppr
        self.min_interval_s = min_interval_s
        self.stale_s = stale_s
        self._clock = clock
        self._intervals = deque(maxlen=window)
        self._last_t = None
        self._last_seen = None
        self._lock = threading.Lock()

    def edge(self, t_ns):
        """Feed one edge with its timestamp in nanoseconds."""
        t = t_ns / 1e9
        with self._lock:
            if self._last_t is not None:
                dt = t - self._last_t
                recent = sum(self._intervals) / len(self._intervals) if self._intervals else 0.0
                if dt < max(self.min_interval_s, 0.5 * recent):
                    return
                if dt > self.stale_s:
                    self._intervals.clear()  # engine was stopped; start over
                else:
                    self._intervals.append(dt)
            self._last_t = t
            self._last_seen = self._clock()

    def rpm(self):
        with self._lock:
            if not self._intervals or self._clock() - self._last_seen > self.stale_s:
                return 0.0
            return 60.0 / ((sum(self._intervals) / len(self._intervals)) * self.ppr)


class LgpioEdgeInput:
    """Rising-edge timestamps from a GPIO via lgpio (works on every Pi, including the Pi 5)."""

    def __init__(self, gpio, on_edge, chip=0, pull="down"):
        import lgpio

        self._lgpio = lgpio
        self._handle = lgpio.gpiochip_open(chip)
        flags = {"down": lgpio.SET_PULL_DOWN, "up": lgpio.SET_PULL_UP, "none": lgpio.SET_PULL_NONE}[pull]
        lgpio.gpio_claim_alert(self._handle, gpio, lgpio.RISING_EDGE, flags)
        self._callback = lgpio.callback(self._handle, gpio, lgpio.RISING_EDGE, lambda chip, g, level, ts: on_edge(ts))


class W1Probes:
    """DS18B20 waterproof probes on the Pi's 1-Wire bus (kernel driver: /sys/bus/w1/devices/28-*)."""

    def __init__(self, base="/sys/bus/w1/devices"):
        self.base = Path(base)

    def devices(self):
        return sorted(p.name for p in self.base.glob("28-*")) if self.base.exists() else []

    def read_f(self, device):
        folder = self.base / device
        try:
            celsius = int((folder / "temperature").read_text().strip()) / 1000.0
        except (OSError, ValueError):
            try:  # older kernels only expose w1_slave: "... crc=xx YES" then "... t=23125"
                lines = (folder / "w1_slave").read_text().splitlines()
                if len(lines) < 2 or not lines[0].strip().endswith("YES"):
                    return None
                celsius = int(lines[1].split("t=")[1]) / 1000.0
            except (OSError, ValueError, IndexError):
                return None
        if celsius == 85.0 or not -55.0 <= celsius <= 125.0:  # 85 C is the chip's power-on value
            return None
        return celsius * 9 / 5 + 32


class Calibration:
    DEFAULTS = {
        # The *_ohm values are for senders the Pi reads itself; the *_pct values are for levels arriving over NMEA 2000.
        "fuel": {"empty_ohm": 240.0, "full_ohm": 33.0, "empty_pct": 0.0, "full_pct": 100.0},   # US standard sender
        "trim": {"down_ohm": None, "up_ohm": None, "down_pct": None, "up_pct": None},         # measure on the boat
        "oil": {"zero_ohm": 10.0, "full_ohm": 180.0, "full_psi": 80.0,  # US standard 0-80 psi sender
                "n2k_full_psi": N2K_OIL_FULL_PSI},  # the sender's real full scale, if the converter assumes 10 bar
        "battery": {"scale": 1.0},
        "tach": {"scale": 1.0},
        "probes": {"engine": "", "water": "", "engine_offset_f": 0.0, "water_offset_f": 0.0},
        "fuel_burn": {"scale": 1.0, "used_gal": 0.0},
        "depth": {"offset_ft": 0.0},  # added to the transducer's own reading; positive raises the displayed depth
        # BOAT_SENDER_WIRING=tap: captured (reading, value) points, see sender_tap.py. The fuel
        # sender's empty/full ohms above are what make two fuel points enough for the whole scale.
        "tap": {"fuel_points": [], "trim_points": [], "oil_points": [], "ratiometric": True},
    }

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = json.loads(json.dumps(self.DEFAULTS))
        # Anything unreadable is set aside and logged rather than silently replaced by defaults on
        # the next write -- this file is flushed every minute, so "next write" is never far off, and
        # it holds the sender calibration that took an afternoon with a multimeter to get right.
        for section, values in read_dict(self.path).items():
            if section in self._data and isinstance(values, dict):
                self._data[section].update({k: v for k, v in values.items() if k in self._data[section]})

    def get(self, section, key):
        with self._lock:
            return self._data[section][key]

    def set(self, section, key, value):
        with self._lock:
            self._data[section][key] = value
            write_json(self.path, self._data, indent=2)

    def snapshot(self):
        with self._lock:
            return json.loads(json.dumps(self._data))


class SensorHub:
    SAMPLE_S = 0.25
    PROBE_S = 2.0
    FLUSH_S = 60.0
    # Fuel sloshes, so it's heavily smoothed; trim and oil move quickly.
    SMOOTHING = {"fuel": 0.05, "trim": 0.3, "oil": 0.3, "battery": 0.2}

    def __init__(self, settings, calibration, adc=None, tach=None, w1=None, n2k=None, env=None, clock=time.monotonic):
        self.settings = settings
        self.cal = calibration
        self.adc = adc
        self.tach = tach
        self.w1 = w1
        self.n2k = n2k  # N2kEngineData or None
        self.env = env  # N2kEnvData (depth, sea temperature) or None
        self._clock = clock
        self.tap = settings.sensors == "real" and settings.sender_wiring == "tap"
        if settings.sensors == "n2k":  # the senders belong to the engine converter; the Pi only measures the battery
            self.channels = {"battery": 2} if settings.battery_adc else {}
        elif self.tap:  # the sensor board: see SENSOR_BOARD.md for which input is which
            self.channels = {"trim": 1, "battery": 2, "gauge": 3}
            if settings.fuel_sender:
                self.channels["fuel"] = 0
            if settings.oil_sender:
                self.channels["oil"] = 4
        else:
            self.channels = {"trim": 1, "battery": 2}
            if settings.fuel_sender:
                self.channels["fuel"] = 0
            if settings.oil_sender:
                self.channels["oil"] = 3
        self._smoothed = {}
        self._ratio_history = {}   # tap name -> deque of (time, smoothed ratio), for the settle check
        self._readings = {}
        self._probe_temps = {}
        self._used_gal = float(calibration.get("fuel_burn", "used_gal"))
        self._last_sample = None
        self._last_flush = clock()
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self):
        self.sample_once()
        self.sample_probes()
        threading.Thread(target=self._run, name="sensors", daemon=True).start()
        threading.Thread(target=self._run_probes, name="probes", daemon=True).start()

    def stop(self):
        self._stop.set()
        # Fuel used is otherwise only written once a minute; don't lose the last one on a restart.
        self.cal.set("fuel_burn", "used_gal", round(self._used_gal, 3))

    # Both loops have to outlive a bad read. Unguarded, one exception (a GPIO or I2C fault, a
    # driver bug) ended the thread for good -- and _readings kept its last values, so the gauges
    # went on showing the last RPM, fuel and oil pressure as if they were live. On an error the
    # readings are dropped instead, which the gauges show as "--", and the next pass tries again.
    def _run(self):
        failing = None
        while not self._stop.wait(self.SAMPLE_S):
            try:
                self.sample_once()
                failing = None
            except Exception as exc:
                with self._lock:
                    self._readings = {}
                if str(exc) != failing:
                    print(f"[sensors] sampling failed ({exc}); showing no data and retrying")
                    failing = str(exc)

    def _run_probes(self):
        failing = None
        while not self._stop.wait(self.PROBE_S):  # each DS18B20 read takes ~0.75 s, so keep it off the fast loop
            try:
                self.sample_probes()
                failing = None
            except Exception as exc:
                with self._lock:
                    self._probe_temps = {}
                if str(exc) != failing:
                    print(f"[sensors] temperature probes failed ({exc}); showing no data and retrying")
                    failing = str(exc)

    def _smooth(self, key, value):
        if value is None:
            self._smoothed.pop(key, None)
            return None
        previous = self._smoothed.get(key)
        smoothed = value if previous is None else previous + self.SMOOTHING[key] * (value - previous)
        self._smoothed[key] = smoothed
        return smoothed

    def sample_probes(self):
        temps = {device: self.w1.read_f(device) for device in self.w1.devices()} if self.w1 else {}
        with self._lock:
            self._probe_temps = temps

    def sample_once(self):
        s = self.settings
        volts = {}
        for name, channel in self.channels.items():
            try:
                volts[name] = self.adc.read_volts(channel) if self.adc else None
            except OSError:
                volts[name] = None

        readings = {}
        if self.tap:
            # The ratio is formed per sample and smoothed afterwards: smoothing the sender and supply
            # voltages separately would make the level jump every time the engine starts charging.
            scale = (s.tap_r_top + s.tap_r_bottom) / s.tap_r_bottom
            gauge_v = None if volts.get("gauge") is None else volts["gauge"] * scale
            readings["gauge_v"] = gauge_v
            ratiometric = bool(self.cal.get("tap", "ratiometric"))
            for name in ("fuel", "trim", "oil"):
                if name not in self.channels:
                    continue
                tap_v = None if volts.get(name) is None else volts[name] * scale
                readings[name + "_tap_v"] = tap_v
                ratio = self._smooth(name, sender_tap.tap_ratio(tap_v, gauge_v, ratiometric))
                readings[name + "_ratio"] = ratio
                history = self._ratio_history.setdefault(name, deque(maxlen=64))
                if ratio is None:
                    history.clear()
                else:
                    history.append((self._clock(), ratio))
        else:
            for name in ("fuel", "trim", "oil"):
                v = volts.get(name)
                ohms = None if v is None else ohms_from_divider(v, s.rail_volts, s.sender_ref_ohms)
                readings[name + "_ohms"] = self._smooth(name, ohms)
        v = volts.get("battery")
        raw_battery = None if v is None else v * (s.batt_r_top + s.batt_r_bottom) / s.batt_r_bottom
        readings["battery_v_raw"] = self._smooth("battery", raw_battery)
        readings["rpm_raw"] = self.tach.rpm() if self.tach else None

        now = self._clock()
        with self._lock:
            self._readings = readings
        gph, _ = self._fuel_flow(self._rpm_raw(readings))
        if self._last_sample is not None and gph:
            self._used_gal += gph * (now - self._last_sample) / 3600.0
        self._last_sample = now
        if now - self._last_flush >= self.FLUSH_S:
            self._last_flush = now
            self.cal.set("fuel_burn", "used_gal", round(self._used_gal, 3))

    def readings(self):
        with self._lock:
            return dict(self._readings)

    def _rpm_raw(self, readings=None):
        """RPM before tach calibration: the engine converter's when it is talking, otherwise the local tach's."""
        heard = self.n2k.rpm() if self.n2k else None
        if heard is not None:
            return heard
        return (self.readings() if readings is None else readings).get("rpm_raw")

    def _calibrated_rpm(self, raw):
        return None if raw is None else raw * self.cal.get("tach", "scale")

    def _fuel_flow(self, rpm_raw):
        """(gallons/hour, is_estimate): measured by a NMEA 2000 fuel-flow sensor when one is talking,
        otherwise estimated from RPM. Either way the fill-up correction scale applies."""
        scale = self.cal.get("fuel_burn", "scale")
        measured = self.n2k.fuel_gph() if self.n2k else None
        if measured is not None:
            return measured * scale, False
        rpm = self._calibrated_rpm(rpm_raw)
        if rpm is None:
            return None, True
        s = self.settings
        return estimate_gph(rpm, s.engine_hp, s.engine_wot_rpm) * scale, True

    def _probe_raw(self, role):
        """(device, raw temperature in F) for the probe assigned to this role, if any."""
        with self._lock:
            temps = dict(self._probe_temps)
        device = self.cal.get("probes", role) or None
        if not device and role == "engine" and self.settings.sensors != "n2k":  # unassigned: the first probe that isn't the water probe is the engine
            free = [d for d in sorted(temps) if d != self.cal.get("probes", "water")]
            device = free[0] if free else None
        return device, (temps.get(device) if device else None)

    def _probe_temp(self, role):
        _, raw = self._probe_raw(role)
        return None if raw is None else raw + self.cal.get("probes", role + "_offset_f")

    def _engine_temp_raw(self):
        """Coolant temperature in F before the offset: from the bus if a converter sends it, else the engine probe."""
        heard = self.n2k.engine_temp_f() if self.n2k else None
        return heard if heard is not None else self._probe_raw("engine")[1]

    def water_temp_f(self):
        heard = self.env.water_temp_f() if self.env else None
        return heard if heard is not None else self._probe_temp("water")

    def depth_ft(self):
        return self.env.depth_ft(self.cal.get("depth", "offset_ft")) if self.env else None

    def engine(self):
        r = self.readings()
        n2k = self.n2k
        fuel = trim = oil = None

        level = n2k.fuel_level_pct() if n2k else None
        if level is not None:  # a NMEA 2000 source (the converter's fuel input, or a tank-level adapter) beats the analog sender
            lo, hi = self.cal.get("fuel", "empty_pct"), self.cal.get("fuel", "full_pct")
            fuel = level if (lo, hi) == (0.0, 100.0) else linear_percent(level, lo, hi)
        elif self.tap:
            fuel = self._tap_fuel(r)[0]
        elif r.get("fuel_ohms") is not None:
            fuel = linear_percent(r["fuel_ohms"], self.cal.get("fuel", "empty_ohm"), self.cal.get("fuel", "full_ohm"))

        heard_trim = n2k.trim_pct() if n2k else None
        if heard_trim is not None:
            down, up = self.cal.get("trim", "down_pct"), self.cal.get("trim", "up_pct")
            if down is not None and up is not None:
                trim = linear_percent(heard_trim, down, up)
        elif self.tap:
            trim = sender_tap.trim_level(r.get("trim_ratio"), self.cal.get("tap", "trim_points"))[0]
        else:
            down, up = self.cal.get("trim", "down_ohm"), self.cal.get("trim", "up_ohm")
            if r.get("trim_ohms") is not None and down is not None and up is not None:
                trim = linear_percent(r["trim_ohms"], down, up)

        heard_oil = n2k.oil_pressure_psi() if n2k else None
        if heard_oil is not None:
            oil = heard_oil * self.cal.get("oil", "n2k_full_psi") / N2K_OIL_FULL_PSI
        elif self.tap:
            oil = sender_tap.oil_pressure(r.get("oil_ratio"), self.cal.get("tap", "oil_points"),
                                          bool(self.cal.get("tap", "ratiometric")))[0]
        elif r.get("oil_ohms") is not None:
            pct = linear_percent(r["oil_ohms"], self.cal.get("oil", "zero_ohm"), self.cal.get("oil", "full_ohm"))
            oil = None if pct is None else pct / 100.0 * self.cal.get("oil", "full_psi")

        rpm_raw = self._rpm_raw(r)
        rpm = None if rpm_raw is None else round(self._calibrated_rpm(rpm_raw))
        gph, gph_is_estimate = self._fuel_flow(rpm_raw)
        temp = self._engine_temp_raw()
        coolant = None if temp is None else temp + self.cal.get("probes", "engine_offset_f")
        return {
            "rpm": rpm,
            "oil_pressure_psi": oil,
            "fuel_pct": fuel,
            "fuel_gph": None if gph is None else round(gph, 1),
            "fuel_gph_est": gph_is_estimate,
            "trim_pct": trim,
            "coolant_f": None if coolant is None else round(coolant),
        }

    def _tap_fuel(self, r=None):
        r = self.readings() if r is None else r
        return sender_tap.fuel_level(r.get("fuel_ratio"), self.cal.get("tap", "fuel_points"),
                                     self.cal.get("fuel", "empty_ohm"), self.cal.get("fuel", "full_ohm"))

    def _tap_status(self):
        r = self.readings()
        ratiometric = bool(self.cal.get("tap", "ratiometric"))
        out = {"gauge_v": r.get("gauge_v"), "ratiometric": ratiometric,
               "gauges_on": r.get("gauge_v") is not None and r["gauge_v"] >= sender_tap.MIN_GAUGE_SUPPLY_V}
        how = {
            "fuel": lambda: self._tap_fuel(r),
            "trim": lambda: sender_tap.trim_level(r.get("trim_ratio"), self.cal.get("tap", "trim_points")),
            "oil": lambda: sender_tap.oil_pressure(r.get("oil_ratio"), self.cal.get("tap", "oil_points"), ratiometric),
        }
        for name, compute in how.items():
            if name not in self.channels:
                continue
            value, detail = compute()
            # The conversion's own "points" is a count; the page wants the list, so it goes last.
            out[name] = {**detail, "tap_v": r.get(name + "_tap_v"), "ratio": r.get(name + "_ratio"), "value": value,
                         "points": sender_tap.clean_points(self.cal.get("tap", name + "_points"))}
        return out

    # How close a new point's value must be to an old one to replace it rather than join it.
    TAP_SAME = {"fuel": 3.0, "trim": 3.0, "oil": 2.0}
    TAP_RANGE = {"fuel": (0.0, 100.0), "trim": (0.0, 100.0), "oil": (0.0, 150.0)}

    SETTLE_S = 3.0          # a captured reading must not have moved more than this much...
    SETTLE_FRACTION = 0.015  # ...(as a share of itself) over this long

    def _settled(self, name):
        """Has this tap's smoothed reading stopped moving? A point saved while it is still gliding
        toward a new level is simply wrong, and one wrong point bends the whole fitted curve -- in a
        test, a half-tank point saved a few seconds early put a 12% tank outside the plausible range.
        Right after the key comes on there is no history yet, and none is needed: the smoothing
        restarts at the exact current value."""
        history = self._ratio_history.get(name)
        if not history:
            return True
        now_t, now_r = history[-1]
        earlier = [r for t, r in history if now_t - t >= self.SETTLE_S]
        if not earlier:
            return True
        return abs(now_r - earlier[-1]) <= max(0.002, self.SETTLE_FRACTION * now_r)

    def _capture_tap(self, name, value):
        if name not in self.channels:
            raise ValueError(f"the {name} tap is not enabled")
        lo, hi = self.TAP_RANGE[name]
        if value is None or not lo <= value <= hi:
            raise ValueError(f"enter a value from {lo:g} to {hi:g}")
        r = self.readings()
        ratio = r.get(name + "_ratio")
        if ratio is None:
            if self.cal.get("tap", "ratiometric") and (r.get("gauge_v") or 0) < sender_tap.MIN_GAUGE_SUPPLY_V:
                raise ValueError("the gauges have no power: turn the key to ON (the engine can stay off)")
            raise ValueError(f"no reading from the {name} tap: is its wire connected at the gauge?")
        if not self._settled(name):
            raise ValueError(f"the {name} reading is still settling: wait a few seconds, then save again")
        points = sender_tap.add_point(self.cal.get("tap", name + "_points"), ratio, value, self.TAP_SAME[name])
        self.cal.set("tap", name + "_points", points)
        unit = "psi" if name == "oil" else "%"
        message = f"Saved {name} {value:g}{unit} ({len(points)} point{'s' if len(points) != 1 else ''})"
        note = self._tap_status().get(name, {}).get("note")
        return f"{message}. {note[0].upper()}{note[1:]}." if note else message

    def battery_volts(self):
        raw = self.readings().get("battery_v_raw")
        if raw is not None:
            return raw * self.cal.get("battery", "scale")
        return self.n2k.alternator_v() if self.n2k else None  # no ADC: the converter's alternator volts, if it sends them

    def engine_heard(self):
        """Is the NMEA 2000 engine converter alive? None if this hub isn't listening to the bus."""
        return self.n2k.heard_engine() if self.n2k else None

    def status(self):
        """Everything the calibration page shows."""
        with self._lock:
            probes = dict(self._probe_temps)
        return {
            "real": True,
            "mode": "tap" if self.tap else self.settings.sensors,
            "tap": self._tap_status() if self.tap else None,
            "readings": self.readings(),
            "engine": self.engine(),
            "battery_volts": self.battery_volts(),
            "probes": probes,
            "fuel_used_gal": round(self._used_gal, 2),
            "oil_enabled": "oil" in self.channels or self.settings.sensors == "n2k",
            "fuel_sender_enabled": "fuel" in self.channels,
            "n2k": self.n2k.status() if self.n2k else None,
            "n2k_raw": self.n2k.raw() if self.n2k else None,
            "env": self.env.status() if self.env else None,
            "depth_ft": self.depth_ft(),
            "calibration": self.cal.snapshot(),
        }

    def capture(self, action, value=None, device=None):
        """Store a calibration point from the current live reading. Returns a message for the UI."""
        r = self.readings()
        if self.settings.sensors == "n2k":  # values arrive as percentages over the bus, not as ohms
            bus_points = {
                "fuel_empty": ("fuel", "empty_pct", "fuel_level_pct"), "fuel_full": ("fuel", "full_pct", "fuel_level_pct"),
                "trim_down": ("trim", "down_pct", "trim_pct"), "trim_up": ("trim", "up_pct", "trim_pct"),
            }
            if action in bus_points:
                section, key, source = bus_points[action]
                heard = self.n2k.raw().get(source) if self.n2k else None
                if heard is None:
                    raise ValueError("nothing heard on NMEA 2000 for that reading (is the converter powered, and is its instance number the one in the settings?)")
                self.cal.set(section, key, round(heard, 1))
                return "Saved %.1f%%" % heard
        if self.tap:
            shortcuts = {"fuel_full": ("fuel", 100.0), "fuel_empty": ("fuel", 0.0), "trim_down": ("trim", 0.0),
                         "trim_up": ("trim", 100.0), "oil_zero": ("oil", 0.0)}
            if action in shortcuts:
                return self._capture_tap(*shortcuts[action])
            if action in ("fuel_point", "trim_point", "oil_point"):
                return self._capture_tap(action.split("_")[0], value)
            if action == "tap_clear":
                if device not in ("fuel", "trim", "oil"):
                    raise ValueError("clear which tap: fuel, trim or oil?")
                self.cal.set("tap", device + "_points", [])
                return f"Cleared the {device} points"
            if action == "tap_ratiometric":
                # A point is a share of the supply in one mode and plain volts in the other; kept
                # across the switch they would be read in the wrong units, so they go with it.
                on = bool(value)
                if on != bool(self.cal.get("tap", "ratiometric")):
                    for name in ("fuel", "trim", "oil"):
                        self.cal.set("tap", name + "_points", [])
                self.cal.set("tap", "ratiometric", on)
                return ("Readings are relative to the gauge supply" if on else "Readings are plain volts") + \
                    "; saved points were cleared, so capture them again"
        if action == "oil_full_scale":
            if not value or value <= 0:
                raise ValueError("enter your oil sender's full-scale pressure in psi (US-standard senders are 80)")
            self.cal.set("oil", "n2k_full_psi", float(value))
            return "Oil pressure scaled for a %g psi sender" % value
        points = {
            "fuel_empty": ("fuel", "empty_ohm", "fuel_ohms"),
            "fuel_full": ("fuel", "full_ohm", "fuel_ohms"),
            "trim_down": ("trim", "down_ohm", "trim_ohms"),
            "trim_up": ("trim", "up_ohm", "trim_ohms"),
            "oil_zero": ("oil", "zero_ohm", "oil_ohms"),
            "oil_full": ("oil", "full_ohm", "oil_ohms"),
        }
        if action in points:
            section, key, source = points[action]
            if r.get(source) is None:
                raise ValueError("no reading from that sender (disconnected or not enabled)")
            self.cal.set(section, key, round(r[source], 1))
            return "Saved %.1f ohms" % r[source]
        if action == "battery":
            if not value or value <= 0 or not r.get("battery_v_raw"):
                raise ValueError("enter the voltage your multimeter shows")
            self.cal.set("battery", "scale", value / r["battery_v_raw"])
            return "Battery calibrated to %.2f V" % value
        if action == "tach":
            raw = self._rpm_raw(r)
            if not value or value <= 0 or not raw:
                raise ValueError("engine must be running; enter the RPM the analog tach shows")
            self.cal.set("tach", "scale", value / raw)
            return "RPM scaled so this reads %d" % value
        if action == "depth_offset":
            heard = self.env.depth_ft(0.0) if self.env else None
            if value is None or heard is None:
                raise ValueError("enter the true depth (e.g. from a lead line or dock marker); needs a depth transducer on the bus")
            self.cal.set("depth", "offset_ft", round(value - heard, 2))
            return "Depth offset %+.2f ft" % (value - heard)
        if action == "engine_temp_offset":
            raw = self._engine_temp_raw()
            if value is None or raw is None:
                raise ValueError("enter the true temperature (e.g. from an infrared thermometer); needs an engine temperature reading")
            self.cal.set("probes", "engine_offset_f", round(value - raw, 1))
            return "Engine temperature offset %+.1f F" % (value - raw)
        if action in ("probe_engine", "probe_water"):
            self.cal.set("probes", action.split("_")[1], device or "")
            return "Probe assigned" if device else "Probe assignment cleared"
        if action == "fuel_fill":
            if not value or value <= 0:
                raise ValueError("enter the gallons you just pumped")
            estimated = self._used_gal
            self._used_gal = 0.0
            self.cal.set("fuel_burn", "used_gal", 0.0)
            if estimated < 1.0:
                return "Counter reset (less than 1 gal estimated since the last fill, too little to correct the estimate)"
            factor = max(0.3, min(3.0, value / estimated))
            self.cal.set("fuel_burn", "scale", round(self.cal.get("fuel_burn", "scale") * factor, 3))
            return "Counted %.1f gal, pumped %.1f gal: fuel burn scaled x%.2f" % (estimated, value, factor)
        raise ValueError("unknown calibration action '%s'" % action)


class RealEngineInfo:
    simulated = False
    MAX_RPM = 6000

    def __init__(self, hub, redline_rpm):
        self.hub = hub
        self.redline_rpm = redline_rpm

    def read(self, sog_kn):
        return {**self.hub.engine(), "redline_rpm": self.redline_rpm, "max_rpm": self.MAX_RPM}


class RealBoatInfo:
    """Battery, depth and water temperature are all real if wired: depth and (if the transducer reports it)
    water temperature come from a NMEA 2000 depth transducer on the shared bus; anything not wired is no data."""

    simulated = False

    def __init__(self, hub):
        self.hub = hub

    def read(self, engine_running):
        return {"battery_voltage": self.hub.battery_volts(), "depth_ft": self.hub.depth_ft(), "water_temp_f": self.hub.water_temp_f()}


def make_sensor_sources(settings, data_dir, node=None):
    """Return (engine_source, boat_source, hub_or_None). node is the shared NMEA 2000 node, if any."""
    if settings.sensors not in ("real", "n2k"):
        return EngineInfo(), BoatInfo(), None

    cal = Calibration(Path(data_dir) / "calibration.json")
    adc = tach = None
    if settings.sensors == "real" or settings.battery_adc:
        try:
            from smbus2 import SMBus

            bus = SMBus(settings.i2c_bus)
            adc = ADS1115(bus, settings.ads1115_address)
            if settings.sensors == "real" and settings.sender_wiring == "tap" and settings.oil_sender:
                adc = ADS1115Pair(adc, ADS1115(bus, settings.ads1115_address2))
        except Exception as exc:  # pragma: no cover - hardware-dependent
            print(f"[sensors] could not open the ADS1115 on I2C bus {settings.i2c_bus} ({exc}); analog inputs will show no data")
    if settings.sensors == "real":
        try:
            tach = TachCounter(settings.tach_ppr)
            tach.input = LgpioEdgeInput(settings.tach_gpio, tach.edge, pull=settings.tach_pull)
        except Exception as exc:  # pragma: no cover - hardware-dependent
            print(f"[sensors] could not watch GPIO {settings.tach_gpio} for the tach ({exc}); RPM will show no data")
            tach = None
    elif node is None:
        print("[sensors] BOAT_SENSORS=n2k needs the NMEA 2000 interface (set BOAT_CAN=can0 and bring it up); engine values will show no data")

    n2k = N2kEngineData(node, settings.n2k_engine_instance, settings.n2k_fuel_tank_instance, settings.n2k_temp_field) if node else None
    env = N2kEnvData(node, settings.n2k_depth_instance) if node else None
    hub = SensorHub(settings, cal, adc, tach, W1Probes(settings.w1_dir), n2k, env)
    hub.start()
    return RealEngineInfo(hub, settings.redline_rpm), RealBoatInfo(hub), hub
