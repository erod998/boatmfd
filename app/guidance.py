"""Following a Go To's planned path: Garmin's Auto Guidance, the steering half.

The browser plans the path, because the chart is there (static/js/guidance.js): over the water,
preferring the Corps' recommended track down the channel. Here the path is followed one leg at a
time, like a route with no names on its turns:

  * the nav fields steer to the next turn -- bearing, course and cross-track error are the
    current leg's, so Course Up turns with the channel and the off-course alarm measures from it;
  * distance and time to go are along the rest of the path, not as the crow flies;
  * a leg is done when the boat reaches its end, or passes abeam of it: cutting a corner inside
    the arrival circle must not leave the guidance steering back to a turn already behind it.

A plain straight-line Go To is the same thing with a two-point path.
"""
import math

from .nav import MIN_SOG_KN, haversine_distance_nm, waypoint_nav

# Close enough to a turn to take the next leg: about 150 ft. Tighter than a route's, because
# these turns are a channel's bends, not destinations.
TURN_RADIUS_NM = 0.025


def along_fraction(lat, lon, a, b):
    """How far along the leg a->b the boat is abeam of: 0 at a, 1 at b (either may be exceeded).
    Flat-earth, which over one leg of a lake route is exact enough."""
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    bx, by = (b[1] - a[1]) * k, b[0] - a[0]
    px, py = (lon - a[1]) * k, lat - a[0]
    length2 = bx * bx + by * by
    return 1.0 if length2 == 0 else (px * bx + py * by) / length2


class GuidedPath:
    """A Go To's path, [(lat, lon), ...] from where it was planned to the destination."""

    def __init__(self, points):
        if len(points) < 2:
            raise ValueError("a path needs a start and a destination")
        self.points = [(float(p[0]), float(p[1])) for p in points]
        self.leg = 1   # steering to points[leg], from points[leg - 1]
        self._legs_nm = [haversine_distance_nm(*a, *b) for a, b in zip(self.points, self.points[1:])]

    @property
    def destination(self):
        return self.points[-1]

    def tick(self, lat, lon, sog_kn, cog_deg=None):
        """The nav fields for the boat here, advancing past any turn it has reached or passed."""
        while self.leg < len(self.points) - 1:
            a, b = self.points[self.leg - 1], self.points[self.leg]
            if haversine_distance_nm(lat, lon, *b) <= TURN_RADIUS_NM or along_fraction(lat, lon, a, b) >= 1.0:
                self.leg += 1
            else:
                break
        a, b = self.points[self.leg - 1], self.points[self.leg]
        nav = waypoint_nav(lat, lon, sog_kn, b[0], b[1], a[0], a[1], cog_deg)
        to_turn = nav["distance_nm"]
        remaining = to_turn + sum(self._legs_nm[self.leg:])
        nav["distance_nm"] = round(remaining, 3)
        nav["turn_distance_nm"] = round(to_turn, 3)
        # Time to go along the path at the speed made good over the ground; VMG towards the next
        # bend says little about a destination three bends further on.
        nav["ete_s"] = round(remaining / sog_kn * 3600.0, 1) if sog_kn and sog_kn > MIN_SOG_KN else None
        nav["leg"] = self.leg
        nav["legs"] = len(self.points) - 1
        return nav
