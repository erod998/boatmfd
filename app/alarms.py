"""Alarms the user sets on engine and boat readings.

Each alarm watches one reading and fires when it crosses a level the user chose (hold a gauge
on the dashboard to open its alarm menu). Levels, on/off and the sound setting are saved in
data/alarms.json, so they survive a restart and every screen sees the same alarms.

How an alarm behaves:
- It must stay past its level for a few seconds before it fires, so a starter-motor voltage dip
  or a depth-sounder dropout doesn't set it off.
- An alarm also has an amber warning a set margin before its level.
- No data never triggers an alarm (a sensor that isn't there is not a reading) -- but it doesn't
  clear one either. An alarm already sounding stays up through a dropout, marked as having lost
  its sensor, until real data shows recovery. A depth sounder loses the bottom in very shallow
  water and prop wash, and an overheating engine can burn through its sender wire; those are
  exactly the moments the alarm must not quietly disappear. (It used to: missing data reset it.)
- Oil pressure is only watched once the engine has been running for a few seconds, because it
  takes a moment to build.
- Acknowledging silences the banner and sound; the alarm stays listed until the reading recovers
  (by a small hysteresis, so it doesn't flicker), and a new alarm needs its own acknowledgement.
"""
import threading
import time
from pathlib import Path
from .storage import read_dict, write_json

RUNNING_RPM = 500          # above this the engine counts as running
RUNNING_GRACE_S = 5.0      # ...for this long before oil pressure is trusted

# side: "high" alarms when the reading rises to the level, "low" when it falls to it.
# warn: how far before the level the amber warning starts. hyst: how far back the reading must recover to clear.
ALARM_DEFS = [
    {"id": "coolant", "group": "coolant", "label": "Coolant / engine temp", "what": "Coolant temperature", "unit": "°F",
     "side": "high", "default": 210, "min": 120, "max": 260, "step": 5, "decimals": 0, "warn": 15, "hyst": 3, "delay": 3.0,
     "source": ("engine", "coolant_f"), "needs_running": False, "default_enabled": True},
    {"id": "oil", "group": "oil", "label": "Oil pressure", "what": "Oil pressure", "unit": "psi",
     "side": "low", "default": 10, "min": 0, "max": 40, "step": 1, "decimals": 0, "warn": 10, "hyst": 2, "delay": 3.0,
     "source": ("engine", "oil_pressure_psi"), "needs_running": True, "default_enabled": True},
    {"id": "battery_low", "group": "battery", "label": "Battery voltage low", "what": "Battery voltage", "unit": "V",
     "side": "low", "default": 11.8, "min": 10.0, "max": 13.0, "step": 0.1, "decimals": 1, "warn": 0.3, "hyst": 0.2, "delay": 5.0,
     "source": ("boat_info", "battery_voltage"), "needs_running": False, "default_enabled": True},
    {"id": "battery_high", "group": "battery", "label": "Battery voltage high", "what": "Charging voltage", "unit": "V",
     "side": "high", "default": 14.8, "min": 13.5, "max": 16.0, "step": 0.1, "decimals": 1, "warn": 0.3, "hyst": 0.2, "delay": 5.0,
     "source": ("boat_info", "battery_voltage"), "needs_running": False, "default_enabled": True},
    {"id": "fuel", "group": "fuel", "label": "Fuel level", "what": "Fuel level", "unit": "%",
     "side": "low", "default": 10, "min": 0, "max": 50, "step": 5, "decimals": 0, "warn": 15, "hyst": 2, "delay": 5.0,
     "source": ("engine", "fuel_pct"), "needs_running": False, "default_enabled": True},
    {"id": "depth", "group": "depth", "label": "Depth", "what": "Water depth", "unit": "ft",
     "side": "low", "default": 5, "min": 2, "max": 100, "step": 1, "decimals": 1, "warn": 3, "hyst": 0.5, "delay": 2.0,
     "source": ("boat_info", "depth_ft"), "needs_running": False, "default_enabled": False},
]
DEFS = {d["id"]: d for d in ALARM_DEFS}


