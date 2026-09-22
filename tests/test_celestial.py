"""Tests for the sunrise/sunset calculation.

    python -m unittest discover -s tests -t . -v
"""
import unittest
from datetime import date, timedelta

from app.celestial import _PHASE_NAMES, moon_phase, sun_times

OLD_HICKORY_LAT, OLD_HICKORY_LON = 36.306, -86.563  # this dashboard's default chart position


class TestSunTimes(unittest.TestCase):
    def test_sunrise_is_before_sunset(self):
        sunrise, sunset = sun_times(OLD_HICKORY_LAT, OLD_HICKORY_LON, date(2026, 6, 21))
        self.assertIsNotNone(sunrise)
        self.assertLess(sunrise, sunset)

    def test_summer_day_is_longer_than_winter_day_in_the_northern_hemisphere(self):
        summer_rise, summer_set = sun_times(OLD_HICKORY_LAT, OLD_HICKORY_LON, date(2026, 6, 21))
        winter_rise, winter_set = sun_times(OLD_HICKORY_LAT, OLD_HICKORY_LON, date(2026, 12, 21))
        self.assertGreater(summer_set - summer_rise, winter_set - winter_rise)

    def test_day_length_is_close_to_12_hours_at_the_equinox(self):
        rise, set_ = sun_times(OLD_HICKORY_LAT, OLD_HICKORY_LON, date(2026, 3, 20))
        day_length = set_ - rise
        self.assertLess(abs(day_length - timedelta(hours=12)), timedelta(minutes=15))

    def test_day_length_at_the_equator_is_close_to_12_hours_year_round(self):
        for d in (date(2026, 3, 20), date(2026, 6, 21), date(2026, 9, 22), date(2026, 12, 21)):
            rise, set_ = sun_times(0.0, 0.0, d)
            self.assertLess(abs((set_ - rise) - timedelta(hours=12)), timedelta(minutes=10))

    def test_higher_latitude_means_a_longer_summer_day_in_the_northern_hemisphere(self):
        _, _ = sun_times(60.0, 0.0, date(2026, 6, 21))
        low_rise, low_set = sun_times(20.0, 0.0, date(2026, 6, 21))
        high_rise, high_set = sun_times(60.0, 0.0, date(2026, 6, 21))
        self.assertGreater(high_set - high_rise, low_set - low_rise)

    def test_far_north_has_no_sunset_at_summer_solstice(self):
        sunrise, sunset = sun_times(78.0, 15.0, date(2026, 6, 21))  # Svalbard: polar day in June
        self.assertIsNone(sunrise)
        self.assertIsNone(sunset)

    def test_longitude_shifts_the_time_but_not_the_day_length(self):
        rise_a, set_a = sun_times(OLD_HICKORY_LAT, -86.563, date(2026, 6, 21))
        rise_b, set_b = sun_times(OLD_HICKORY_LAT, -70.0, date(2026, 6, 21))  # further east: earlier in UTC
        self.assertLess(rise_b, rise_a)
        self.assertLess(abs((set_a - rise_a) - (set_b - rise_b)), timedelta(minutes=1))

    def test_prints_old_hickory_lake_times_for_a_manual_sanity_check(self):
        # Not an assertion on exact clock time (no live reference to check against here), just a
        # human-readable printout of what the algorithm thinks Old Hickory Lake, TN sees, to compare
        # by eye against known local sunrise/sunset (America/Chicago, UTC-5 in summer / UTC-6 in winter).
        for d in (date(2026, 3, 20), date(2026, 6, 21), date(2026, 9, 22), date(2026, 12, 21)):
            rise, set_ = sun_times(OLD_HICKORY_LAT, OLD_HICKORY_LON, d)
            print(f"{d}: sunrise {rise.isoformat()}  sunset {set_.isoformat()}  day length {set_ - rise}")


class TestMoonPhase(unittest.TestCase):
    def test_name_is_always_one_of_the_eight_known_phases(self):
        for offset in range(0, 60):
            name, _ = moon_phase(date(2026, 1, 1) + timedelta(days=offset))
            self.assertIn(name, _PHASE_NAMES)

    def test_illuminated_fraction_is_always_in_range(self):
        for offset in range(0, 60):
            _, frac = moon_phase(date(2026, 1, 1) + timedelta(days=offset))
            self.assertGreaterEqual(frac, 0.0)
            self.assertLessEqual(frac, 1.0)

    def test_the_reference_new_moon_date_is_a_new_moon(self):
        name, frac = moon_phase(date(2000, 1, 6))
        self.assertEqual(name, "New Moon")
        self.assertLess(frac, 0.05)

    def test_half_a_synodic_month_after_a_new_moon_is_full(self):
        halfway = date(2000, 1, 6) + timedelta(days=round(29.530588853 / 2))  # ~15 days: a full moon
        name, frac = moon_phase(halfway)
        self.assertEqual(name, "Full Moon")
        self.assertGreater(frac, 0.95)

    def test_the_phase_repeats_every_synodic_month(self):
        base = moon_phase(date(2026, 4, 10))
        later = moon_phase(date(2026, 4, 10) + timedelta(days=round(29.530588853 * 3)))
        self.assertEqual(base[0], later[0])
        self.assertLess(abs(base[1] - later[1]), 0.05)

    def test_illumination_rises_from_new_to_full_and_falls_back(self):
        new_moon = moon_phase(date(2000, 1, 6))[1]
        waxing = moon_phase(date(2000, 1, 13))[1]
        full_moon = moon_phase(date(2000, 1, 21))[1]
        waning = moon_phase(date(2000, 1, 28))[1]
        self.assertLess(new_moon, waxing)
        self.assertLess(waxing, full_moon)
        self.assertGreater(full_moon, waning)


if __name__ == "__main__":
    unittest.main()
