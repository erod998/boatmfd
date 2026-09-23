"""The sensor board, as data: every part, what each of its pins connects to, and where it goes.

This is the single source of truth. schematic.py draws the schematic from it and board.py lays
out and routes the PCB from it, so the two cannot disagree -- and KiCad's own parity check
(`kicad-cli pcb drc --schematic-parity`, run by build.py) proves they don't.

What the board does, and why each part is there, is in ../../SENSOR_BOARD.md. The input
channels here must match the software's channel map in app/sensors.py (tap mode):
fuel 0, trim 1, battery 2, gauge supply 3 on the first ADS1115, oil 4 on the second.
"""
from dataclasses import dataclass, field

TITLE = "Boat MFD sensor board"
REVISION = "1.0"
DATE = "2026-09-22"

# ---------------------------------------------------------------- the input channels
# (name, helm-connector pin, ADC, ADC pin, top resistor). ADS1115 pins: AIN0=4, AIN1=5, AIN2=6, AIN3=7.
# The top resistor is 39k for a gauge tap -- a 10k in heatshrink at the gauge end makes 49k -- and
# a single 47k for the battery, which has no remote resistor. All over 10k.
# Listed in board order, top to bottom. Battery comes before the gauge supply so their traces reach
# U1's right-hand pins (AIN2 below AIN3) around the chip without crossing.
CHANNELS = [
    ("FUEL", 1, "U1", 4, "39k"),    # software channel 0
    ("TRIM", 2, "U1", 5, "39k"),    # 1
    ("BATT", 3, "U1", 6, "47k"),    # 2
    ("GAUGE", 4, "U1", 7, "39k"),   # 3: the gauges' own supply (I terminal)
    ("OIL", 5, "U2", 4, "39k"),     # 4
    ("SPARE", 6, "U2", 5, "39k"),   # 5
]

# Pi header: physical pin -> net. Everything not listed is left unconnected.
PI_PINS = {1: "+3V3", 17: "+3V3", 3: "SDA", 5: "SCL",
           33: "TACH_GPIO",   # GPIO13: tach            (BOAT_TACH_GPIO=13)
           37: "OW",          # GPIO26: 1-Wire probes   (dtoverlay=w1-gpio,gpiopin=26)
           6: "GND", 9: "GND", 14: "GND", 20: "GND", 25: "GND", 30: "GND", 34: "GND", 39: "GND"}

# The ignition side of the tach input. Kept 3 mm from everything else (board.py, and the
# custom rule in sensor-board.kicad_dru).
TACH_HV_NETS = ["TACH_IN", "TACH_A", "TACH_B", "TACH_LED", "TACH_GND"]


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
C0805 = "sensor-board:C_0805_2012Metric_NoSilk"


def yageo(value, size):
    code = {"39k": "39KL", "47k": "47KL", "10k": "10KL", "1k": "1KL", "3.3k": "3K3L", "4.7k": "4K7L", "100": "100RL"}[value]
    return f"RC{size}FR-07{code}"


def resistor(ref, value, a, b, size="0805", **kw):
    return Part(ref, "Device:R", value, R1206 if size == "1206" else R0805, {"1": a, "2": b},
                mpn=yageo(value, size), **kw)