def _fmt(value, decimals, unit):
    text = f"{value:.{decimals}f}"
    return text + unit if unit in ("°F", "%") else f"{text} {unit}"


class _State:
    __slots__ = ("pending_since", "alarm", "warning", "acked", "value", "last", "lost")

    def __init__(self):
        self.value = None   # the latest reading, None when there is none
        self.last = None    # the latest reading that was not None, for the banner of a lost alarm
        self.reset()

    def reset(self):
        self.pending_since = None   # when the reading first crossed the level (for the delay)
        self.alarm = False
        self.warning = False
        self.acked = False
        self.lost = False           # alarming, and the sensor has since stopped reporting


class AlarmManager:
    def __init__(self, path=None, clock=time.monotonic):
        self.path = Path(path) if path else None
        self._clock = clock
        self._lock = threading.RLock()
        self._cfg = {d["id"]: {"enabled": d["default_enabled"], "level": d["default"]} for d in ALARM_DEFS}
        self._sound = True
        self._state = {d["id"]: _State() for d in ALARM_DEFS}
        self._running_since = None
        self.revision = 0   # bumped on every settings change so other screens know to reload them
        self._load()

    # ---------------- settings ----------------
    def _load(self):
        if not self.path:
            return
        saved = read_dict(self.path)   # a damaged file means defaults (and is kept), never a crash
        try:
            if isinstance(saved.get("sound"), bool):
                self._sound = saved["sound"]
            for alarm_id, values in (saved.get("alarms") or {}).items():
                d = DEFS.get(alarm_id)
                if d is None or not isinstance(values, dict):
                    continue
                if isinstance(values.get("enabled"), bool):
                    self._cfg[alarm_id]["enabled"] = values["enabled"]
                level = values.get("level")
                if isinstance(level, (int, float)) and not isinstance(level, bool) and d["min"] <= level <= d["max"]:
                    self._cfg[alarm_id]["level"] = level
        except (ValueError, AttributeError):
            pass  # an entry in the wrong shape keeps its default

    def _save(self):
        if self.path:
            write_json(self.path, {"sound": self._sound, "alarms": self._cfg}, indent=2)

    def update(self, alarm_id, enabled=None, level=None):
        d = DEFS.get(alarm_id)
        if d is None:
            raise ValueError("unknown alarm '%s'" % alarm_id)
        if level is not None and not d["min"] <= level <= d["max"]:
            raise ValueError("%s level must be %g to %g %s" % (d["label"], d["min"], d["max"], d["unit"]))
        with self._lock:
            cfg = self._cfg[alarm_id]
            if enabled is not None:
                cfg["enabled"] = bool(enabled)
            if level is not None:
                cfg["level"] = round(float(level), 2)
            self._state[alarm_id].reset()   # a changed setting starts the alarm from scratch: it re-arms against the new level
            self.revision += 1
            self._save()

    def set_sound(self, on):
        with self._lock:
            self._sound = bool(on)
            self.revision += 1
            self._save()

    @property
    def sound(self):
        return self._sound

    def config(self):
        """{id: {"enabled", "level"}}: the settings that are saved."""
        with self._lock:
            return {k: dict(v) for k, v in self._cfg.items()}

    def frame_config(self):
        """The settings plus what the dashboard needs to paint each gauge's red and amber bands and its bell icon."""
        with self._lock:
            return {k: {**v, "warn": DEFS[k]["warn"], "side": DEFS[k]["side"]} for k, v in self._cfg.items()}

    # ---------------- evaluation ----------------
    def evaluate(self, engine, boat):
        """Update every alarm from the latest readings. Call this often (the dashboard does, 5 times a second)."""
        now = self._clock()
        readings = {"engine": engine or {}, "boat_info": boat or {}}
        rpm = readings["engine"].get("rpm")
        with self._lock:
            if rpm is not None and rpm > RUNNING_RPM:
                if self._running_since is None:
                    self._running_since = now
            else:
                self._running_since = None
            running = self._running_since is not None and now - self._running_since >= RUNNING_GRACE_S

            for d in ALARM_DEFS:
                st = self._state[d["id"]]
                cfg = self._cfg[d["id"]]
                value = readings[d["source"][0]].get(d["source"][1])
                st.value = value
                if not cfg["enabled"] or (d["needs_running"] and not running):
                    st.reset()
                    continue
                if value is None:
                    # Never a reason to raise an alarm, and never evidence that one has recovered.
                    st.pending_since = None
                    st.warning = False
                    st.lost = st.alarm
                    continue
                st.last = value
                st.lost = False

                level = cfg["level"]
                high = d["side"] == "high"
                past = value >= level if high else value <= level
                recovered = value < level - d["hyst"] if high else value > level + d["hyst"]
                warn_level = level - d["warn"] if high else level + d["warn"]
                in_warn = value >= warn_level if high else value <= warn_level
                warn_clear = value < warn_level - d["hyst"] if high else value > warn_level + d["hyst"]

                if st.alarm:
                    if recovered:
                        st.alarm, st.acked, st.pending_since = False, False, None
                elif past:
                    if st.pending_since is None:
                        st.pending_since = now
                    if now - st.pending_since >= d["delay"]:
                        st.alarm, st.acked = True, False
                else:
                    st.pending_since = None

                if st.warning:
                    if warn_clear:
                        st.warning = False
                elif in_warn:
                    st.warning = True

    def acknowledge(self):
        """Silence everything that is alarming right now. Returns how many alarms that was."""
        with self._lock:
            count = 0
            for st in self._state.values():
                if st.alarm and not st.acked:
                    st.acked = True
                    count += 1
            return count

    # ---------------- what the dashboard sees ----------------
    def _entry(self, d, st, severity):
        level = self._cfg[d["id"]]["level"]
        value = st.value
        word = "high" if d["side"] == "high" else "low"
        if severity == "alarm" and st.lost:
            last = f" (last {_fmt(st.last, d['decimals'], d['unit'])})" if st.last is not None else ""
            message = f"{d['what']} {word}: no reading from the sensor{last}"
        elif severity == "alarm":
            message = f"{d['what']} {word}: {_fmt(value, d['decimals'], d['unit'])} (alarm at {_fmt(level, d['decimals'], d['unit'])})"
        else:
            message = f"{d['what']} nearing {word} alarm: {_fmt(value, d['decimals'], d['unit'])}"
        return {"id": d["id"], "group": d["group"], "severity": severity, "acked": st.acked if severity == "alarm" else False,
                "value": value, "level": level, "unit": d["unit"], "message": message, "sensor_lost": st.lost}

    def active(self):
        """Alarms first, then warnings: everything the dashboard should be showing right now."""
        with self._lock:
            alarms, warnings = [], []
            for d in ALARM_DEFS:
                st = self._state[d["id"]]
                if st.alarm:
                    alarms.append(self._entry(d, st, "alarm"))
                elif st.warning:
                    warnings.append(self._entry(d, st, "warning"))
            return alarms + warnings

    def snapshot(self):
        """Everything the alarm menu needs: each alarm's definition, setting and current state."""
        with self._lock:
            rows = []
            for d in ALARM_DEFS:
                st = self._state[d["id"]]
                cfg = self._cfg[d["id"]]
                rows.append({
                    "id": d["id"], "group": d["group"], "label": d["label"], "unit": d["unit"], "side": d["side"],
                    "min": d["min"], "max": d["max"], "step": d["step"], "decimals": d["decimals"], "warn": d["warn"],
                    "delay": d["delay"], "default": d["default"], "default_enabled": d["default_enabled"],
                    "enabled": cfg["enabled"], "level": cfg["level"], "value": st.value,
                    "state": "alarm" if st.alarm else "warning" if st.warning else "ok", "acked": st.acked,
                })
            return {"sound": self._sound, "revision": self.revision, "alarms": rows}
