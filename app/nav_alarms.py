"""Navigation alarms: Arrival, Off Course, Anchor Drag and GPS Accuracy. Distinct from the engine/
boat threshold alarms in alarms.py because they depend on navigation state (how close the active
waypoint is, how far off the direct line to it, a dropped-anchor reference point, the GPS fix's
own reported accuracy) rather than a single steady instrument reading.
"""
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .nav import haversine_distance_nm
from .storage import read_dict, write_json

NM_TO_FT = 6076.12
# Arriving is an event, not a condition that lasts: on its own it showed for one 1 Hz frame -- a
# second of banner and beep. Held this long instead, as a boundary crossing is (main.py).
ARRIVAL_HOLD_S = 8.0


@dataclass
class NavAlarmSettings:
    arrival_enabled: bool = True
    arrival_radius_nm: float = 0.1
    off_course_enabled: bool = False
    off_course_xte_nm: float = 0.25
    anchor_radius_ft: float = 100.0
    gps_accuracy_enabled: bool = False
    gps_accuracy_hdop_max: float = 4.0


class NavAlarmManager:
    def __init__(self, storage_path: Optional[Path] = None, clock=time.monotonic):
        self.storage_path = Path(storage_path) if storage_path else None
        self._clock = clock
        self.settings = NavAlarmSettings()
        self._arrived = False       # edge-detect "just arrived," so it fires once, not every tick
        self._arrival = None        # (alert, shown until): the arrival being held on screen
        self._off_course = False
        # {"lat", "lon"} or None: where the anchor was dropped. Saved with the settings, so a Pi
        # that restarts at anchor overnight (a power blip) keeps watching rather than silently not.
        self._anchor_point = None
        self._anchor_dragging = False
        self._load()

    # ---------------- settings ----------------
    def _load(self):
        """Settings from disk, each one only if it is the same kind of value as its default.

        This used to setattr whatever the file held. A readable file with the wrong type in it --
        a hand edit, or a value from an older version -- then made every arrival or off-course
        comparison raise inside the telemetry loop, once a second, so no frame ever reached the
        screens again. A bad value now just leaves that one setting at its default."""
        if not self.storage_path:
            return
        saved = read_dict(self.storage_path)
        for key, default in asdict(self.settings).items():
            value = saved.get(key)
            if isinstance(default, bool):
                ok = isinstance(value, bool)
            elif isinstance(default, (int, float)):
                ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
                      and math.isfinite(value) and value > 0)
            else:
                ok = value is None or isinstance(value, type(default))
            if key in saved and ok:
                setattr(self.settings, key, value)
        anchor = saved.get("anchor")
        if (isinstance(anchor, dict)
                and all(isinstance(anchor.get(k), (int, float)) and not isinstance(anchor.get(k), bool)
                        and math.isfinite(anchor[k]) for k in ("lat", "lon"))
                and -90 <= anchor["lat"] <= 90 and -180 <= anchor["lon"] <= 180):
            self._anchor_point = {"lat": anchor["lat"], "lon": anchor["lon"]}

    def _save(self):
        if self.storage_path:
            write_json(self.storage_path, {**asdict(self.settings), "anchor": self._anchor_point})

    def update_settings(self, **kwargs):
        for key, value in kwargs.items():
            if value is None:
                continue
            if key not in asdict(self.settings):
                raise ValueError("unknown setting '%s'" % key)
            setattr(self.settings, key, value)
        self._save()
        return asdict(self.settings)

    # ---------------- anchor watch ----------------
    @property
    def anchor_dropped(self):
        return self._anchor_point is not None

    def drop_anchor(self, lat, lon):
        self._anchor_point = {"lat": lat, "lon": lon}
        self._anchor_dragging = False
        self._save()
        return dict(self._anchor_point)

    def raise_anchor(self):
        was_dropped = self._anchor_point is not None
        self._anchor_point = None
        self._anchor_dragging = False
        if was_dropped:
            self._save()
        return was_dropped

    # ---------------- evaluation ----------------
    def evaluate(self, fix, nav):
        """fix: the GPS Fix-like object (lat, lon, hdop, has_fix). nav: the dashboard's current
        waypoint_nav() dict (bearing/distance/xte to the active waypoint, or the active route's
        leg), with an optional "target_name", or None if nothing is being followed. Returns the
        list of alerts that should show right now."""
        alerts = []
        s = self.settings
        now = self._clock()

        if not fix.has_fix:
            self._arrived = self._off_course = self._anchor_dragging = False
            return alerts

        if nav is not None:
            if s.arrival_enabled:
                arrived_now = nav["distance_nm"] <= s.arrival_radius_nm
                if arrived_now and not self._arrived:
                    name = nav.get("target_name")
                    alert = {"id": "arrival", "severity": "alarm",
                             "message": "Arriving at %s" % name if name else "Arriving at destination"}
                    self._arrival = (alert, now + ARRIVAL_HOLD_S)
                self._arrived = arrived_now
            if s.off_course_enabled and nav.get("xte_nm") is not None:
                off_course_now = abs(nav["xte_nm"]) >= s.off_course_xte_nm
                if off_course_now:
                    alerts.append({"id": "off_course", "severity": "alarm",
                                    "message": "Off course: %.2f nm from the direct line" % abs(nav["xte_nm"])})
                self._off_course = off_course_now
        else:
            self._arrived = self._off_course = False
        # Shown for ARRIVAL_HOLD_S whatever follows: a route's last leg stops the route at once.
        if self._arrival and now < self._arrival[1]:
            alerts.append(dict(self._arrival[0]))
        elif self._arrival:
            self._arrival = None

        if self._anchor_point is not None:
            drift_ft = haversine_distance_nm(fix.lat, fix.lon, self._anchor_point["lat"], self._anchor_point["lon"]) * NM_TO_FT
            dragging_now = drift_ft > s.anchor_radius_ft
            if dragging_now:
                alerts.append({"id": "anchor_drag", "severity": "alarm",
                                "message": "Anchor drag: %d ft from where it was dropped" % round(drift_ft)})
            self._anchor_dragging = dragging_now

        if s.gps_accuracy_enabled and fix.hdop is not None and fix.hdop > s.gps_accuracy_hdop_max:
            alerts.append({"id": "gps_accuracy", "severity": "warning",
                            "message": "GPS accuracy is poor (HDOP %.1f)" % fix.hdop})

        return alerts
