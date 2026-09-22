"""Sends NMEA-sourced alarms to the stereo's mute, the same idea as a phone or VHF wired into a
Fusion head unit's TelMute input: when something needs attention, duck the music so the alarm is
actually heard, then let go on its own a few seconds later. Fusion has no TelMute command over
NMEA 2000 itself, but its ordinary mute message (fusion.set_mute, already used for the dashboard's
own Mute button) has the same audible effect without extra wiring, so that's what this sends.
"""
import json
import os
import tempfile
import time
from pathlib import Path

MUTE_SECONDS = 8.0  # how long a fresh alarm keeps the stereo muted; a new alarm during that window extends it


class AlarmMute:
    def __init__(self, path, media, clock=time.monotonic):
        self._path = Path(path) if path else None
        self._media = media
        self._clock = clock
        self.enabled = True
        self._muted_until = 0.0
        self._we_muted = False
        self._seen_alarm_ids = set()
        self._load()

    def _load(self):
        if not self._path or not self._path.exists():
            return
        try:
            saved = json.loads(self._path.read_text())
            if isinstance(saved.get("enabled"), bool):
                self.enabled = saved["enabled"]
        except (OSError, ValueError):
            pass

    def _save(self):
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".telemute-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(json.dumps({"enabled": self.enabled}))
            os.replace(tmp, self._path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def set_enabled(self, on):
        on = bool(on)
        if not on and self._we_muted:
            self._unmute()
        self.enabled = on
        self._save()

    def note_external_mute(self, on):
        """Call this when the mute state changes for a reason other than this class (the dashboard's
        own Mute button, or someone at the stereo itself), so a manual unmute isn't fought."""
        if not on:
            self._we_muted = False

    def _unmute(self):
        try:
            self._media.handle("mute", False)
        except (ValueError, RuntimeError, OSError):
            pass
        self._we_muted = False

    def tick(self, active_alarms, now=None):
        """Call often (the dashboard evaluates alarms 5 times a second) with alarms.active()."""
        now = self._clock() if now is None else now
        ids = {a["id"] for a in active_alarms if a["severity"] == "alarm"}
        new_ids = ids - self._seen_alarm_ids
        self._seen_alarm_ids = ids
        if not self.enabled:
            return
        if new_ids:
            if not self._we_muted:
                try:
                    self._media.handle("mute", True)
                except (ValueError, RuntimeError, OSError):
                    return
                self._we_muted = True
            self._muted_until = now + MUTE_SECONDS
        elif self._we_muted and now >= self._muted_until:
            self._unmute()
