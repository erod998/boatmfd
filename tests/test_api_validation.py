"""Bounds on what the REST endpoints will accept.

    python -m unittest discover -s tests -t . -v

main.py is otherwise untested by convention -- it is wiring, and the pieces it wires are
each tested on their own. The request models are the exception, because they are the only
place a bad value can enter the system, and everything downstream (the nav solution, the
track, the alarm state, the saved files) assumes it already did not.

NaN is the case worth being explicit about: JSON carries it, Python parses it, and it
compares False against every bound, so a hand-written `if lat < -90 or lat > 90` waves it
through. One NaN latitude turns every distance computed from it into NaN for the rest of
the trip. These tests exist mostly to keep that door shut.
"""
import unittest

from pydantic import ValidationError

from app.main import (
    AlarmIn,
    ChartSettingsIn,
    CreateBoundaryIn,
    CreateRouteIn,
    CreateWaypointIn,
    LightingIn,
    MediaIn,
    NavAlarmSettingsIn,
    RenameRouteIn,
    UpdateBoundaryIn,
    UpdateWaypointIn,
    VolumeBoostIn,
    WaypointIn,
)

NAN, INF = float("nan"), float("inf")
HERE = {"lat": 36.306, "lon": -86.563}


class TestPositions(unittest.TestCase):
    def test_accepts_a_real_position(self):
        wp = WaypointIn(**HERE, name="Dock")
        self.assertAlmostEqual(wp.lat, 36.306)

    def test_rejects_impossible_latitudes_and_longitudes(self):
        for bad in [{"lat": 91, "lon": 0}, {"lat": -91, "lon": 0},
                    {"lat": 0, "lon": 181}, {"lat": 0, "lon": -181}]:
            with self.subTest(**bad), self.assertRaises(ValidationError):
                WaypointIn(**bad)

    def test_accepts_the_poles_and_the_date_line(self):
        for edge in [{"lat": 90, "lon": 180}, {"lat": -90, "lon": -180}]:
            with self.subTest(**edge):
                WaypointIn(**edge)

    def test_rejects_nan_and_infinite_positions(self):
        # The whole reason these are Field bounds rather than hand-written comparisons.
        for bad in [{"lat": NAN, "lon": 0}, {"lat": 0, "lon": NAN},
                    {"lat": INF, "lon": 0}, {"lat": -INF, "lon": 0}]:
            with self.subTest(lat=bad["lat"], lon=bad["lon"]), self.assertRaises(ValidationError):
                WaypointIn(**bad)

    def test_every_model_that_takes_a_position_bounds_it(self):
        for model in (WaypointIn, CreateWaypointIn):
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                model(lat=NAN, lon=0)
        with self.assertRaises(ValidationError):
            UpdateWaypointIn(lat=999)

    def test_a_partial_waypoint_update_may_leave_a_field_out(self):
        self.assertIsNone(UpdateWaypointIn(name="Renamed").lat)

    def test_names_cannot_be_unbounded(self):
        with self.assertRaises(ValidationError):
            CreateWaypointIn(**HERE, name="x" * 500)


class TestBoundaries(unittest.TestCase):
    def test_accepts_a_sensible_geofence(self):
        b = CreateBoundaryIn(**HERE, radius_ft=300, alarm_on="enter")
        self.assertEqual(b.alarm_on, "enter")

    def test_radius_must_be_positive(self):
        for bad in (0, -1, NAN):
            with self.subTest(radius=bad), self.assertRaises(ValidationError):
                CreateBoundaryIn(**HERE, radius_ft=bad)

    def test_only_the_three_real_alarm_triggers(self):
        for good in ("enter", "exit", "both"):
            with self.subTest(alarm_on=good):
                CreateBoundaryIn(**HERE, radius_ft=100, alarm_on=good)
        with self.assertRaises(ValidationError):
            CreateBoundaryIn(**HERE, radius_ft=100, alarm_on="sometimes")

    def test_the_same_rules_apply_to_an_update(self):
        with self.assertRaises(ValidationError):
            UpdateBoundaryIn(radius_ft=-5)
        with self.assertRaises(ValidationError):
            UpdateBoundaryIn(alarm_on="maybe")


class TestRoutes(unittest.TestCase):
    def test_a_route_needs_at_least_two_points(self):
        with self.assertRaises(ValidationError):
            CreateRouteIn(points=[{"lat": 36.3, "lon": -86.5}])

    def test_two_points_is_a_route(self):
        r = CreateRouteIn(points=[{"lat": 36.3, "lon": -86.5}, {"lat": 36.4, "lon": -86.5}])
        self.assertEqual(len(r.points), 2)

    def test_a_bad_point_anywhere_rejects_the_route(self):
        with self.assertRaises(ValidationError):
            CreateRouteIn(points=[{"lat": 36.3, "lon": -86.5}, {"lat": NAN, "lon": -86.5}])

    def test_route_names_are_bounded(self):
        with self.assertRaises(ValidationError):
            RenameRouteIn(name="x" * 500)


