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
    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = Path(storage_path) if storage_path else None
        self.settings = NavAlarmSettings()
        self._load()
        self._arrived = False       # edge-detect "just arrived," so it fires once, not every tick
        self._off_course = False
        self._anchor_point = None   # {"lat", "lon"} or None: where the anchor was dropped
        self._anchor_dragging = False

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

    def _save(self):
        if self.storage_path:
            write_json(self.storage_path, asdict(self.settings))

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
        return dict(self._anchor_point)

    def raise_anchor(self):
        was_dropped = self._anchor_point is not None
        self._anchor_point = None
        self._anchor_dragging = False
        return was_dropped

    # ---------------- evaluation ----------------
    def evaluate(self, fix, nav):
        """fix: the GPS Fix-like object (lat, lon, hdop, has_fix). nav: the dashboard's current
        waypoint_nav() dict (bearing/distance/xte to the active waypoint), or None if there's no
        active waypoint. Returns the list of alerts that should show right now."""
        alerts = []
        s = self.settings

        if not fix.has_fix:
            self._arrived = self._off_course = self._anchor_dragging = False
            return alerts

        if nav is not None:
            if s.arrival_enabled:
                arrived_now = nav["distance_nm"] <= s.arrival_radius_nm
                if arrived_now and not self._arrived:
                    alerts.append({"id": "arrival", "severity": "alarm", "message": "Arriving at destination"})
                self._arrived = arrived_now
            if s.off_course_enabled and nav.get("xte_nm") is not None:
                off_course_now = abs(nav["xte_nm"]) >= s.off_course_xte_nm
                if off_course_now:
                    alerts.append({"id": "off_course", "severity": "alarm",
                                    "message": "Off course: %.2f nm from the direct line" % abs(nav["xte_nm"])})
                self._off_course = off_course_now
        else:
            self._arrived = self._off_course = False

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
