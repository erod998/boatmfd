"""Garmin Quickdraw Contours, simplified: record (position, depth) samples as the boat moves so
the chart can show a personal depth map of water that was never professionally surveyed. Real
Quickdraw draws smoothed contour *lines*; this instead keeps colored sample points (shallow to
deep), which is a much simpler thing to both compute and draw and still answers the question a
personal depth map is actually for ("how deep is it around here, roughly"). Unlike most of this
app's other simulators, the input here is real (or as real as the depth sensor in use is) -- this
module is just recording and thinning it, not inventing it.

Two limits keep it fit to run for a whole season:

* **One sample per ~60 ft cell.** Spacing used to be checked only against the previous sample,
  so every pass over the same water added another full set, and the file grew with hours on the
  water rather than with water covered. A new sample in an already-visited cell now replaces the
  old one -- the newest depth is the one to trust on a lake whose level moves.
* **Saved every SAVE_EVERY_S, not every sample.** Each save rewrote the whole file from the
  telemetry thread, every second or two at speed. Now it is at most twice a minute, plus on
  stopping and on shutdown; a power cut costs at most the last half-minute of samples.
"""
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .nav import haversine_distance_nm
from .storage import read_records, write_json

MIN_SPACING_NM = 0.01  # about 60 ft: don't keep a sample closer than this to the last one
CELL_DEG = MIN_SPACING_NM / 60.0  # the same distance as a grid cell, in degrees of latitude
SAVE_EVERY_S = 30.0


@dataclass
class DepthSample:
    lat: float
    lon: float
    depth_ft: float
    t: float


def cell_of(lat, lon):
    """The ~60 ft grid cell a position falls in. Longitude cells are widened by 1/cos(latitude) of
    the cell's row so cells stay roughly square on the water, not just in degrees."""
    row = math.floor(lat / CELL_DEG)
    scale = math.cos(math.radians((row + 0.5) * CELL_DEG))
    return row, math.floor(lon * scale / CELL_DEG)


class QuickdrawRecorder:
    def __init__(self, storage_path: Path):
        self.storage_path = Path(storage_path)
        self.enabled = False
        self._samples: list = []
        self._cells = {}          # cell -> index into _samples
        self._last = None         # the most recent sample, for along-track spacing
        self._dirty = False
        self._last_save = 0.0
        for s in sorted(read_records(self.storage_path, DepthSample), key=lambda s: s.t):
            self._put(s)          # also compacts a file written before the grid existed

    def _put(self, sample):
        cell = cell_of(sample.lat, sample.lon)
        at = self._cells.get(cell)
        if at is None:
            self._cells[cell] = len(self._samples)
            self._samples.append(sample)
        else:
            self._samples[at] = sample
        self._last = sample

    def _save(self):
        return write_json(self.storage_path, [asdict(s) for s in self._samples])

    def flush(self):
        """Write out anything recorded since the last save."""
        if self._dirty and self._save():
            self._dirty = False
        self._last_save = time.time()

    def set_enabled(self, on):
        self.enabled = bool(on)
        if not self.enabled:
            self.flush()

    def record(self, lat, lon, depth_ft):
        """Call once a fix arrives with a valid depth; no-ops when disabled, no fix, or too close
        to the last recorded point to be worth another sample."""
        if not self.enabled or depth_ft is None:
            return False
        if self._last is not None and haversine_distance_nm(lat, lon, self._last.lat, self._last.lon) < MIN_SPACING_NM:
            return False
        self._put(DepthSample(lat=lat, lon=lon, depth_ft=round(depth_ft, 1), t=time.time()))
        self._dirty = True
        if time.time() - self._last_save >= SAVE_EVERY_S:
            self.flush()
        return True

    def points(self):
        return [asdict(s) for s in self._samples]

    def clear(self):
        count = len(self._samples)
        self._samples, self._cells, self._last = [], {}, None
        self._dirty = False
        self._save()
        return count