class TestLighting(unittest.TestCase):
    def test_accepts_a_colour_and_brightness(self):
        led = LightingIn(r=255, g=0, b=128, brightness=0.5)
        self.assertEqual(led.r, 255)

    def test_channels_stay_inside_a_byte(self):
        for bad in ({"r": 256}, {"g": -1}, {"b": 999}):
            with self.subTest(**bad), self.assertRaises(ValidationError):
                LightingIn(**bad)

    def test_brightness_is_a_fraction(self):
        for bad in (-0.1, 1.5, NAN):
            with self.subTest(brightness=bad), self.assertRaises(ValidationError):
                LightingIn(brightness=bad)


class TestMedia(unittest.TestCase):
    def test_zone_defaults_to_the_first(self):
        self.assertEqual(MediaIn(action="play").zone, 1)

    def test_zone_stays_within_a_four_zone_head_unit(self):
        for bad in (0, 5, -1):
            with self.subTest(zone=bad), self.assertRaises(ValidationError):
                MediaIn(action="volume", zone=bad)

    def test_values_are_bounded_to_a_byte(self):
        # Volume's real limit (0..24 on this head unit) is enforced by the stereo layer; this bound
        # only has to admit every action's legitimate range, including the unit's own source ids.
        MediaIn(action="source", value=255)
        for bad in (-1, 256):
            with self.subTest(value=bad), self.assertRaises(ValidationError):
                MediaIn(action="volume", value=bad)


class TestAlarmsAndBoost(unittest.TestCase):
    def test_an_alarm_level_may_be_negative_but_not_nan(self):
        AlarmIn(level=-40.0)          # a temperature alarm could legitimately be below zero
        with self.assertRaises(ValidationError):
            AlarmIn(level=NAN)

    def test_boost_percentages_are_percentages(self):
        VolumeBoostIn(boost_pct=100.0, smoothing_pct=0.0)
        for bad in ({"boost_pct": 101}, {"smoothing_pct": -1}):
            with self.subTest(**bad), self.assertRaises(ValidationError):
                VolumeBoostIn(**bad)


class TestNavAlarmSettings(unittest.TestCase):
    def test_radii_must_be_positive(self):
        for bad in ({"arrival_radius_nm": 0}, {"off_course_xte_nm": -1},
                    {"anchor_radius_ft": 0}, {"gps_accuracy_hdop_max": NAN}):
            with self.subTest(**bad), self.assertRaises(ValidationError):
                NavAlarmSettingsIn(**bad)

    def test_everything_is_optional(self):
        self.assertIsNone(NavAlarmSettingsIn().arrival_radius_nm)

    def test_accepts_a_realistic_anchor_watch(self):
        self.assertEqual(NavAlarmSettingsIn(anchor_radius_ft=150.0).anchor_radius_ft, 150.0)




class TestChartSettings(unittest.TestCase):
    def test_a_lake_level_or_none_for_normal_pool(self):
        self.assertEqual(ChartSettingsIn(lake_level_ft=443.5).lake_level_ft, 443.5)
        self.assertIsNone(ChartSettingsIn(lake_level_ft=None).lake_level_ft)

    def test_rejects_nan_infinity_and_absurd_levels(self):
        for bad in (NAN, INF, -INF, 1e9):
            with self.subTest(level=bad), self.assertRaises(ValidationError):
                ChartSettingsIn(lake_level_ft=bad)


class TestGoToPath(unittest.TestCase):
    def test_accepts_a_planned_path(self):
        wp = WaypointIn(**HERE, path=[[36.30, -86.57], [36.305, -86.565], [36.306, -86.563]])
        self.assertEqual(len(wp.path), 3)
        self.assertIsNone(WaypointIn(**HERE).path)

    def test_rejects_paths_that_are_not_real_positions(self):
        for bad in ([[NAN, -86.5], [36.3, -86.5]], [[36.3, INF], [36.3, -86.5]], [[91, 0], [36.3, -86.5]],
                    [[36.3, -86.5]], [[36.3, -86.5, 1.0], [36.3, -86.5]], [[36.3], [36.3, -86.5]]):
            with self.subTest(path=bad), self.assertRaises(ValidationError):
                WaypointIn(**HERE, path=bad)

    def test_rejects_an_absurdly_long_path(self):
        with self.assertRaises(ValidationError):
            WaypointIn(**HERE, path=[[36.3, -86.5]] * 5001)


class TestLightingPreset(unittest.TestCase):
    def test_only_a_known_preset_gets_in(self):
        # Unchecked, an unknown one raised out of the endpoint (HTTP 500) after the rest of the
        # request -- power, brightness, colour -- had already been applied.
        self.assertEqual(LightingIn(preset="rainbow").preset, "rainbow")
        with self.assertRaises(ValidationError):
            LightingIn(preset="strobe", on=True)


class TestStalledScreens(unittest.TestCase):
    def test_a_screen_that_stalls_is_closed_not_just_forgotten(self):
        # Forgotten but left open, it never reconnected: its last frame stayed up as if live.
        import asyncio

        from app import main

        class Stalled:
            closed = None

            async def send_text(self, text):
                await asyncio.sleep(10)

            async def close(self, code=1000):
                self.closed = code

        async def run():
            ws = Stalled()
            main._clients.add(ws)
            try:
                await main._broadcast({"type": "fast"})
                await asyncio.sleep(0.05)
            finally:
                main._clients.discard(ws)
            return ws

        ws = asyncio.run(run())
        self.assertNotIn(ws, main._clients)
        self.assertIsNotNone(ws.closed)


if __name__ == "__main__":
    unittest.main()
