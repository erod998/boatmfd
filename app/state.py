"""App settings and simulated boat sensors (battery, depth, water temp, engine).

Real sensors (battery voltage divider on an ADC, NMEA depth sounder, an
NMEA 2000 engine gateway, etc.) plug in by replacing BoatInfo.read() /
EngineInfo.read() with real reads — the dashboard/API shape stays the same.
"""
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class Settings:
    gps_port: str = os.environ.get("BOAT_GPS_PORT", "")  # e.g. /dev/serial0 — empty = simulator
    gps_baud: int = int(os.environ.get("BOAT_GPS_BAUD", "9600"))
    led_pixel_count: int = int(os.environ.get("BOAT_LED_COUNT", "30"))
    led_gpio_pin: int = int(os.environ.get("BOAT_LED_GPIO", "18"))
    led_driver: str = os.environ.get("BOAT_LED_DRIVER", "mock")  # mock | ws281x (addressable) | pwm (12 V RGB strip) | ledboard
    pca9685_address: int = int(os.environ.get("BOAT_PCA9685_ADDR", "0x40"), 0)
    # The I2C bus the LED board's PCA9685s are on. The sensor board gives them one of their own on
    # GPIO5/6, apart from its converters: dtoverlay=i2c-gpio,bus=7,i2c_gpio_sda=5,i2c_gpio_scl=6 and
    # BOAT_LED_I2C_BUS=7. Without the board they share the main bus, BOAT_I2C_BUS.
    led_i2c_bus: int = int(os.environ.get("BOAT_LED_I2C_BUS", os.environ.get("BOAT_I2C_BUS", "1")))
    led_pwm_channels: str = os.environ.get("BOAT_LED_PWM_CHANNELS", "0,1,2")  # PCA9685 outputs for R,G,B
    # The LED board (BOAT_LED_DRIVER=ledboard, LED_BOARD.md): each addressable output's pixel count
    # (J7-J10), their byte order, and the budget it holds the whole board to. zone/pixel figures are
    # a zone's full-white draw and one pixel's, for working out what a scene will draw before it's sent.
    led_board_pixels: str = os.environ.get("BOAT_LED_PIXELS", "300,300,300,300")
    led_board_order: str = os.environ.get("BOAT_LED_ORDER", "GRB")
    led_board_max_amps: float = float(os.environ.get("BOAT_LED_MAX_AMPS", "20"))
    led_board_zone_amps: float = float(os.environ.get("BOAT_LED_ZONE_AMPS", "8"))
    led_board_pixel_ma: float = float(os.environ.get("BOAT_LED_PIXEL_MA", "25"))
    led_board_white: bool = os.environ.get("BOAT_LED_WHITE", "true").lower() == "true"  # RGBW zones: light whites with W
    # sim | n2k (engine data from a NMEA 2000 converter such as the CX5003) | real (the Pi reads senders, tach and probes itself)
    sensors: str = os.environ.get("BOAT_SENSORS", "sim")
    battery_adc: bool = os.environ.get("BOAT_BATTERY_ADC", "false").lower() == "true"  # with n2k: measure the battery on an ADS1115
    # The sensor board's second battery input (J1.8, HSE+: the house battery, on the second ADS1115's
    # AIN2), beside the engine battery on J1.3.
    house_battery: bool = os.environ.get("BOAT_HOUSE_BATTERY", "false").lower() == "true"
    i2c_bus: int = int(os.environ.get("BOAT_I2C_BUS", "1"))
    ads1115_address: int = int(os.environ.get("BOAT_ADS1115_ADDR", "0x48"), 0)
    tach_gpio: int = int(os.environ.get("BOAT_TACH_GPIO", "17"))
    tach_ppr: int = int(os.environ.get("BOAT_TACH_PPR", "2"))  # pulses per revolution: 2 for a 4-cyl distributor engine
    redline_rpm: int = int(os.environ.get("BOAT_REDLINE_RPM", "4800"))
    oil_sender: bool = os.environ.get("BOAT_OIL_SENDER", "false").lower() == "true"
    # How the Pi reads the senders when BOAT_SENSORS=real:
    #   reference -- it takes each sender over through its own 240 ohm reference resistor, and the
    #                analog gauge on that sender must be disconnected
    #   tap       -- it only listens to the wire each analog gauge already drives, so the gauges stay
    #                connected and keep working (the sensor board in SENSOR_BOARD.md)
    sender_wiring: str = os.environ.get("BOAT_SENDER_WIRING", "reference")
    ads1115_address2: int = int(os.environ.get("BOAT_ADS1115_ADDR2", "0x49"), 0)  # the board's second ADC (oil, temp)
    # Is the engine temperature gauge tapped (the sensor board's J1.6, TEMP)? Its sender then gives
    # the coolant temperature, ahead of a clip-on probe.
    temp_sender: bool = os.environ.get("BOAT_TEMP_SENDER", "false").lower() == "true"
    # Each tap's divider on the sensor board: 49.9k over 10k. (Rev 1.0-1.1 boards had 39k on the
    # board and a 10k at the gauge end of each tap wire: BOAT_TAP_R_TOP=49000 for those.)
    tap_r_top: float = float(os.environ.get("BOAT_TAP_R_TOP", "49900"))
    tap_r_bottom: float = float(os.environ.get("BOAT_TAP_R_BOTTOM", "10000"))
    # The tach GPIO's internal pull: down for the resistor+zener input, none for the sensor board's
    # optocoupler input (which has its own pull-up), up if a board needs it.
    tach_pull: str = os.environ.get("BOAT_TACH_PULL", "down")
    # Is the analog fuel-level sender wired to the ADC? Set false if fuel level comes from NMEA 2000 instead.
    fuel_sender: bool = os.environ.get("BOAT_FUEL_SENDER", "true").lower() == "true"
    n2k_engine_instance: int = int(os.environ.get("BOAT_N2K_ENGINE_INSTANCE", "0"))  # for the engine converter and fuel-flow sensor
    n2k_fuel_tank_instance: int = int(os.environ.get("BOAT_N2K_FUEL_TANK_INSTANCE", "0"))  # for the converter's fuel input or a tank-level adapter
    n2k_temp_field: str = os.environ.get("BOAT_N2K_TEMP", "engine")  # which NMEA 2000 temperature is the coolant: engine | oil
    # Which depth transducer, by its NMEA 2000 source address, when there is more than one; empty = any
    # (the freshest). Depth messages carry no instance number to choose by.
    n2k_depth_source: Optional[int] = int(os.environ["BOAT_N2K_DEPTH_SOURCE"]) if os.environ.get("BOAT_N2K_DEPTH_SOURCE") else None
    sender_ref_ohms: float = float(os.environ.get("BOAT_SENDER_REF_OHMS", "240"))  # reference resistor above each sender
    rail_volts: float = float(os.environ.get("BOAT_RAIL_VOLTS", "3.3"))
    batt_r_top: float = float(os.environ.get("BOAT_BATT_R_TOP", "47000"))
    batt_r_bottom: float = float(os.environ.get("BOAT_BATT_R_BOTTOM", "10000"))
    w1_dir: str = os.environ.get("BOAT_W1_DIR", "/sys/bus/w1/devices")  # DS18B20 temperature probes
    # Tank size, so a fuel percentage can become gallons remaining -- and from there the
    # Fuel Remaining, Economy and Range data fields a GPSMAP shows.
    fuel_capacity_gal: float = float(os.environ.get("BOAT_FUEL_CAPACITY_GAL", "40"))
    engine_hp: float = float(os.environ.get("BOAT_ENGINE_HP", "135"))  # for the fuel-burn estimate
    engine_wot_rpm: float = float(os.environ.get("BOAT_ENGINE_WOT_RPM", "4600"))
    # The CAN interface to the NMEA 2000 network (engine converter, Fusion stereo, fuel sensor), e.g. can0. Empty = simulated stereo.
    # BOAT_FUSION_CAN is the old name and still works.
    can_channel: str = os.environ.get("BOAT_CAN") or os.environ.get("BOAT_FUSION_CAN", "")
    can_interface: str = os.environ.get("BOAT_CAN_INTERFACE", "socketcan")
    n2k_address: int = int(os.environ.get("BOAT_N2K_ADDRESS", "42"))
    fusion_max_volume: int = int(os.environ.get("BOAT_FUSION_MAX_VOLUME", "24"))
    fusion_zones: int = max(1, min(4, int(os.environ.get("BOAT_FUSION_ZONES", "4"))))  # a 4-zone Fusion head unit
    album_art: bool = os.environ.get("BOAT_ALBUM_ART", "true").lower() == "true"  # iTunes lookup by artist/album
    host: str = os.environ.get("BOAT_HOST", "0.0.0.0")
    port: int = int(os.environ.get("BOAT_PORT", "8090"))


