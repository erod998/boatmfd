"""The sensor board, as data: every part, what each of its pins connects to, and where it goes.

This is the single source of truth. schematic.py draws the schematic from it and board.py lays
out and routes the PCB from it, so the two cannot disagree -- and KiCad's own parity check
(`kicad-cli pcb drc --schematic-parity`, run by build.py) proves they don't.

What the board does, and why each part is there, is in ../../SENSOR_BOARD.md. The input
channels here must match the software's channel map in app/sensors.py (tap mode):
fuel 0, trim 1, battery 2, gauge supply 3 on the first ADS1115, oil 4 and temp 5 on the second.
"""
from dataclasses import dataclass, field

TITLE = "Boat MFD sensor board"
REVISION = "1.3"
DATE = "2026-09-24"

# ---------------------------------------------------------------- the input channels
# (name, helm-connector pin, ADC, ADC pin, top resistor). ADS1115 pins: AIN0=4, AIN1=5, AIN2=6, AIN3=7.
# The top resistor is 49.9k for a gauge tap and 47k for the battery, all over 10k, all on the board:
# the tap wires run straight from the gauge terminals to J1. (Rev 1.0-1.1 split a tap's top
# resistor into 10k at the gauge end of the wire, in case the wire chafed, and 39k here.)
# Listed in board order, top to bottom. Battery comes before the gauge supply so their traces reach
# U1's right-hand pins (AIN2 below AIN3) around the chip without crossing.
CHANNELS = [
    ("FUEL", 1, "U1", 4, "49.9k"),  # software channel 0
    ("TRIM", 2, "U1", 5, "49.9k"),  # 1
    ("BATT", 3, "U1", 6, "47k"),    # 2
    ("GAUGE", 4, "U1", 7, "49.9k"), # 3: the gauges' own supply (I terminal)
    ("OIL", 5, "U2", 4, "49.9k"),   # 4
    ("TEMP", 6, "U2", 5, "49.9k"),  # 5: the engine temperature gauge (BOAT_TEMP_SENDER=true)
]

# Pi header: physical pin -> net. Everything not listed is left unconnected.
PI_PINS = {1: "+3V3", 17: "+3V3", 3: "SDA", 5: "SCL",
           2: "+5V", 4: "+5V",   # the Pi is powered from this board (J7), through the header
           33: "TACH_GPIO",   # GPIO13: tach            (BOAT_TACH_GPIO=13)
           # NMEA 2000: the CAN controller on SPI0, interrupt on GPIO25 -- the pins every Pi CAN HAT
           # uses, so the stock overlay drives it:
           #   dtparam=spi=on   dtoverlay=mcp251xfd,spi0-0,oscillator=40000000,interrupt=25
           19: "SPI_MOSI",    # GPIO10
           21: "SPI_MISO",    # GPIO9
           23: "SPI_SCLK",    # GPIO11
           24: "SPI_CE0",     # GPIO8
           22: "CAN_INT",     # GPIO25
           # LIGHTS (J6): the link to the LED board (LED_BOARD.md). Everything on that board -- its
           # PCA9685, its current monitor, and the RP2040 that runs its addressable strips -- is on
           # one I2C bus of its own, so a fault out there can't stall the converters' bus and with
           # it the engine data. A kernel-driven one (i2c-gpio) on two inner-row pins, which also
           # honours clock stretching (the RP2040 stretches), as the Pi's hardware I2C does not:
           #   dtoverlay=i2c-gpio,bus=7,i2c_gpio_sda=5,i2c_gpio_scl=6   (BOAT_LED_I2C_BUS=7)
           # U8 carries it down the RJ45's patch cable as differential I2C. (Rev 1.2 also gave the
           # LED board GPIO 18, 19 and 21 for addressable data; the LED board's RP2040 makes that
           # data itself now, and those pins are free.)
           29: "LIGHT_SDA",   # GPIO5
           31: "LIGHT_SCL",   # GPIO6
           6: "GND", 9: "GND", 14: "GND", 20: "GND", 25: "GND", 30: "GND", 34: "GND", 39: "GND"}

