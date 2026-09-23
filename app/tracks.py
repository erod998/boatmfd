"""Saved chart tracks: the always-on breadcrumb trail (the plain `track` list in main.py) can be
saved as a named, permanent record before it's trimmed away, the way a Garmin chartplotter lets
you save the active track. Separate from TripTracker (trips.py), which is a start/stop-logged trip
with distance/fuel/average speed; a saved track is just the recorded path itself.
"""
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .nav import haversine_distance_nm
from .storage import read_records, write_json


@dataclass
class SavedTrack:
    id: str
    name: str
    created_at: float
    points: list = field(default_factory=list)  # [{"lat":, "lon":, "t":}, ...], oldest first


def _distance_nm(points):
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += haversine_distance_nm(a["lat"], a["lon"], b["lat"], b["lon"])
    return total


class SavedTracks:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._tracks: list = self._load()

    def _load(self):
        return read_records(self.storage_path, SavedTrack)

    def _save(self):
        write_json(self.storage_path, [asdict(t) for t in self._tracks], indent=2)

    def save(self, points, name=None):
        if not points:
            raise ValueError("nothing to save: the track is empty")
        track = SavedTrack(
            id=uuid.uuid4().hex[:8],
            name=(name or "").strip() or time.strftime("%b %d, %Y %I:%M %p", time.localtime()),
            created_at=time.time(),
            points=list(points),
        )
        self._tracks.insert(0, track)
        self._save()
        return track

    def list(self):
        """Summaries only (no points, which can be long): what the Map Settings track list shows."""
        return [
            {"id": t.id, "name": t.name, "created_at": t.created_at,
             "points": len(t.points), "distance_nm": round(_distance_nm(t.points), 2)}
            for t in self._tracks
        ]

    def get(self, track_id):
        return next((t for t in self._tracks if t.id == track_id), None)

    def rename(self, track_id, name):
        name = (name or "").strip()
        if not name:
            raise ValueError("name can't be blank")
        track = self.get(track_id)
        if track is None:
            return None
        track.name = name
        self._save()
        return track

    def delete(self, track_id):
        before = len(self._tracks)
        self._tracks = [t for t in self._tracks if t.id != track_id]
        if len(self._tracks) != before:
            self._save()
            return True
        return False

    def delete_all(self):
        count = len(self._tracks)
        self._tracks = []
        self._save()
        return count
