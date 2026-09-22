"""Sunrise, sunset and moon phase, the way a Garmin chartplotter's Celestial Information works,
without a network call: the standard astronomical "sunrise equation"
(en.wikipedia.org/wiki/Sunrise_equation), accurate to within a couple of minutes for civil use
(not celestial navigation), and a synodic-month phase estimate. Tide and current predictions and
moonrise/moonset from that same manual page are skipped: this boat is on a freshwater lake, not
tidal water, so tide/current wouldn't mean anything here, and moonrise/moonset (unlike sunrise/
sunset) needs the moon's actual position, not just a phase angle — a meaningfully bigger and
riskier calculation than this dashboard's use case (knowing whether it'll be a dark night) needs.
"""
import math
from datetime import datetime, timedelta, timezone

_J2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # Julian date 2451545.0
_ZENITH = math.radians(-0.833)  # standard sunrise/sunset altitude: -50' (solar radius + average refraction)
_OBLIQUITY = math.radians(23.4397)  # Earth's axial tilt
_KNOWN_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)  # a new moon, any one will do as a reference
_SYNODIC_MONTH_DAYS = 29.530588853  # average new-moon-to-new-moon period
_PHASE_NAMES = ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
                "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent"]


def sun_times(lat, lon, on_date):
    """(sunrise, sunset) as UTC datetimes for `on_date` (a date) at (lat, lon) in degrees, or
    (None, None) if the sun doesn't cross the horizon that day (only possible near the poles)."""
    midday_utc = datetime(on_date.year, on_date.month, on_date.day, 12, 0, 0, tzinfo=timezone.utc)
    days_since_j2000 = (midday_utc - _J2000).total_seconds() / 86400.0

    mean_anomaly_deg = (357.5291 + 0.98560028 * (days_since_j2000 - lon / 360.0)) % 360
    mean_anomaly = math.radians(mean_anomaly_deg)
    center_deg = (1.9148 * math.sin(mean_anomaly) + 0.0200 * math.sin(2 * mean_anomaly)
                  + 0.0003 * math.sin(3 * mean_anomaly))
    ecliptic_long = math.radians((mean_anomaly_deg + center_deg + 180 + 102.9372) % 360)

    declination = math.asin(math.sin(ecliptic_long) * math.sin(_OBLIQUITY))
    lat_rad = math.radians(lat)
    denom = math.cos(lat_rad) * math.cos(declination)
    if denom == 0:
        return None, None
    cos_hour_angle = (math.sin(_ZENITH) - math.sin(lat_rad) * math.sin(declination)) / denom
    if not -1 <= cos_hour_angle <= 1:
        return None, None  # polar day or polar night: the sun doesn't cross the horizon today

    transit_days = (days_since_j2000 - lon / 360.0) + 0.0053 * math.sin(mean_anomaly) - 0.0069 * math.sin(2 * ecliptic_long)
    hour_angle_days = math.degrees(math.acos(cos_hour_angle)) / 360.0
    return _J2000 + timedelta(days=transit_days - hour_angle_days), _J2000 + timedelta(days=transit_days + hour_angle_days)


def moon_phase(on_date):
    """(name, illuminated_fraction) at local noon on `on_date` (a date): illuminated_fraction is
    0 at New Moon, 1 at Full Moon. Longitude/observer position doesn't affect the phase (everyone
    on Earth sees the same one on the same date), so unlike sun_times this only takes a date."""
    midday_utc = datetime(on_date.year, on_date.month, on_date.day, 12, 0, 0, tzinfo=timezone.utc)
    days_since_new = (midday_utc - _KNOWN_NEW_MOON).total_seconds() / 86400.0
    age_days = days_since_new % _SYNODIC_MONTH_DAYS
    cycle_fraction = age_days / _SYNODIC_MONTH_DAYS  # 0 = new, 0.5 = full, ->1 = new again
    illuminated_fraction = (1 - math.cos(2 * math.pi * cycle_fraction)) / 2
    name = _PHASE_NAMES[round(cycle_fraction * 8) % 8]
    return name, illuminated_fraction
