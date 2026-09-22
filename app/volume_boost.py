"""RPM-linked stereo volume boost, the marine equivalent of a car stereo's speed-compensated
volume: as RPM climbs toward redline (more engine and wind noise), nudge every zone's volume up
from whatever the user set it to, easing back down as RPM falls. Two knobs, both set from the
dashboard: how much to add at redline, and how heavily to smooth the ramp so it doesn't chase
every small RPM flicker. Settings are saved to disk so they survive a restart.
"""
import json
import math
import os
import tempfile
import time
from pathlib import Path

DEFAULTS = {"enabled": False, "boost_pct": 25.0, "smoothing_pct": 50.0}
MAX_SMOOTHING_TAU_S = 4.0  # smoothing_pct 100 -> about a 4s time constant; 0 -> essentially instant


class VolumeBoost:
    def __init__(self, path, media, redline_rpm, clock=time.monotonic):
        self._path = Path(path) if path else None
        self._media = media
        self._redline_rpm = max(1, redline_rpm)
        self._clock = clock
        self._cfg = dict(DEFAULTS)
        self._load()
        self._manual = {}   # zone -> volume the user actually asked for (the un-boosted baseline)
        self._applied = {}  # zone -> the last eased multiplier, so a config/RPM change ramps instead of jumping
        self._last_t = clock()

    def _load(self):
        if not self._path or not self._path.exists():
            return
        try:
            saved = json.loads(self._path.read_text())
            if isinstance(saved.get("enabled"), bool):
                self._cfg["enabled"] = saved["enabled"]
            for key in ("boost_pct", "smoothing_pct"):
                value = saved.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self._cfg[key] = float(value)
        except (OSError, ValueError):
            pass  # a damaged file means defaults, never a crash at startup

    def _save(self):
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".volboost-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(json.dumps(self._cfg))
            os.replace(tmp, self._path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def config(self):
        return dict(self._cfg)

    def set_config(self, enabled=None, boost_pct=None, smoothing_pct=None):
        if boost_pct is not None and not 0 <= boost_pct <= 200:
            raise ValueError("boost_pct must be 0-200")
        if smoothing_pct is not None and not 0 <= smoothing_pct <= 100:
            raise ValueError("smoothing_pct must be 0-100")
        if enabled is not None:
            self._cfg["enabled"] = bool(enabled)
        if boost_pct is not None:
            self._cfg["boost_pct"] = float(boost_pct)
        if smoothing_pct is not None:
            self._cfg["smoothing_pct"] = float(smoothing_pct)
        self._save()
        return self.config()

    def note_manual_volume(self, zone, volume):
        """Call this whenever a user action (not this feature) sets a zone's volume, so there's an
        un-boosted baseline to boost from instead of the boost ratcheting up its own last output."""
        self._manual[zone] = volume

    def _ease(self, current, target, dt):
        tau = (self._cfg["smoothing_pct"] / 100.0) * MAX_SMOOTHING_TAU_S
        if tau <= 0:
            return target
        alpha = 1 - math.exp(-dt / tau)
        return current + (target - current) * alpha

    def tick(self, rpm, zones_state):
        """Called once a second with the current engine RPM and the media source's zone rows."""
        now = self._clock()
        dt, self._last_t = max(0.0, now - self._last_t), now
        frac = max(0.0, min(1.0, (rpm or 0) / self._redline_rpm)) if self._cfg["enabled"] else 0.0
        target_mult = 1.0 + frac * (self._cfg["boost_pct"] / 100.0)
        for row in zones_state:
            zone = row["id"]
            if zone not in self._manual:
                self._manual[zone] = row["volume"]
            self._applied[zone] = self._ease(self._applied.get(zone, 1.0), target_mult, dt)
            if row["muted"]:
                continue
            base = self._manual[zone]
            target_volume = round(min(row["limit"], base * self._applied[zone]))
            if target_volume != row["volume"]:
                try:
                    self._media.handle("volume", target_volume, zone=zone)
                except (ValueError, RuntimeError, OSError):
                    pass