def capacitor(ref, value, a, b, **kw):
    mpn = {"100n": "CL21B104KBCNNNC", "1u": "CL21B105KAFNNNE", "10n": "CL21B103KBANNNC"}[value]
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
             mpn="ADS1115IDGSR", sch=(345.44, 124.46), note="I2C 0x49: oil, spare (optional)"),
        capacitor("C7", "100n", "+3V3", "GND", sch=(375.92, 60.96), note="U1 decoupling"),
        capacitor("C8", "1u", "+3V3", "GND", sch=(386.08, 60.96), note="U1 decoupling"),
        capacitor("C9", "100n", "+3V3", "GND", sch=(375.92, 114.3), note="U2 decoupling"),
        capacitor("C10", "1u", "+3V3", "GND", sch=(386.08, 114.3), note="U2 decoupling"),
    ]

    # ---------------- tach: the Delco EST ignition's gray tach wire, through an optocoupler ----------------
    # The newer EST coil (12 V in, tach out) drives the gray tach wire. Whether that turns out to be a
    # switched 12 V signal or the coil primary itself (spikes of a few hundred volts at each spark),
    # this input takes it: ~1 mA from the wire at 12 V, and a spike is shared across three 1206s.
    parts += [
        resistor("R19", "3.3k", "TACH_IN", "TACH_A", size="1206", sch=(99.06, 190.5), note="tach input: 3 in series share the ignition spike"),
        resistor("R20", "3.3k", "TACH_A", "TACH_B", size="1206", sch=(111.76, 190.5), note="tach input: 3 in series share the ignition spike"),
        resistor("R21", "3.3k", "TACH_B", "TACH_LED", size="1206", sch=(124.46, 190.5), note="tach input: 3 in series share the ignition spike"),
        capacitor("C11", "10n", "TACH_LED", "TACH_GND", sch=(139.7, 205.74), note="filters the ring-down after each spark"),
        Part("D7", "Diode:1N4148W", "1N4148W", "Diode_SMD:D_SOD-123", {"1": "TACH_LED", "2": "TACH_GND"},
             mpn="1N4148W-7-F", sch=(154.94, 205.74), note="keeps the ring-down's negative swing off the LED"),
        Part("U3", "Isolator:PC817", "PC817", "Package_DIP:SMDIP-4_W9.53mm",
             {"1": "TACH_LED", "2": "TACH_GND", "3": "GND", "4": "TACH_OUT"},
             mpn="PC817 SMD (gull-wing), CTR rank B or C", sch=(187.96, 200.66),
             note="isolates the Pi from the ignition"),
        resistor("R22", "10k", "TACH_OUT", "+3V3", sch=(213.36, 187.96), note="opto pull-up"),
        resistor("R23", "1k", "TACH_OUT", "TACH_GPIO", sch=(228.6, 203.2), note="protects the GPIO"),
    ]

    # ---------------- 1-Wire temperature probes ----------------
    parts += [
        Part("D8", "Diode:SMAJ5.0A", "SMAJ5.0A", "Diode_SMD:D_SMA", {"1": "OW_EXT", "2": "GND"},
             mpn="SMAJ5.0A", sch=(292.1, 205.74), note="the probe wire runs to the engine bay"),
        resistor("R25", "100", "OW_EXT", "OW", sch=(307.34, 195.58), note="1-Wire series resistor"),
        resistor("R24", "4.7k", "OW", "+3V3", sch=(322.58, 187.96), note="1-Wire pull-up"),
    ]

    # ---------------- connectors ----------------
    helm = {str(pin): f"{name}_IN" for name, pin, _, _, _ in CHANNELS}
    helm.update({"7": "GND", "8": "GND"})
    parts += [
        Part("J1", "Connector_Generic:Conn_01x08", "HELM",
             "Connector_Phoenix_MC:PhoenixContact_MC_1,5_8-GF-3.81_1x08_P3.81mm_Horizontal_ThreadedFlange", helm,
             mpn="Phoenix MC 1,5/ 8-GF-3,81 (plug: MC 1,5/ 8-STF-3,81)", sch=(35.56, 157.48),
             note="1 fuel S, 2 trim S, 3 battery +12V, 4 gauge I, 5 oil S, 6 spare, 7 gauge G, 8 battery -"),
        Part("J2", "Connector_Generic:Conn_01x02", "TACH",
             "Connector_Phoenix_MC:PhoenixContact_MC_1,5_2-GF-3.81_1x02_P3.81mm_Horizontal_ThreadedFlange",
             {"1": "TACH_IN", "2": "TACH_GND"}, mpn="Phoenix MC 1,5/ 2-GF-3,81 (plug: MC 1,5/ 2-STF-3,81)",
             sch=(35.56, 200.66), note="Delco EST: 1 gray tach wire, 2 engine ground"),
        Part("J3", "Connector_Generic:Conn_01x03", "PROBES",
             "Connector_Phoenix_MC:PhoenixContact_MC_1,5_3-GF-3.81_1x03_P3.81mm_Horizontal_ThreadedFlange",
             {"1": "+3V3", "2": "OW_EXT", "3": "GND"}, mpn="Phoenix MC 1,5/ 3-GF-3,81 (plug: MC 1,5/ 3-STF-3,81)",
             sch=(35.56, 228.6), note="DS18B20 probes: 1 red, 2 yellow, 3 black"),
        Part("J4", "Connector_Generic:Conn_02x20_Odd_Even", "RPi GPIO",
             "Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical", {str(k): v for k, v in PI_PINS.items()},
             mpn="2x20 female stacking header, 2.54 mm, extra-tall (e.g. Adafruit 1979)", sch=(35.56, 83.82),
             note="to the Raspberry Pi"),
    ]
    for i, ref in enumerate(("H1", "H2", "H3", "H4")):
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
