"""Multi-leg routes: Garmin's "Route To" (add turns along the way) as distinct from "Go To" (a
straight line at a single waypoint, which is what main.py's `waypoint` global already does).
A route is a saved, ordered list of points; RouteTracker also tracks the one route currently
being navigated (if any) and which leg of it is active, auto-advancing to the next leg once the
boat arrives at the current one, the same "arrival" concept the Navigation Alarms use.
"""
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .nav import haversine_distance_nm, waypoint_nav

ARRIVAL_RADIUS_NM = 0.05  # about 300 ft: close enough to call a leg "arrived" and advance


@dataclass
class RoutePoint:
    lat: float
    lon: float
    name: str = "WPT"


@dataclass
class Route:
    id: str
    name: str
    created_at: float
    points: list = field(default_factory=list)  # list of dicts (lat, lon, name), at least 2


def _distance_nm(points):
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += haversine_distance_nm(a["lat"], a["lon"], b["lat"], b["lon"])
    return total


class RouteTracker:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._routes: list = self._load()
        self._active_route_id = None
        self._active_leg = 0  # index into the active route's points; navigating TO this point

    def _load(self):
        if not self.storage_path.exists():
            return []
        try:
            raw = json.loads(self.storage_path.read_text())
            return [Route(**r) for r in raw]
        except (json.JSONDecodeError, TypeError):
            return []

    def _save(self):
        self.storage_path.write_text(json.dumps([asdict(r) for r in self._routes], indent=2))

    # ---------------- saved routes: create/list/get/rename/delete ----------------
    def create(self, points, name=None):
        if not points or len(points) < 2:
            raise ValueError("a route needs at least two points")
        route = Route(
            id=uuid.uuid4().hex[:8],
            name=(name or "").strip() or time.strftime("Route %b %d, %Y %I:%M %p", time.localtime()),
            created_at=time.time(),
            points=[{"lat": p["lat"], "lon": p["lon"], "name": p.get("name", "WPT")} for p in points],
        )
        self._routes.append(route)
        self._save()
        return route

    def list(self):
        return [
            {"id": r.id, "name": r.name, "created_at": r.created_at, "legs": len(r.points) - 1,
             "distance_nm": round(_distance_nm(r.points), 2),
             "active": r.id == self._active_route_id}
            for r in self._routes
        ]

    def get(self, route_id):
        return next((r for r in self._routes if r.id == route_id), None)

    def rename(self, route_id, name):
        name = (name or "").strip()
        if not name:
            raise ValueError("name can't be blank")
        route = self.get(route_id)
        if route is None:
            return None
        route.name = name
        self._save()
        return route

    def delete(self, route_id):
        if route_id == self._active_route_id:
            self.stop()
        before = len(self._routes)
        self._routes = [r for r in self._routes if r.id != route_id]
        if len(self._routes) != before:
            self._save()
            return True
        return False

    # ---------------- following a route leg by leg ----------------
    def start(self, route_id):
        route = self.get(route_id)
        if route is None:
            raise ValueError("no route with that id")
        self._active_route_id = route_id
        self._active_leg = 1  # points[0] is the route's own start/origin, not a real target
        return route

    def stop(self):
        was_active = self._active_route_id is not None
        self._active_route_id = None
        self._active_leg = 1
        return was_active

    @property
    def is_active(self):
        return self._active_route_id is not None

    def tick(self, lat, lon, sog_kn):
        """Call once a fix arrives; advances to the next leg on arrival. Returns the current
        leg's nav info (like waypoint_nav) plus route context, or None if no route is active."""
        if self._active_route_id is None:
            return None
        route = self.get(self._active_route_id)
        if route is None:  # the active route was deleted out from under us
            self.stop()
            return None
        target = route.points[self._active_leg]
        origin = route.points[self._active_leg - 1]
        nav = waypoint_nav(lat, lon, sog_kn, target["lat"], target["lon"], origin["lat"], origin["lon"])
        advanced = False
        if nav["distance_nm"] <= ARRIVAL_RADIUS_NM and self._active_leg < len(route.points) - 1:
            self._active_leg += 1
            target = route.points[self._active_leg]
            origin = route.points[self._active_leg - 1]
            nav = waypoint_nav(lat, lon, sog_kn, target["lat"], target["lon"], origin["lat"], origin["lon"])
            advanced = True
        finished = nav["distance_nm"] <= ARRIVAL_RADIUS_NM and self._active_leg == len(route.points) - 1
        return {
            **nav,
            "route_id": route.id,
            "route_name": route.name,
            "leg": self._active_leg,
            "legs": len(route.points) - 1,
            "leg_name": target["name"],
            "leg_lat": target["lat"],
            "leg_lon": target["lon"],
            "advanced": advanced,
            "finished": finished,
        }