def estimate_gph(rpm, hp_wot, rpm_wot, idle_gph=0.7, bsfc=0.45, lb_per_gal=6.1):
    """Rough gasoline burn from RPM: propeller-law horsepower (~rpm^3) times brake-specific fuel
    consumption, plus an idle floor. An estimate only; fill-up calibration corrects the scale."""
    if not rpm or rpm < 500:
        return 0.0
    horsepower = hp_wot * min(rpm / rpm_wot, 1.3) ** 3
    return idle_gph + horsepower * bsfc / lb_per_gal


def _blend(dt, per_second):
    """Fraction of the way to close in dt seconds, given the fraction closed per second."""
    return 1.0 - (1.0 - per_second) ** dt


class BoatInfo:
    """Simulated engine and house batteries, depth sounder and water temperature (BOAT_SENSORS=sim only;
    see sensors.RealBoatInfo for real depth/water-temperature from a NMEA 2000 transducer).

    Dynamics are per second, not per call, so the dashboard can read this at any rate.
    """

    def __init__(self):
        self.battery_voltage = 12.6
        self.house_voltage = 12.8
        self.depth_ft = 14.0
        self.water_temp_f = 68.0
        self._last_t = time.monotonic()

    def read(self, engine_running: bool):
        now = time.monotonic()
        dt = max(0.0, min(now - self._last_t, 2.0))
        self._last_t = now
        wander = math.sqrt(dt)  # random walks scale with the square root of time

        # Alternator lifts the bus to ~13.9V while the engine runs; resting battery sits near 12.6V.
        target_v = 13.9 if engine_running else 12.6
        self.battery_voltage += (target_v - self.battery_voltage) * _blend(dt, 0.2) + random.uniform(-0.03, 0.03) * wander
        self.battery_voltage = max(10.0, min(16.0, self.battery_voltage))
        # The house battery, which runs the lights and the stereo: charged from the engine's side
        # (a DC-DC charger) while it runs, otherwise slowly drawn down.
        house_target = 13.6 if engine_running else 12.7
        self.house_voltage += (house_target - self.house_voltage) * _blend(dt, 0.05) + random.uniform(-0.02, 0.02) * wander
        self.house_voltage = max(10.0, min(16.0, self.house_voltage))
        # depth wanders around a lake-like 14 ft rather than drifting off for good
        self.depth_ft += (14.0 - self.depth_ft) * _blend(dt, 0.02) + random.uniform(-0.3, 0.3) * wander
        self.depth_ft = max(2.0, min(60.0, self.depth_ft))
        self.water_temp_f = max(50.0, min(85.0, self.water_temp_f + random.uniform(-0.05, 0.05) * wander))

        return {
            "battery_voltage": round(self.battery_voltage, 2),
            "house_battery_voltage": round(self.house_voltage, 2),
            "depth_ft": round(self.depth_ft, 1),
            "water_temp_f": round(self.water_temp_f, 1),
        }


