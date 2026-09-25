"""Sends NMEA-sourced alarms to the stereo's mute, the same idea as a phone or VHF wired into a
Fusion head unit's TelMute input: when something needs attention, duck the music so the alarm is
actually heard, then let go on its own a few seconds later. Fusion has no TelMute command over
NMEA 2000 itself, but its ordinary mute message (fusion.set_mute, already used for the dashboard's
own Mute button) has the same audible effect without extra wiring, so that's what this sends.
"""
import time
from pathlib import Path
from .storage import read_dict, write_json

MUTE_SECONDS = 8.0  # how long a fresh alarm keeps the stereo muted; a new alarm during that window extends it
RETRY_S = 5.0       # an unmute that didn't reach the stereo (the bus down) is tried again this often


class AlarmMute:
    def __init__(self, path, media, clock=time.monotonic):
        self._path = Path(path) if path else None
        self._media = media
        self._clock = clock
        self.enabled = True
        self._muted_until = 0.0
        self._retry_at = 0.0
        self._we_muted = False
        self._seen_alarm_ids = set()
        self._load()

    def _load(self):
        if not self._path:
            return
        saved = read_dict(self._path)
        if isinstance(saved.get("enabled"), bool):
            self.enabled = saved["enabled"]

    def _save(self):
        if self._path:
            write_json(self._path, {"enabled": self.enabled})

    def set_enabled(self, on):
        on = bool(on)
        if not on and self._we_muted and not self._unmute():
            self._we_muted = False   # switched off: the retry is up to whoever turned it off
        self.enabled = on
        self._save()

    def note_external_mute(self, on):
        """Call this when the mute state changes for a reason other than this class (the dashboard's
        own Mute button, or someone at the stereo itself), so a manual unmute isn't fought."""
        if not on:
            self._we_muted = False

    def _unmute(self, now=None):
        """Unmute, if the stereo can be reached. If not (the bus is down), the mute this class put
        on is kept on the books and tried again every RETRY_S -- forgetting it would leave the
        stereo muted for good -- and not every tick, since a send can wait for the bus."""
        try:
            self._media.handle("mute", False)
        except (ValueError, RuntimeError, OSError):
            self._retry_at = (self._clock() if now is None else now) + RETRY_S
            return False
        self._we_muted = False
        return True

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
        elif self._we_muted and now >= self._muted_until and now >= self._retry_at:
            self._unmute(now)
