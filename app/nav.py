"""Great-circle navigation math: bearing, course, distance, VMG, time enroute, cross-track error.

The vocabulary is a GPSMAP's, because the data fields on screen are:

  BRG     bearing -- the direction to the destination from where the boat is *now*
  CRS     course  -- the direction of the intended leg, origin to destination
  XTE     how far off that leg the boat has drifted, signed: + is right of track
  VMG     velocity made good -- how fast the distance to the destination is actually
          shrinking, which is SOG only when travelling straight at it
  ETE     time enroute, from VMG rather than SOG: sliding sideways past a waypoint at
          20 kn is not arriving at 20 kn, and a real chartplotter does not claim it is
  TURN    how far, and which way, to turn to point at the destination

Everything here is a pure function of its arguments -- no clock, no state. ETA is
deliberately not computed: it is only ETE added to the current time, and doing it on
the display keeps this testable and keeps one clock in charge of it.
"""
import math

EARTH_RADIUS_NM = 3440.065  # nautical miles

# Below this the heading of travel is noise -- a drifting boat's COG wanders freely -- so
# VMG-derived numbers stop meaning anything and are reported as unknown rather than wrong.
MIN_SOG_KN = 0.1


def haversine_distance_nm(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


def initial_bearing_deg(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    theta = math.atan2(x, y)
    return (math.degrees(theta) + 360) % 360


def cross_track_error_nm(lat, lon, start_lat, start_lon, end_lat, end_lon):
    """Perpendicular distance (nm) from the start->end great circle. Positive = right of track."""
    d13 = haversine_distance_nm(start_lat, start_lon, lat, lon) / EARTH_RADIUS_NM
    brng13 = math.radians(initial_bearing_deg(start_lat, start_lon, lat, lon))
    brng12 = math.radians(initial_bearing_deg(start_lat, start_lon, end_lat, end_lon))
    return math.asin(math.sin(d13) * math.sin(brng13 - brng12)) * EARTH_RADIUS_NM


def signed_angle_diff(from_deg, to_deg):
    """to - from, wrapped to (-180, 180]. Negative = left/port, positive = right/starboard.

    An exact reversal lands on +180 rather than -180: either way round is the same turn, and
    the Turn field reading "180 R" is less odd than "180 L".
    """
    return -((from_deg - to_deg + 180) % 360 - 180)


def velocity_made_good_kn(sog_kn, cog_deg, bearing_deg):
    """Speed at which the distance to the destination is closing.

    The component of the boat's velocity that points at the destination: SOG when steering
    straight at it, zero when crossing it at a right angle, negative when opening away.
    """
    if sog_kn is None or cog_deg is None or sog_kn < MIN_SOG_KN:
        return None
    return sog_kn * math.cos(math.radians(signed_angle_diff(cog_deg, bearing_deg)))


def waypoint_nav(lat, lon, sog_kn, wp_lat, wp_lon, origin_lat=None, origin_lon=None, cog_deg=None):
    """Everything the nav data fields need about the leg to one waypoint.

    `origin_*` is the start of the intended leg, which is what makes CRS and XTE meaningful;
    without it there is no track to be off of. `cog_deg` is the direction of travel over the
    ground, which is what makes VMG and TURN meaningful. Both are optional, and the fields
    that depend on them come back None rather than guessed.
    """
    distance_nm = haversine_distance_nm(lat, lon, wp_lat, wp_lon)
    bearing = initial_bearing_deg(lat, lon, wp_lat, wp_lon)

    vmg_kn = velocity_made_good_kn(sog_kn, cog_deg, bearing)
    # Closing slower than this and "time remaining" is hours of noise, so call it unknown --
    # which is also what happens when the boat is pointed away and VMG has gone negative.
    ete_s = (distance_nm / vmg_kn) * 3600.0 if vmg_kn and vmg_kn > MIN_SOG_KN else None

    course = xte_nm = None
    if origin_lat is not None and origin_lon is not None:
        course = initial_bearing_deg(origin_lat, origin_lon, wp_lat, wp_lon)
        xte_nm = cross_track_error_nm(lat, lon, origin_lat, origin_lon, wp_lat, wp_lon)

    return {
        "bearing_deg": round(bearing, 1),
        "course_deg": round(course, 1) if course is not None else None,
        "distance_nm": round(distance_nm, 3),
        "vmg_kn": round(vmg_kn, 2) if vmg_kn is not None else None,
        "ete_s": round(ete_s, 1) if ete_s is not None else None,
        "xte_nm": round(xte_nm, 3) if xte_nm is not None else None,
        "turn_deg": round(signed_angle_diff(cog_deg, bearing), 1) if cog_deg is not None else None,
    }


def relative_bearing(heading_deg, bearing_deg):
    """Bearing relative to boat heading, -180..180 (negative = port, positive = starboard)."""
    return round(signed_angle_diff(heading_deg, bearing_deg), 1)
