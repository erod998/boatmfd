"""Garmin Quickdraw Contours, simplified: record (position, depth) samples as the boat moves so
the chart can show a personal depth map of water that was never professionally surveyed. Real
Quickdraw draws smoothed contour *lines*; this instead keeps colored sample points (shallow to
deep), which is a much simpler thing to both compute and draw and still answers the question a
personal depth map is actually for ("how deep is it around here, roughly"). Unlike most of this
app's other simulators, the input here is real (or as real as the depth sensor in use is) -- this
module is just recording and thinning it, not inventing it.
"""
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .nav import haversine_distance_nm

MIN_SPACING_NM = 0.01  # about 60 ft: don't keep a sample closer than this to the last one


@dataclass
class DepthSample:
    lat: float
    lon: float
    depth_ft: float
    t: float


class QuickdrawRecorder:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._samples: list = self._load()
        self.enabled = False

    def _load(self):
        if not self.storage_path.exists():
            return []
        try:
            raw = json.loads(self.storage_path.read_text())
            return [DepthSample(**s) for s in raw]
        except (json.JSONDecodeError, TypeError):
            return []

    def _save(self):
        self.storage_path.write_text(json.dumps([asdict(s) for s in self._samples]))

    def set_enabled(self, on):
        self.enabled = bool(on)

    def record(self, lat, lon, depth_ft):
        """Call once a fix arrives with a valid depth; no-ops when disabled, no fix, or too close
        to the last recorded point to be worth another sample."""
        if not self.enabled or depth_ft is None:
            return False
        if self._samples:
            last = self._samples[-1]
            if haversine_distance_nm(lat, lon, last.lat, last.lon) < MIN_SPACING_NM:
                return False
        self._samples.append(DepthSample(lat=lat, lon=lon, depth_ft=round(depth_ft, 1), t=time.time()))
        self._save()
        return True

    def points(self):
        return [asdict(s) for s in self._samples]

    def clear(self):
        count = len(self._samples)
        self._samples = []
        self._save()
        return count
