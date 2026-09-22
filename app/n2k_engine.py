"""Engine, fuel and tank data broadcast on NMEA 2000.

Sent by engine-data converters (such as the Matsutec CX5003, which turns analog senders
into NMEA 2000), fuel-flow sensors and tank-level adapters:

- PGN 127488 "Engine Parameters, Rapid Update": engine speed (0.25 rpm) and tilt/trim (%).
- PGN 127489 "Engine Parameters, Dynamic": oil pressure (100 Pa), oil temperature (0.1 K),
  engine (coolant) temperature (0.01 K), alternator potential (0.01 V) and fuel rate (0.1 L/h).
- PGN 127505 "Fluid Level": tank level (0.004 %), tank type (0 = fuel) and capacity (0.1 L).

Field layouts are from canboat's PGN database. Values a device marks as not available, or
as an error, come back as None (never as a number).
"""
import threading
import time

PGN_ENGINE_RAPID = 127488
PGN_ENGINE_DYNAMIC = 127489
PGN_FLUID_LEVEL = 127505
LITERS_PER_GALLON = 3.785411784
PA_PER_PSI = 6894.757
TANK_TYPE_FUEL = 0
# Fields that read as a plain zero when a device has nothing to measure (some send 0 instead of "not available").
# They are only believed once that source has reported a nonzero value; after that, a zero is a real zero.
ZERO_MEANS_UNMEASURED = ("fuel_gph", "oil_pressure_pa")
TANK_TYPES = {0: "fuel", 1: "water", 2: "gray water", 3: "live well", 4: "oil", 5: "black water"}


def _u16(payload, offset):
    """Unsigned 16-bit field; None if the payload is too short or the value is a not-available/error/reserved code."""
    if len(payload) < offset + 2:
        return None
    raw = int.from_bytes(payload[offset:offset + 2], "little")
    return raw if raw < 0xFFFD else None


def _i16(payload, offset):
    if len(payload) < offset + 2:
        return None
    raw = int.from_bytes(payload[offset:offset + 2], "little", signed=True)
    return raw if raw < 0x7FFD else None


def _kelvin_to_c(kelvin):
    """Temperatures outside -73..227 C are a device's filler (zero, open sender), not a reading."""
    return kelvin - 273.15 if kelvin is not None and 200.0 <= kelvin <= 500.0 else None


def parse_engine_rapid(payload):
    """Engine speed (rpm) and tilt/trim (%) from PGN 127488, or None if the payload is too short."""
    if len(payload) < 6:
        return None
    rpm_raw = _u16(payload, 1)
    trim_raw = int.from_bytes(payload[5:6], "little", signed=True)  # signed 8-bit, 1 %; 0x7D-0x7F are reserved/error/not available
    return {
        "instance": payload[0],
        "rpm": None if rpm_raw is None else rpm_raw * 0.25,
        "trim_pct": trim_raw if trim_raw < 0x7D else None,
    }


def parse_engine_dynamic(payload):
    """Oil pressure, temperatures, alternator volts and fuel rate from PGN 127489, or None if the payload is too short."""
    if len(payload) < 11:
        return None
    oil_pa = _u16(payload, 1)
    oil_temp = _u16(payload, 3)
    engine_temp = _u16(payload, 5)
    alternator = _i16(payload, 7)
    fuel_raw = _i16(payload, 9)  # signed 16-bit at bit 72, 0.1 L/h
    return {
        "instance": payload[0],
        "oil_pressure_pa": None if oil_pa is None else oil_pa * 100.0,
        "oil_temp_c": _kelvin_to_c(None if oil_temp is None else oil_temp * 0.1),
        "engine_temp_c": _kelvin_to_c(None if engine_temp is None else engine_temp * 0.01),
        "alternator_v": None if alternator is None or alternator < 100 else alternator * 0.01,  # under 1 V is a filler zero
        "fuel_gph": None if fuel_raw is None or fuel_raw < 0 else fuel_raw * 0.1 / LITERS_PER_GALLON,
    }


