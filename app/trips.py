"""Discrete trip logging: start/stop a trip, track distance/speed while
underway, and persist finished trips to a JSON file so trip history
survives a restart.

This is separate from BoatInfo's running trip_nm (a simple always-on trip
meter) — TripTracker models Garmin-style logged trips you explicitly
start and stop, each saved as its own record.
"""
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .nav import haversine_distance_nm


@dataclass
class Trip:
    id: str
    start_time: float
    end_time: Optional[float] = None
    start_lat: float = 0.0
    start_lon: float = 0.0
    end_lat: float = 0.0
    end_lon: float = 0.0
    distance_nm: float = 0.0
    max_speed_kn: float = 0.0
    duration_s: float = 0.0
    fuel_gal: float = 0.0  # fuel burned during the trip (older saved trips without it load as 0)


class TripTracker:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._trips: list[Trip] = self._load()
        self._active: Optional[Trip] = None
        self._last_point = None  # (lat, lon)
        self._last_tick = None   # wall-clock time of the previous tick, for integrating fuel burn

    def _load(self) -> list:
        if not self.storage_path.exists():
            return []
        try:
            raw = json.loads(self.storage_path.read_text())
            return [Trip(**t) for t in raw]
        except (json.JSONDecodeError, TypeError):
            return []

    def _save(self):
        self.storage_path.write_text(json.dumps([asdict(t) for t in self._trips], indent=2))

    @property
    def is_active(self):
        return self._active is not None

    def start(self, lat, lon):
        self._active = Trip(id=uuid.uuid4().hex[:8], start_time=time.time(), start_lat=lat, start_lon=lon)
        self._last_point = (lat, lon)
        self._last_tick = time.time()
        return self._active

    def stop(self):
        if not self._active:
            return None
        self._active.end_time = time.time()
        self._active.duration_s = round(self._active.end_time - self._active.start_time, 1)
        finished = self._active
        self._trips.insert(0, finished)
        self._save()
        self._active = None
        self._last_point = None
        self._last_tick = None
        return finished

    def tick(self, lat, lon, sog_kn, gph=None):
        if not self._active:
            return
        now = time.time()
        if self._last_point is not None:
            self._active.distance_nm += haversine_distance_nm(*self._last_point, lat, lon)
        if gph and self._last_tick is not None:
            self._active.fuel_gal += gph * (now - self._last_tick) / 3600.0
        self._last_tick = now
        self._last_point = (lat, lon)
        self._active.end_lat, self._active.end_lon = lat, lon
        self._active.max_speed_kn = max(self._active.max_speed_kn, sog_kn)

    def current(self):
        if not self._active:
            return None
        elapsed = time.time() - self._active.start_time
        avg = (self._active.distance_nm / (elapsed / 3600)) if elapsed > 5 else 0.0
        return {
            "id": self._active.id,
            "start_time": self._active.start_time,
            "distance_nm": round(self._active.distance_nm, 3),
            "duration_s": round(elapsed, 1),
            "max_speed_kn": round(self._active.max_speed_kn, 2),
            "avg_speed_kn": round(avg, 2),
            "fuel_gal": round(self._active.fuel_gal, 3),
        }

    def history(self):
        return [asdict(t) for t in self._trips]

    def delete(self, trip_id: str):
        before = len(self._trips)
        self._trips = [t for t in self._trips if t.id != trip_id]
        if len(self._trips) != before:
            self._save()
            return True
        return False
