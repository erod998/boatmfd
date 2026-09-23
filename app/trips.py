"""Discrete trip logging: start/stop a trip, track distance/speed while
underway, and persist finished trips to a JSON file so trip history
survives a restart.

This is separate from BoatInfo's running trip_nm (a simple always-on trip
meter) — TripTracker models Garmin-style logged trips you explicitly
start and stop, each saved as its own record.

A trip in progress is checkpointed to its own file every CHECKPOINT_S. It used to live only in
memory until Stop was pressed -- but a boat's day ends with the battery switch, not the Stop
button, and cranking the engine can brown out a Pi mid-trip, so in practice most trips would
simply have vanished. On the next start an interrupted trip is resumed if it is recent (the
crank, a lunch stop with the switch off), or closed where it was last known if it is not.
"""
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .nav import haversine_distance_nm
from .storage import quarantine, read_json, read_records, write_json

CHECKPOINT_S = 30.0            # how often a trip in progress is written to disk
RESUME_WINDOW_S = 6 * 3600.0   # interrupted less than this ago: carry on; longer: close it out
# Below this the boat is not going anywhere and a moving position is only the GPS wandering --
# a metre or three between fixes even when perfectly still. Summed once a second, that used to add
# miles to a trip over an hour at anchor, and inflate its average speed with them.
MIN_MOVING_KN = 0.5


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
        self.storage_path = Path(storage_path)
        self.active_path = self.storage_path.with_name(self.storage_path.stem + "_active.json")
        self._trips: list[Trip] = read_records(self.storage_path, Trip)
        self._active: Optional[Trip] = None
        self._last_point = None  # (lat, lon)
        self._last_tick = None   # wall-clock time of the previous tick, for integrating fuel burn
        self._last_checkpoint = 0.0
        self._prev_sog = 0.0
        self._recover()

    def _save(self):
        write_json(self.storage_path, [asdict(t) for t in self._trips], indent=2)

    # ---------------- the trip in progress, on disk ----------------
    def _checkpoint(self, now):
        data = asdict(self._active)
        data["checkpoint_time"] = now
        write_json(self.active_path, data)
        self._last_checkpoint = now

    def _clear_checkpoint(self):
        try:
            self.active_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _recover(self):
        data = read_json(self.active_path, None)
        if data is None:
            return
        trip, saved_at = None, None
        if isinstance(data, dict):
            saved_at = data.pop("checkpoint_time", None)
            try:
                trip = Trip(**data)
            except (TypeError, ValueError):
                trip = None
        if trip is None:
            quarantine(self.active_path, "an interrupted trip could not be read")
            return
        if any(t.id == trip.id for t in self._trips):
            # Stop saved it, and the power went before the checkpoint was cleared.
            self._clear_checkpoint()
            return
        now = time.time()
        if isinstance(saved_at, (int, float)) and 0 <= now - saved_at <= RESUME_WINDOW_S:
            self._active = trip
            self._last_checkpoint = saved_at
            print(f"[trips] resumed trip {trip.id}, interrupted {int(now - saved_at)} s ago")
            return
        end = saved_at if isinstance(saved_at, (int, float)) else trip.start_time
        trip.end_time = end
        trip.duration_s = round(end - trip.start_time, 1)
        self._trips.insert(0, trip)
        self._save()
        self._clear_checkpoint()
        print(f"[trips] closed trip {trip.id} where it was last saved; it was never stopped")

    # ---------------- trips ----------------
    @property
    def is_active(self):
        return self._active is not None

    def start(self, lat, lon):
        now = time.time()
        self._active = Trip(id=uuid.uuid4().hex[:8], start_time=now, start_lat=lat, start_lon=lon,
                            end_lat=lat, end_lon=lon)
        self._last_point = (lat, lon)
        self._last_tick = now
        self._prev_sog = 0.0
        self._checkpoint(now)
        return self._active

    def stop(self):
        if not self._active:
            return None
        self._active.end_time = time.time()
        self._active.duration_s = round(self._active.end_time - self._active.start_time, 1)
        finished = self._active
        self._trips.insert(0, finished)
        self._save()               # history first, then the checkpoint: see _recover
        self._clear_checkpoint()
        self._active = None
        self._last_point = None
        self._last_tick = None
        return finished

    def tick(self, lat, lon, sog_kn, gph=None):
        if not self._active:
            return
        now = time.time()
        if self._last_point is not None and sog_kn is not None and sog_kn >= MIN_MOVING_KN:
            self._active.distance_nm += haversine_distance_nm(*self._last_point, lat, lon)
        if gph and self._last_tick is not None:
            self._active.fuel_gal += gph * (now - self._last_tick) / 3600.0
        self._last_tick = now
        self._last_point = (lat, lon)   # always, so motion resumes from here and not from the drift
        self._active.end_lat, self._active.end_lon = lat, lon
        if sog_kn is not None:
            # A speed has to hold for two consecutive fixes to count, so one glitched fix cannot
            # set the trip's max speed forever.
            self._active.max_speed_kn = max(self._active.max_speed_kn, min(sog_kn, self._prev_sog))
            self._prev_sog = sog_kn
        if now - self._last_checkpoint >= CHECKPOINT_S:
            self._checkpoint(now)

    def flush(self):
        """Checkpoint the trip in progress now. Called on shutdown, so a clean restart loses nothing."""
        if self._active:
            self._checkpoint(time.time())

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