def parse_fluid_level(payload):
    """Tank type, instance, level (%) and capacity (gal) from PGN 127505, or None if too short."""
    if len(payload) < 7:
        return None
    raw_level = int.from_bytes(payload[1:3], "little", signed=True)  # signed 16-bit, 0.004 %
    raw_capacity = int.from_bytes(payload[3:7], "little")           # unsigned 32-bit, 0.1 L
    return {
        "instance": payload[0] & 0x0F,
        "type": payload[0] >> 4,
        "level_pct": min(100.0, raw_level * 0.004) if 0 <= raw_level < 0x7FFD else None,
        "capacity_gal": raw_capacity * 0.1 / LITERS_PER_GALLON if raw_capacity < 0xFFFFFFFD else None,
    }


def c_to_f(celsius):
    return None if celsius is None else celsius * 9 / 5 + 32


class N2kEngineData:
    """Keeps the latest engine, fuel and tank values seen on the bus; stale readings count as no data.

    Several devices may talk about the same engine (a converter sending RPM, oil and temperature, and a
    fuel-flow sensor sending fuel rate), so each value is tracked separately and the freshest one for the
    configured engine instance wins. Fuel rate and oil pressure need a first nonzero reading before a
    source's zeros count (see ZERO_MEANS_UNMEASURED), so an unwired input shows no data, not 0.
    """

    # Seconds before a value counts as gone: a few missed broadcasts (RPM comes ~10x/s, dynamic ~2x/s, levels every ~2.5 s).
    STALE_S = {PGN_ENGINE_RAPID: 2.0, PGN_ENGINE_DYNAMIC: 4.0, PGN_FLUID_LEVEL: 5.0}
    REPORT_S = 30.0  # how long the calibration page keeps listing a device that has gone quiet

    def __init__(self, node, engine_instance=0, tank_instance=0, temp_field="engine", clock=time.monotonic):
        self.engine_instance = engine_instance
        self.tank_instance = tank_instance
        self.temp_field = "oil_temp_c" if temp_field == "oil" else "engine_temp_c"  # which temperature is the coolant
        self._clock = clock
        self._engine = {}   # (source, engine instance) -> {field: (time seen, value, pgn)}
        self._seen = {}     # (source, engine instance) -> time of the last engine message of any kind
        self._proven = set()  # (source, engine instance, field) that has reported a nonzero value, for ZERO_MEANS_UNMEASURED fields
        self._levels = {}   # (source, tank instance, tank type) -> (time seen, level %, capacity gal)
        self._lock = threading.Lock()
        node.subscribe(PGN_FLUID_LEVEL, PGN_ENGINE_RAPID)  # single-frame PGNs (127489 is fast-packet, always delivered)
        node.add_listener(self._on_message)

    def _on_message(self, pgn, source, payload):
        now = self._clock()
        if pgn == PGN_ENGINE_RAPID:
            parsed = parse_engine_rapid(payload)
        elif pgn == PGN_ENGINE_DYNAMIC:
            parsed = parse_engine_dynamic(payload)
        elif pgn == PGN_FLUID_LEVEL:
            parsed = parse_fluid_level(payload)
            if parsed:
                with self._lock:
                    self._levels[(source, parsed["instance"], parsed["type"])] = (now, parsed["level_pct"], parsed["capacity_gal"])
            return
        else:
            return
        if not parsed:
            return
        key = (source, parsed["instance"])
        with self._lock:
            fields = self._engine.setdefault(key, {})
            for name, value in parsed.items():
                if name == "instance":
                    continue
                if name in ZERO_MEANS_UNMEASURED and value:
                    self._proven.add(key + (name,))
                if value is None:
                    fields.pop(name, None)  # the device just said "not available": drop the old number, don't let it linger
                else:
                    fields[name] = (now, value, pgn)
            self._seen[key] = now

    def _value(self, name):
        now = self._clock()
        with self._lock:
            fresh = [(t, v) for key, fields in self._engine.items() if key[1] == self.engine_instance
                     for field, (t, v, pgn) in fields.items()
                     if field == name and now - t <= self.STALE_S[pgn]
                     # a source that has only ever said zero has nothing connected (converters often fill unused fields with zero)
                     and (name not in ZERO_MEANS_UNMEASURED or key + (name,) in self._proven)]
        return max(fresh)[1] if fresh else None

    def rpm(self):
        return self._value("rpm")

    def trim_pct(self):
        return self._value("trim_pct")

    def oil_pressure_psi(self):
        pa = self._value("oil_pressure_pa")
        return None if pa is None else pa / PA_PER_PSI

    def engine_temp_f(self):
        return c_to_f(self._value(self.temp_field))

    def alternator_v(self):
        return self._value("alternator_v")

    def fuel_gph(self):
        return self._value("fuel_gph")

    def fuel_level_pct(self):
        now = self._clock()
        with self._lock:
            fresh = [(t, level) for (_, instance, tank_type), (t, level, _) in self._levels.items()
                     if tank_type == TANK_TYPE_FUEL and instance == self.tank_instance and level is not None
                     and now - t <= self.STALE_S[PGN_FLUID_LEVEL]]
        return max(fresh)[1] if fresh else None

    def heard_engine(self):
        """True if any engine message for the configured instance is fresh (the converter is alive)."""
        now = self._clock()
        with self._lock:
            return any(key[1] == self.engine_instance and now - t <= self.STALE_S[PGN_ENGINE_DYNAMIC] for key, t in self._seen.items())

    def raw(self):
        """Every value in use, for the calibration page."""
        return {
            "rpm": self.rpm(), "trim_pct": self.trim_pct(), "oil_pressure_psi": self.oil_pressure_psi(),
            "engine_temp_f": self.engine_temp_f(), "alternator_v": self.alternator_v(),
            "fuel_gph": self.fuel_gph(), "fuel_level_pct": self.fuel_level_pct(),
        }

    def status(self):
        """Everything heard recently, including devices whose instance numbers don't match the settings."""
        now = self._clock()
        with self._lock:
            engines, flow = [], []
            for key, fields in self._engine.items():
                if now - self._seen[key] > self.REPORT_S:
                    continue
                got = {name: v for name, (t, v, pgn) in fields.items() if now - t <= self.REPORT_S}
                source, instance = key
                engines.append({
                    "source": source, "instance": instance, "age_s": round(now - self._seen[key], 1),
                    "rpm": got.get("rpm"), "trim_pct": got.get("trim_pct"),
                    "oil_pressure_psi": None if "oil_pressure_pa" not in got else round(got["oil_pressure_pa"] / PA_PER_PSI, 1),
                    "engine_temp_f": c_to_f(got.get("engine_temp_c")), "oil_temp_f": c_to_f(got.get("oil_temp_c")),
                    "alternator_v": got.get("alternator_v"),
                })
                if "fuel_gph" in got:
                    flow.append({"source": source, "instance": instance, "gph": got["fuel_gph"], "age_s": round(now - fields["fuel_gph"][0], 1),
                                 "flow_sensor": key + ("fuel_gph",) in self._proven})
            levels = [{"source": s, "instance": i, "type": TANK_TYPES.get(ty, str(ty)), "level_pct": lv, "capacity_gal": cap, "age_s": round(now - t, 1)}
                      for (s, i, ty), (t, lv, cap) in self._levels.items() if now - t <= self.REPORT_S]
        return {"engines": engines, "fuel_flow": flow, "tank_levels": levels,
                "engine_instance": self.engine_instance, "tank_instance": self.tank_instance}


N2kFuelData = N2kEngineData  # the original name, from when this only handled fuel
