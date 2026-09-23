"""Boundaries: a circular geofence around a point (a marina, a no-wake zone, a swimming area) that
alarms when the boat enters or exits it. Garmin's own Boundaries support polygons and lines too;
this starts with circles only, since drawing a circle on a touchscreen (tap a center, pick a
radius) is a lot simpler than collecting an ordered ring of polygon points, and a circle already
covers the common cases (stay within/away from an area) a personal boat is likely to actually use.
"""
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .nav import haversine_distance_nm
from .storage import read_records, write_json

NM_TO_FT = 6076.12


@dataclass
class Boundary:
    id: str
    name: str
    lat: float
    lon: float
    radius_ft: float
    alarm_on: str = "exit"  # "enter" | "exit" | "both"
    enabled: bool = True
    created_at: float = 0.0


class BoundaryManager:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._boundaries: list = self._load()
        self._inside = {}  # boundary id -> bool, last known state, to detect crossings

    def _load(self):
        return read_records(self.storage_path, Boundary)

    def _save(self):
        write_json(self.storage_path, [asdict(b) for b in self._boundaries], indent=2)

    def create(self, lat, lon, radius_ft, name=None, alarm_on="exit"):
        if alarm_on not in ("enter", "exit", "both"):
            raise ValueError("alarm_on must be 'enter', 'exit' or 'both'")
        if not radius_ft or radius_ft <= 0:
            raise ValueError("radius_ft must be positive")
        b = Boundary(
            id=uuid.uuid4().hex[:8],
            name=(name or "").strip() or f"Boundary {len(self._boundaries) + 1}",
            lat=lat, lon=lon, radius_ft=radius_ft, alarm_on=alarm_on,
            created_at=time.time(),
        )
        self._boundaries.append(b)
        self._save()
        return b

    def list(self):
        return [asdict(b) for b in self._boundaries]

    def get(self, boundary_id):
        return next((b for b in self._boundaries if b.id == boundary_id), None)

    def update(self, boundary_id, enabled=None, alarm_on=None, radius_ft=None, name=None):
        b = self.get(boundary_id)
        if b is None:
            return None
        if enabled is not None:
            b.enabled = bool(enabled)
        if alarm_on is not None:
            if alarm_on not in ("enter", "exit", "both"):
                raise ValueError("alarm_on must be 'enter', 'exit' or 'both'")
            b.alarm_on = alarm_on
        if radius_ft is not None:
            if radius_ft <= 0:
                raise ValueError("radius_ft must be positive")
            b.radius_ft = radius_ft
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("name can't be blank")
            b.name = name
        self._save()
        return b

    def delete(self, boundary_id):
        before = len(self._boundaries)
        self._boundaries = [b for b in self._boundaries if b.id != boundary_id]
        self._inside.pop(boundary_id, None)
        if len(self._boundaries) != before:
            self._save()
            return True
        return False

    def evaluate(self, lat, lon):
        """Call once a fix arrives. Returns the list of boundaries just crossed this tick, each
        as {"boundary", "event": "entered"|"exited"}. Also returns whether the boat is currently
        inside as part of each boundary's own dict, for `active()`-style display."""
        crossed = []
        for b in self._boundaries:
            if not b.enabled:
                continue
            distance_ft = haversine_distance_nm(lat, lon, b.lat, b.lon) * NM_TO_FT
            now_inside = distance_ft <= b.radius_ft
            was_inside = self._inside.get(b.id)
            if was_inside is not None and now_inside != was_inside:
                event = "entered" if now_inside else "exited"
                if b.alarm_on == "both" or (b.alarm_on == "enter" and event == "entered") or (b.alarm_on == "exit" and event == "exited"):
                    crossed.append({"id": b.id, "name": b.name, "event": event})
            self._inside[b.id] = now_inside
        return crossed