# The ignition side of the tach input. Kept 3 mm from everything else (board.py, and the
# custom rule in sensor-board.kicad_dru).
TACH_HV_NETS = ["TACH_IN", "TACH_A", "TACH_B", "TACH_LED", "TACH_GND"]

# The NMEA 2000 network's side of the CAN isolator: powered from the network's own NET-S and
# referenced to its NET-C, never to the Pi's ground. Kept 2 mm from everything else (board.py,
# sensor-board.kicad_dru), with its own ground pour.
N2K_BUS_NETS = ["N2K_NET_S", "N2K_12V", "N2K_GND", "N2K_5V", "N2K_H", "N2K_L", "N2K_TERM"]


@dataclass
class Part:
    ref: str
    symbol: str          # KiCad library symbol, lib:name
    value: str
    footprint: str       # KiCad library footprint, lib:name
    pins: dict           # pin number (str) -> net name; pins not listed are no-connects
    mpn: str = ""
    note: str = ""
    sch: tuple = (0, 0)  # schematic position, mm (on the 1.27 mm grid)
    pcb: tuple = None    # (x, y, rotation) in board mm, top side unless flipped
    flip: bool = False   # on the bottom of the board
    dnp: bool = False
    extra: dict = field(default_factory=dict)


# The small parts use the project library (footprints.py): KiCad's own footprints with their
# silkscreen moved to the fab layer, since this board is too dense for their outlines.
R0805, R1206 = "sensor-board:R_0805_2012Metric_NoSilk", "sensor-board:R_1206_3216Metric_NoSilk"
C0805, C1206 = "sensor-board:C_0805_2012Metric_NoSilk", "sensor-board:C_1206_3216Metric_NoSilk"


def yageo(value, size):
    code = {"49.9k": "49K9L", "47k": "47KL", "10k": "10KL", "1k": "1KL", "3.3k": "3K3L", "4.7k": "4K7L", "100": "100RL",
            "120": "120RL", "620": "620RL"}[value]
    return f"RC{size}FR-07{code}"


def resistor(ref, value, a, b, size="0805", **kw):
    fp = {"1206": R1206, "0402": "sensor-board:R_0402_1005Metric_NoSilk"}.get(size, R0805)
    return Part(ref, "Device:R", value, fp, {"1": a, "2": b},
                mpn=yageo(value, size), **kw)


def capacitor(ref, value, a, b, size="0805", **kw):
    if size == "1206":   # C16 sees the NMEA 2000 network's 12 V: 50 V; C21 is the 5 V bulk, 25 V (less DC-bias loss)
        return Part(ref, "Device:C", value, C1206, {"1": a, "2": b},
                    mpn={"1u": "CL31B105KBHNNNE", "22u": "CL31A226KAHNNNE"}[value], **kw)
    if size == "0402":   # the LED link's, on the RJ45's tab
        return Part(ref, "Device:C", value, "sensor-board:C_0402_1005Metric_NoSilk", {"1": a, "2": b},
                    mpn={"100n": "CL05B104KO5NNNC"}[value], **kw)
    mpn = {"100n": "CL21B104KBCNNNC", "1u": "CL21B105KAFNNNE", "10n": "CL21B103KBANNNC",
           "4.7u": "CL21A475KAQNNNE"}[value]
    return Part(ref, "Device:C", value, C0805, {"1": a, "2": b}, mpn=mpn, **kw)


