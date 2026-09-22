"""Great-circle navigation math: bearing, distance, ETA, cross-track error."""
import math

EARTH_RADIUS_NM = 3440.065  # nautical miles


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
    """Perpendicular distance (nm) of current position from the start->end great-circle route."""
    d13 = haversine_distance_nm(start_lat, start_lon, lat, lon) / EARTH_RADIUS_NM
    brng13 = math.radians(initial_bearing_deg(start_lat, start_lon, lat, lon))
    brng12 = math.radians(initial_bearing_deg(start_lat, start_lon, end_lat, end_lon))
    return math.asin(math.sin(d13) * math.sin(brng13 - brng12)) * EARTH_RADIUS_NM


def waypoint_nav(lat, lon, sog_kn, wp_lat, wp_lon, origin_lat=None, origin_lon=None):
    """Return bearing/distance/eta/xte from current position to a waypoint."""
    distance_nm = haversine_distance_nm(lat, lon, wp_lat, wp_lon)
    bearing = initial_bearing_deg(lat, lon, wp_lat, wp_lon)
    eta_hours = (distance_nm / sog_kn) if sog_kn and sog_kn > 0.1 else None
    xte_nm = None
    if origin_lat is not None and origin_lon is not None:
        xte_nm = cross_track_error_nm(lat, lon, origin_lat, origin_lon, wp_lat, wp_lon)
    return {
        "bearing_deg": round(bearing, 1),
        "distance_nm": round(distance_nm, 3),
        "eta_minutes": round(eta_hours * 60, 1) if eta_hours is not None else None,
        "xte_nm": round(xte_nm, 3) if xte_nm is not None else None,
    }


def relative_bearing(heading_deg, bearing_deg):
    """Bearing relative to boat heading, -180..180 (negative = port, positive = starboard)."""
    diff = (bearing_deg - heading_deg + 540) % 360 - 180
    return round(diff, 1)
