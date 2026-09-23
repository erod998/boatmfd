"""Saved waypoints: a named list of marked positions, separate from the single "active"
navigation target in main.py's `waypoint` global (Go To one point right now). This is the
database you pick a destination from -- Garmin's own "Waypoints" list (mark, save, browse,
rename, edit position, delete) -- while `waypoint`/`/api/waypoint` is just "where am I headed
right now," which may or may not be one of these.
"""
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from .storage import read_records, write_json


@dataclass
class Waypoint:
    id: str
    name: str
    lat: float
    lon: float
    created_at: float


class SavedWaypoints:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._points: list = self._load()

    def _load(self):
        return read_records(self.storage_path, Waypoint)

    def _save(self):
        write_json(self.storage_path, [asdict(w) for w in self._points], indent=2)

    def create(self, lat, lon, name=None):
        wp = Waypoint(
            id=uuid.uuid4().hex[:8],
            name=(name or "").strip() or f"WPT {len(self._points) + 1}",
            lat=lat, lon=lon,
            created_at=time.time(),
        )
        self._points.append(wp)
        self._save()
        return wp

    def list(self):
        return [asdict(w) for w in self._points]

    def get(self, wp_id):
        return next((w for w in self._points if w.id == wp_id), None)

    def update(self, wp_id, name=None, lat=None, lon=None):
        wp = self.get(wp_id)
        if wp is None:
            return None
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("name can't be blank")
            wp.name = name
        if lat is not None:
            wp.lat = lat
        if lon is not None:
            wp.lon = lon
        self._save()
        return wp

    def delete(self, wp_id):
        before = len(self._points)
        self._points = [w for w in self._points if w.id != wp_id]
        if len(self._points) != before:
            self._save()
            return True
        return False

    def delete_all(self):
        count = len(self._points)
        self._points = []
        self._save()
        return count