class EngineInfo:
    """Simulated outboard/inboard engine instrument cluster (all read-only).

    RPM tracks boat speed (like a real engine would); oil pressure follows
    RPM (low at idle, builds and plateaus once running); fuel burns down
    proportionally to RPM over time; trim drifts out as speed builds, the
    way an operator (or auto-trim) sets it once the boat gets on plane.

    Swap for real reads later: RPM/oil pressure/trim typically arrive over
    NMEA 2000 (an engine gateway), fuel level from a tank sender via ADC.
    """

    REDLINE_RPM = 5500
    MAX_RPM = 6000

    def __init__(self):
        self.rpm = 650.0
        self.oil_pressure_psi = 0.0
        self.fuel_pct = 92.0
        self.trim_pct = 15.0
        self.coolant_f = 165.0
        self._last_t = time.time()

    def read(self, sog_kn: float):
        """Dynamics are per second, not per call, so this can be read at any rate."""
        now = time.time()
        dt = max(0.0, min(now - self._last_t, 2.0))
        dt_hours = dt / 3600.0
        self._last_t = now
        wander = math.sqrt(dt)  # random walks scale with the square root of time

        target_rpm = 700 + min(sog_kn, 35.0) / 35.0 * 3500  # idle to ~4200 RPM at a ~40 mph cruise
        # A real tach at steady cruise barely moves; +-40 here (a random walk, not just per-tick jitter,
        # since it's added straight into the running state) used to make even the smoothest gauge setting
        # look shaky, because no amount of easing can hide noise with genuinely slow, drifting components.
        self.rpm += (target_rpm - self.rpm) * _blend(dt, 0.3) + random.uniform(-8, 8) * wander
        self.rpm = max(0.0, min(self.MAX_RPM, self.rpm))

        running = self.rpm > 500
        target_oil = 20 + min(self.rpm / self.REDLINE_RPM, 1.0) * 40 if running else 0
        self.oil_pressure_psi += (target_oil - self.oil_pressure_psi) * _blend(dt, 0.25) + random.uniform(-1.5, 1.5) * wander
        self.oil_pressure_psi = max(0.0, min(80.0, self.oil_pressure_psi))

        burn_gph = estimate_gph(self.rpm, 135, 4600)
        if running:
            self.fuel_pct = max(0.0, self.fuel_pct - (burn_gph / 40.0) * dt_hours * 100)

        target_trim = 10 + min(sog_kn, 9.0) / 9.0 * 60
        self.trim_pct += (target_trim - self.trim_pct) * _blend(dt, 0.05) + random.uniform(-0.3, 0.3) * wander
        self.trim_pct = max(0.0, min(100.0, self.trim_pct))

        target_coolant = 165 + (self.rpm / self.MAX_RPM) * 25
        self.coolant_f += (target_coolant - self.coolant_f) * _blend(dt, 0.03) + random.uniform(-0.3, 0.3) * wander

        return {
            "rpm": round(self.rpm),
            "redline_rpm": self.REDLINE_RPM,
            "max_rpm": self.MAX_RPM,
            "oil_pressure_psi": round(self.oil_pressure_psi, 1),
            "fuel_pct": round(self.fuel_pct, 1),
            "fuel_gph": round(burn_gph, 1),
            "fuel_gph_est": True,
            "trim_pct": round(self.trim_pct, 1),
            "coolant_f": round(self.coolant_f),
        }