def build_parts():
    parts = []

    # ---------------- analog front ends: one per channel ----------------
    for i, (name, _, _, _, top) in enumerate(CHANNELS):
        x = 88.9 + i * 40.64   # schematic column
        n_in, n_div, n_adc = f"{name}_IN", f"{name}_DIV", f"{name}_ADC"
        parts += [
            resistor(f"R{1 + i}", top, n_in, n_div, size="1206", sch=(x, 66.04),
                     note="tap top resistor (1206 for voltage rating)"),
            Part(f"D{1 + i}", "Diode:BAT54S", "BAT54S", "sensor-board:SOT-23_NoSilk",
                 {"1": "GND", "2": "+3V3", "3": n_div}, mpn="BAT54S,215", sch=(x + 15.24, 81.28),
                 note="clamps the divider node between GND and 3V3"),
            resistor(f"R{7 + i}", "10k", n_div, "GND", sch=(x, 93.98), note="divider bottom"),
            resistor(f"R{13 + i}", "1k", n_div, n_adc, sch=(x + 10.16, 106.68), note="ADC pin series resistor"),
            capacitor(f"C{1 + i}", "100n", n_adc, "GND", sch=(x + 20.32, 119.38), note="ADC input filter"),
        ]

    # ---------------- converters ----------------
    adc_pins = {"U1": {"1": "GND", "3": "GND", "8": "+3V3", "9": "SDA", "10": "SCL"},        # ADDR=GND -> 0x48
                "U2": {"1": "+3V3", "3": "GND", "8": "+3V3", "9": "SDA", "10": "SCL"}}       # ADDR=VDD -> 0x49
    for name, _, adc, pin, _ in CHANNELS:
        adc_pins[adc][str(pin)] = f"{name}_ADC"
    parts += [
        Part("U1", "Analog_ADC:ADS1115IDGS", "ADS1115IDGSR", "Package_SO:TSSOP-10_3x3mm_P0.5mm", adc_pins["U1"],
             mpn="ADS1115IDGSR", sch=(345.44, 71.12), note="I2C 0x48: fuel, trim, battery, gauge supply"),
        Part("U2", "Analog_ADC:ADS1115IDGS", "ADS1115IDGSR", "Package_SO:TSSOP-10_3x3mm_P0.5mm", adc_pins["U2"],
             mpn="ADS1115IDGSR", sch=(345.44, 124.46), note="I2C 0x49: oil, engine temperature"),
        capacitor("C7", "100n", "+3V3", "GND", sch=(375.92, 60.96), note="U1 decoupling"),
        capacitor("C8", "1u", "+3V3", "GND", sch=(386.08, 60.96), note="U1 decoupling"),
        capacitor("C9", "100n", "+3V3", "GND", sch=(375.92, 114.3), note="U2 decoupling"),
        capacitor("C10", "1u", "+3V3", "GND", sch=(386.08, 114.3), note="U2 decoupling"),
    ]

    # ---------------- tach: the Delco EST coil's TACH terminal (gray wire), through an optocoupler ----------------
    # The EST coil has two terminals, BAT (+12 V in) and TACH. TACH is the coil's switched side: it
    # sits at 12 V, drops to ground while the coil charges, and flies up to a few hundred volts at
    # each spark. The input takes that: ~1 mA from the wire at 12 V, and the spike is shared
    # across three 1206 resistors, all isolated from the Pi by the optocoupler.
    parts += [
        resistor("R19", "3.3k", "TACH_IN", "TACH_A", size="1206", sch=(99.06, 190.5), note="tach input: 3 in series share the ignition spike"),
        resistor("R20", "3.3k", "TACH_A", "TACH_B", size="1206", sch=(111.76, 190.5), note="tach input: 3 in series share the ignition spike"),
        resistor("R21", "3.3k", "TACH_B", "TACH_LED", size="1206", sch=(124.46, 190.5), note="tach input: 3 in series share the ignition spike"),
        capacitor("C11", "10n", "TACH_LED", "TACH_GND", sch=(139.7, 205.74), note="filters the ring-down after each spark"),
        Part("D7", "Diode:1N4148W", "1N4148W", "Diode_SMD:D_SOD-123", {"1": "TACH_LED", "2": "TACH_GND"},
             mpn="1N4148W-7-F", sch=(154.94, 205.74), note="keeps the ring-down's negative swing off the LED"),
        Part("U3", "Isolator:PC817", "EL817", "Package_DIP:SMDIP-4_W9.53mm",
             {"1": "TACH_LED", "2": "TACH_GND", "3": "GND", "4": "TACH_OUT"},
             mpn="EL817S1(C)(TU)-F", sch=(187.96, 200.66),
             note="isolates the Pi from the ignition"),
        resistor("R22", "10k", "TACH_OUT", "+3V3", sch=(213.36, 187.96), note="opto pull-up"),
        resistor("R23", "1k", "TACH_OUT", "TACH_GPIO", sch=(228.6, 203.2), note="protects the GPIO"),
    ]

    # (Rev 1.0-1.1 had a 1-Wire input for DS18B20 probes here, J3, D8, R24, R25. This boat reads
    # every temperature from its own senders -- the engine's through the temp gauge (TEMP), the
    # water's from the depth transducer on NMEA 2000 -- so it went in rev 1.2.)

    # ---------------- the LED board's I2C bus: pull-ups, so it idles high with nothing plugged in ----------------
    parts += [
        resistor("R24", "4.7k", "LIGHT_SDA", "+3V3", sch=(350.52, 246.38), note="LED board I2C pull-up (SDA, GPIO5)"),
        resistor("R25", "4.7k", "LIGHT_SCL", "+3V3", sch=(360.68, 246.38), note="LED board I2C pull-up (SCL, GPIO6)"),
    ]

    # ---------------- NMEA 2000: an isolated CAN interface ----------------
    # For the Fusion stereo, and anything else on the backbone (the depth transducer). A CAN FD
    # controller on the Pi's SPI bus, and an isolated transceiver whose bus side is powered from the
    # network's own 12 V (NET-S), as every NMEA 2000 device's is: the Pi's ground never joins the
    # network's, so no ground loop through the stereo's power and the Pi's. About 1 LEN.
    parts += [
        Part("U4", "Interface_CAN_LIN:MCP2517FD-xSL", "MCP2518FD", "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm",
             {"1": "CAN_TXD", "2": "CAN_RXD", "4": "CAN_INT", "6": "CAN_CLK", "7": "GND",
              "10": "SPI_SCLK", "11": "SPI_MOSI", "12": "SPI_MISO", "13": "SPI_CE0", "14": "+3V3"},
             mpn="MCP2518FDT-E/SL", sch=(299.72, 320.04),
             note="SPI CAN FD controller (pin-for-pin successor to the MCP2517FD; Linux driver mcp251xfd)"),
        Part("Y1", "Oscillator:ASE-xxxMHz", "40MHz", "Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm",
             {"1": "+3V3", "2": "GND", "3": "CAN_CLK", "4": "+3V3"},
             mpn="ASE-40.000MHZ-L-C-T", sch=(345.44, 304.8),
             note="U4's clock (oscillator=40000000 in the overlay)"),
        capacitor("C12", "100n", "+3V3", "GND", sch=(368.3, 337.82), note="U4 decoupling"),
        capacitor("C13", "1u", "+3V3", "GND", sch=(381.0, 337.82), note="U4 decoupling"),
        capacitor("C14", "100n", "+3V3", "GND", sch=(393.7, 337.82), note="Y1 decoupling"),
        resistor("R27", "10k", "SPI_CE0", "+3V3", sch=(332.74, 347.98), note="keeps U4 deselected while the Pi boots"),
        resistor("R28", "10k", "CAN_INT", "+3V3", sch=(345.44, 347.98), note="interrupt pull-up"),
        Part("U5", "Interface_CAN_LIN:ISO1044BD", "ISO1044BD", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
             {"1": "+3V3", "2": "CAN_TXD", "3": "CAN_RXD", "4": "GND",
              "5": "N2K_L", "6": "N2K_H", "7": "N2K_GND", "8": "N2K_5V"},
             mpn="ISO1044BDR", sch=(226.06, 320.04),
             note="isolated CAN transceiver: the Pi side and the network side share no ground"),
        capacitor("C15", "100n", "+3V3", "GND", sch=(254.0, 342.9), note="U5 Pi-side decoupling"),
        # the network side
        Part("D9", "Diode:SS14", "SS14", "Diode_SMD:D_SMA", {"1": "N2K_12V", "2": "N2K_NET_S"},
             mpn="SS14", sch=(76.2, 312.42), note="reverse-polarity protection on NET-S"),
        Part("D10", "Diode:SMAJ18A", "SMAJ18A", "Diode_SMD:D_SMA", {"1": "N2K_12V", "2": "N2K_GND"},
             mpn="SMAJ18A", sch=(96.52, 332.74), note="clamps surges on the network's power"),
        capacitor("C16", "1u", "N2K_12V", "N2K_GND", size="1206", sch=(111.76, 332.74), note="regulator input (50 V)"),
        Part("U6", "Regulator_Linear:L78L05_SOT89", "UA78L05", "Package_TO_SOT_SMD:SOT-89-3",
             {"1": "N2K_5V", "2": "N2K_GND", "3": "N2K_12V"}, mpn="UA78L05ACPK", sch=(137.16, 312.42),
             note="5 V for U5's network side, from NET-S"),
        capacitor("C17", "4.7u", "N2K_5V", "N2K_GND", sch=(160.02, 332.74), note="regulator output; U5's VCC2 bulk (TI: ~4.7 uF)"),
        capacitor("C18", "100n", "N2K_5V", "N2K_GND", sch=(172.72, 332.74), note="U5 network-side decoupling"),
        Part("D11", "Power_Protection:NUP2105L", "NUP2105L", "sensor-board:SOT-23_NoSilk",
             {"1": "N2K_H", "2": "N2K_L", "3": "N2K_GND"}, mpn="NUP2105LT1G", sch=(190.5, 350.52),
             note="ESD and surge protection for NET-H / NET-L"),
        resistor("R26", "120", "N2K_H", "N2K_TERM", sch=(208.28, 358.14), note="bench terminator, only with JP1 bridged"),
        Part("JP1", "Jumper:SolderJumper_2_Open", "TERM", "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
             {"1": "N2K_TERM", "2": "N2K_L"}, sch=(241.3, 360.68),
             note="bridge ONLY to test on the bench with no backbone: a device on a real network must not terminate it"),
    ]

    # ---------------- 5 V in: the Pi and this board, from an external 12 V -> 5 V converter ----------------
    # The converter's output comes in on J7 and runs the Pi through the header's 5 V pins
    # (back-powering: 5 V +/-5 %, up to 2.5 A, as the HAT design guide allows); this board's 3.3 V
    # then comes from the Pi as before. In the way sits an ideal diode, U7 driving Q1: about 20 mV
    # forward drop instead of a Schottky's 0.4 V, which the Pi's 4.63 V undervoltage warning
    # couldn't spare. J7 wired backwards is blocked (U7 is rated to -65 V), and with the Pi's own
    # USB-C plugged in as well, that supply can't push current back into the converter. D12 clamps
    # spikes on the rail. None of it saves the Pi from a converter that fails with 12 V on its
    # output: use one with output overvoltage protection.
    parts += [
        Part("U7", "Power_Management:LM74700", "LM74700-Q1", "Package_TO_SOT_SMD:SOT-23-6",
             {"1": "VCAP", "2": "GND", "3": "VIN", "4": "+5V", "5": "IDEAL_G", "6": "VIN"},
             mpn="LM74700QDBVRQ1", sch=(464.82, 78.74),
             note="ideal diode controller: ANODE/EN to J7, CATHODE to the Pi's 5 V"),
        Part("Q1", "Transistor_FET:IRLML0030", "IRLML0030", "Package_TO_SOT_SMD:SOT-23",
             {"1": "IDEAL_G", "2": "VIN", "3": "+5V"}, mpn="IRLML0030TRPBF", sch=(500.38, 78.74),
             note="the ideal diode's switch: source to J7, drain to the Pi; 27 mOhm, VGS +/-20 V"),
        capacitor("C19", "100n", "VCAP", "VIN", sch=(452.12, 106.68), note="U7 charge pump (VCAP to ANODE)"),
        capacitor("C20", "100n", "VIN", "GND", sch=(431.8, 106.68), note="J7 input"),
        capacitor("C21", "22u", "+5V", "GND", size="1206", sch=(487.68, 106.68), note="bulk at the Pi's 5 V pins"),
        Part("D12", "Diode:SMAJ5.0A", "SMAJ5.0A", "Diode_SMD:D_SMA", {"1": "+5V", "2": "GND"},
             mpn="SMAJ5.0A", sch=(510.54, 106.68), note="clamps spikes on the 5 V rail"),
    ]

    # ---------------- connectors ----------------
    helm = {str(pin): f"{name}_IN" for name, pin, _, _, _ in CHANNELS}
    helm.update({"7": "GND", "8": "GND"})
    parts += [
        Part("J1", "Connector_Generic:Conn_01x08", "HELM",
             "Connector_Phoenix_MC:PhoenixContact_MC_1,5_8-GF-3.81_1x08_P3.81mm_Horizontal_ThreadedFlange", helm,
             mpn="Phoenix Contact 1827923 (MC 1,5/ 8-GF-3,81); plug 1827761 (MC 1,5/ 8-STF-3,81)", sch=(35.56, 157.48),
             note="FUEL/TRIM/OIL/TEMP: that gauge's S terminal; BAT+: the dashboard's switched 12 V (1 A fuse); IGN: any gauge's I terminal; GND: gauge G / battery -"),
        Part("J2", "Connector_Generic:Conn_01x02", "TACH",
             "Connector_Phoenix_MC:PhoenixContact_MC_1,5_2-GF-3.81_1x02_P3.81mm_Horizontal_ThreadedFlange",
             {"1": "TACH_IN", "2": "TACH_GND"}, mpn="Phoenix Contact 1827868 (MC 1,5/ 2-GF-3,81); plug 1827703 (MC 1,5/ 2-STF-3,81)",
             sch=(35.56, 200.66), note="TACH: the gray wire (EST coil TACH terminal); GND: the tach gauge's G terminal"),
        Part("J4", "Connector_Generic:Conn_02x20_Odd_Even", "RPi GPIO",
             "Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical", {str(k): v for k, v in PI_PINS.items()},
             mpn="2x20 female stacking header, 2.54 mm, extra-tall (e.g. Adafruit 1979)", sch=(35.56, 83.82),
             note="to the Raspberry Pi"),
        Part("J5", "Connector_Generic:Conn_01x05", "NMEA 2000",
             "Connector_Phoenix_MC:PhoenixContact_MC_1,5_5-GF-3.81_1x05_P3.81mm_Horizontal_ThreadedFlange",
             {"2": "N2K_NET_S", "3": "N2K_GND", "4": "N2K_H", "5": "N2K_L"},
             mpn="Phoenix Contact 1827897 (MC 1,5/ 5-GF-3,81); plug 1827732 (MC 1,5/ 5-STF-3,81)", sch=(35.56, 320.04),
             note="NMEA 2000 drop cable, Micro-C order: 1 shield (bare, not connected), 2 NET-S red, 3 NET-C black, 4 NET-H white, 5 NET-L blue"),
        Part("J7", "Connector_Generic:Conn_01x02", "5V IN",
             "Connector_Phoenix_MC:PhoenixContact_MC_1,5_2-GF-3.81_1x02_P3.81mm_Horizontal_ThreadedFlange",
             {"1": "VIN", "2": "GND"}, mpn="Phoenix Contact 1827868 (MC 1,5/ 2-GF-3,81); plug 1827703 (MC 1,5/ 2-STF-3,81)",
             sch=(421.64, 78.74), note="from the 12 V -> 5 V converter (set 5.1-5.2 V, 3 A): 1 +5V, 2 GND"),
        # Lights: the link to the LED board, over an ordinary straight-through Cat5e/Cat6 patch
        # cable. U8 turns the LED board's I2C bus into differential I2C on two of the cable's
        # twisted pairs, and the LED board's PCA9615 turns it back: noise-proof over the boat's
        # cable runs, and with no ground in the cable, the strips' current can't come back through
        # it. Each pair is terminated at both ends: 620 ohm up, 120 across, 620 down (NXP's figures
        # at 5 V, the same as the LED board's end). Pairs 4/5 and 7/8 are reserved.
        Part("J6", "Connector:RJ45", "LIGHTS", "Connector_RJ:RJ45_Amphenol_54602-x08_Horizontal",
             {"1": "LINK_SCLM", "2": "LINK_SCLP", "3": "LINK_SDAP", "6": "LINK_SDAM"},
             mpn="Amphenol 54602-908LF", sch=(271.78, 248.92),
             note="to the LED board's J1: a straight-through Cat5e/Cat6 patch cable. NOT Ethernet: never plug "
                  "either end into a network switch or a PoE injector"),
        Part("U8", "Interface:PCA9615DP", "PCA9615DP", "Package_SO:TSSOP-10_3x3mm_P0.5mm",
             {"1": "+3V3", "2": "LIGHT_SDA", "3": "+3V3", "4": "LIGHT_SCL", "5": "GND",
              "6": "LINK_SCLM", "7": "LINK_SCLP", "8": "LINK_SDAP", "9": "LINK_SDAM", "10": "+5V"},
             mpn="PCA9615DP,118", sch=(317.5, 251.46),
             note="the LED board's I2C bus (GPIO5/6, 3.3 V) as differential I2C (5 V) on the RJ45"),
        capacitor("C22", "100n", "+3V3", "GND", size="0402", sch=(441.96, 248.92), note="U8 decoupling, 3.3 V side"),
        capacitor("C23", "100n", "+5V", "GND", size="0402", sch=(452.12, 248.92), note="U8 decoupling, cable side"),
        resistor("R29", "620", "+5V", "LINK_SCLP", size="0402", sch=(381.00, 248.92), note="link SCL pair: bias (pull-up)"),
        resistor("R30", "120", "LINK_SCLP", "LINK_SCLM", size="0402", sch=(391.16, 248.92), note="link SCL pair: termination"),
        resistor("R31", "620", "LINK_SCLM", "GND", size="0402", sch=(401.32, 248.92), note="link SCL pair: bias (pull-down)"),
        resistor("R32", "620", "+5V", "LINK_SDAP", size="0402", sch=(411.48, 248.92), note="link SDA pair: bias (pull-up)"),
        resistor("R33", "120", "LINK_SDAP", "LINK_SDAM", size="0402", sch=(421.64, 248.92), note="link SDA pair: termination"),
        resistor("R34", "620", "LINK_SDAM", "GND", size="0402", sch=(431.80, 248.92), note="link SDA pair: bias (pull-down)"),
    ]
    # H5 carries the part of the board that reaches past the Pi's edge.
    for i, ref in enumerate(("H1", "H2", "H3", "H4", "H5")):
        parts.append(Part(ref, "Mechanical:MountingHole", "MountingHole", "MountingHole:MountingHole_2.7mm_M2.5", {},
                          sch=(368.3 + i * 10.16, 185.42), note="M2.5"))
    return parts


PARTS = build_parts()


def nets():
    """net name -> [(ref, pin)], for every net on the board."""
    out = {}
    for p in PARTS:
        for pin, net in p.pins.items():
            out.setdefault(net, []).append((p.ref, pin))
    return out


if __name__ == "__main__":
    # A quick self-check of the netlist, independent of KiCad.
    n = nets()
    for net, members in sorted(n.items()):
        flag = "  <-- only one connection" if len(members) < 2 else ""
        print(f"{net:12} {len(members):2}  {', '.join(f'{r}.{p}' for r, p in members)}{flag}")
    print(f"\n{len(PARTS)} parts, {len(n)} nets")
