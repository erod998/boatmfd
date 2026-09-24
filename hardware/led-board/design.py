"""The LED board, as data: every part, what each of its pins connects to.

This is the single source of truth. schematic.py draws the schematic from it and board.py lays
out and routes the PCB from it, so the two cannot disagree -- and KiCad's own parity check
(`kicad-cli pcb drc --schematic-parity`, run by build.py) proves they don't.

What the board does, and why each part is there, is in ../../LED_BOARD.md. In short:

  * 12 V at up to 20 A comes in on J2 (a 24 A screw terminal for 10 AWG wire), through a 1 mOhm
    shunt that U4 (INA226) measures, so the software can hold the whole board to its 20 A budget.
  * Eight outputs, each with its own standard (ATO/ATC) blade fuse, F1-F8:
      - four RGBW zones, J3-J6, for 5 m of 12 V 5050 RGBW strip each: U5 (PCA9685) makes 16 PWM
        channels, one low-side MOSFET per colour. Zone 1's R, G, B are PCA9685 outputs 0, 1, 2 --
        the software's default BOAT_LED_PWM_CHANNELS;
      - four addressable outputs, J7-J10, for 5 m of 12 V pixel strip each (WS2815 and the like),
        driven by U2 (an RP2040), which runs their effects itself.
  * J1 (RJ45) is the link to the sensor board's J6 over any straight-through Cat5e/Cat6 patch
    cable: the board's I2C bus as differential I2C on two pairs (a PCA9615 at each end). The
    other two pairs are reserved. No ground and no power go down the cable, so the strips'
    current can never return through it or through the Pi. The Pi reaches all three I2C devices
    over it: the PCA9685 (0x40), the INA226 (0x45) and the RP2040 (0x30).
"""
from dataclasses import dataclass, field

TITLE = "Boat MFD LED board"
REVISION = "1.0"
DATE = "2026-09-24"

ZONES = 4
COLOURS = ["R", "G", "B", "W"]
PIXELS = 4

# I2C addresses on the board's bus (7-bit).
ADDR_PCA9685 = 0x40      # all six address pins low: BOAT_PCA9685_ADDR's default
ADDR_INA226 = 0x45       # A1 = A0 = VS
ADDR_RP2040 = 0x30       # set in the RP2040's firmware

# The RP2040's pins, by GPIO number (the firmware uses the same table).
GPIO_SDA, GPIO_SCL = 0, 1                  # I2C0, as a target at ADDR_RP2040
GPIO_PIXEL = {1: 2, 2: 3, 3: 4, 4: 5}      # pixel output n's data, through U7-U10 to J7-J10
GPIO_STATUS = 6                            # the status LED, D8
RP2040_GPIO_PIN = {g: str(g + 2) for g in range(8)}    # GPIO0-7 are QFN pins 2-9


def channel(zone, colour):
    """The PCA9685 output (0-15) for a zone (1-4) and colour: zone 1 R, G, B, W = 0, 1, 2, 3."""
    return 4 * (zone - 1) + COLOURS.index(colour)


# The link: RJ45 pin -> net. The same pins on the sensor board's J6, so a straight-through patch
# cable (T568A or T568B at both ends -- any ordinary one) joins them pair to pair. 1/2 and 3/6 are
# two of the cable's twisted pairs; 4/5 and 7/8 are left unconnected, reserved for later.
LINK_PINS = {"1": "LINK_SCLM", "2": "LINK_SCLP",    # pair: differential I2C clock (PCA9615 DSCLM/DSCLP)
             "3": "LINK_SDAP", "6": "LINK_SDAM"}    # pair: differential I2C data  (PCA9615 DSDAP/DSDAM)
LINK_NETS = sorted(set(LINK_PINS.values()))

# Nets that carry the strips' current, and get wide tracks and extra clearance on the board.
POWER_NETS = (["VIN", "+12V"] + [f"Z{z}_12V" for z in range(1, ZONES + 1)] +
              [f"P{n}_12V" for n in range(1, PIXELS + 1)] +
              [f"Z{z}_{c}" for z in range(1, ZONES + 1) for c in COLOURS])


@dataclass
class Part:
    ref: str
    symbol: str          # KiCad library symbol, lib:name
    value: str
    footprint: str       # KiCad library footprint, lib:name
    pins: dict           # pin number (str) -> net name; pins not listed are no-connects
    mpn: str = ""
    note: str = ""
    extra: dict = field(default_factory=dict)


# The small parts use the project library (footprints.py): KiCad's own footprints with their
# silkscreen moved to the fab layer, since the MOSFET rows and the RP2040's parts are packed tight.
# The RP2040's own capacitors and resistors are 0402, to sit right at its pins.
R_FP = {size: f"led-board:R_{size}_{metric}Metric_NoSilk" for size, metric in (("0402", "1005"), ("0805", "2012"), ("1206", "3216"))}
C_FP = {size: f"led-board:C_{size}_{metric}Metric_NoSilk" for size, metric in (("0402", "1005"), ("0805", "2012"), ("1206", "3216"))}
C_FP["1210"] = "Capacitor_SMD:C_1210_3225Metric"
FUSE_FP = "Fuse:FuseHolder_Blade_ATO_Littelfuse_FLR_178.6165"
FUSE_MPN = "Littelfuse 178.6165.0002 (ATO/ATC holder, 30 A)"


def yageo(value, size):
    code = {"100": "100RL", "47k": "47KL", "10k": "10KL", "4.7k": "4K7L", "2.2k": "2K2L", "620": "620RL",
            "120": "120RL", "330": "330RL", "27": "27RL", "5.1k": "5K1L", "1k": "1KL"}[value]
    return f"RC{size}FR-07{code}"


def resistor(ref, value, a, b, size="0805", **kw):
    return Part(ref, "Device:R", value, R_FP[size], {"1": a, "2": b}, mpn=yageo(value, size), **kw)


def capacitor(ref, value, a, b, size="0805", **kw):
    mpn = {("100n", "0402"): "CL05B104KO5NNNC", ("1u", "0402"): "CL05A105KA5NQNC", ("15p", "0402"): "CL05C150JB5NNNC",
           ("100n", "0805"): "CL21B104KBCNNNC", ("1u", "0805"): "CL21B105KAFNNNE",
           ("10u", "0805"): "CL21A106KAYNNNE", ("15p", "0805"): "CL21C150JBANNNC",
           ("22u", "1206"): "CL31A226KAHNNNE", ("10u", "1210"): "CL32B106KBJNNNE"}[(value, size)]
    return Part(ref, "Device:C", value, C_FP[size], {"1": a, "2": b}, mpn=mpn, **kw)


def build_parts():
    parts = []

    # ---------------- the link from the sensor board ----------------
    # Differential I2C: U1 turns the two twisted pairs back into an ordinary 5 V I2C bus. Each
    # pair is terminated at both ends of the cable by 620 / 120 / 620 ohms (pull-up, across,
    # pull-down): the 120 matches the cable, and the 620s bias the pair so an idle bus reads as
    # idle. (NXP's figures for 5 V; the sensor board has the same network at its end.)
    parts += [
        Part("U1", "Interface:PCA9615DP", "PCA9615DP", "Package_SO:TSSOP-10_3x3mm_P0.5mm",
             {"1": "+5V", "2": "SDA", "3": "+5V", "4": "SCL", "5": "GND",
              "6": "LINK_SCLM", "7": "LINK_SCLP", "8": "LINK_SDAP", "9": "LINK_SDAM", "10": "+5V"},
             mpn="PCA9615DP,118", note="differential I2C from the RJ45, back to plain I2C"),
        capacitor("C1", "100n", "+5V", "GND", note="U1 decoupling"),
        resistor("R33", "620", "+5V", "LINK_SCLP", note="link SCL pair: bias (pull-up)"),
        resistor("R34", "120", "LINK_SCLP", "LINK_SCLM", note="link SCL pair: termination"),
        resistor("R35", "620", "LINK_SCLM", "GND", note="link SCL pair: bias (pull-down)"),
        resistor("R36", "620", "+5V", "LINK_SDAP", note="link SDA pair: bias (pull-up)"),
        resistor("R37", "120", "LINK_SDAP", "LINK_SDAM", note="link SDA pair: termination"),
        resistor("R38", "620", "LINK_SDAM", "GND", note="link SDA pair: bias (pull-down)"),
        resistor("R39", "4.7k", "SDA", "+5V", note="I2C pull-up, 5 V side"),
        resistor("R40", "4.7k", "SCL", "+5V", note="I2C pull-up, 5 V side"),
    ]

    # ---------------- PWM: a PCA9685 at 0x40, 16 outputs ----------------
    # At 5 V, so its outputs drive the MOSFET gates to 5 V (their on-resistance is rated there).
    # OE is tied low: the outputs follow the registers, which are all off at power-up until the
    # Pi sets them.
    pca = {"1": "GND", "2": "GND", "3": "GND", "4": "GND", "5": "GND", "24": "GND", "25": "GND", "23": "GND",
           "14": "GND", "28": "+5V", "26": "SCL", "27": "SDA"}
    led_pins = [str(p) for p in list(range(6, 14)) + list(range(15, 23))]    # LED0-LED15
    for ch, pin in enumerate(led_pins):
        pca[pin] = f"PWM{ch}"
    parts += [
        Part("U5", "Driver_LED:PCA9685PW", "PCA9685PW", "Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm", pca,
             mpn="PCA9685PW,118", note=f"16 PWM channels, I2C address 0x{ADDR_PCA9685:02X}"),
        capacitor("C5", "100n", "+5V", "GND", note="U5 decoupling"),
        capacitor("C6", "1u", "+5V", "GND", note="U5 decoupling"),
    ]

    # ---------------- the 16 channels: a logic-level MOSFET each, switching a colour to ground ----------------
    # Common-anode strips (the shared wire is +12 V, each colour wire is switched to ground), which
    # is nearly every 4- and 5-wire 12 V strip. The BUK9M7R2-40E is an automotive part: 40 V,
    # 7.2 mOhm at a 5 V gate, so a colour of a 5 m strip (2-3 A) costs it well under 0.1 W, and even
    # the zone fuse's 10 A in one channel only 0.7 W. 100 ohm in each gate (slows the edges a
    # little, for the radio's sake), 47k to ground so a gate can't float on while U5 is unpowered.
    for z in range(1, ZONES + 1):
        for c in COLOURS:
            ch = channel(z, c)
            q = ch + 1
            parts += [
                Part(f"Q{q}", "Transistor_FET:BUK9M7R2-40EX", "BUK9M7R2-40E", "Package_TO_SOT_SMD:LFPAK33",
                     {"1": "GND", "2": "GND", "3": "GND", "4": f"G{ch}", "5": f"Z{z}_{c}"},
                     mpn="BUK9M7R2-40EX", note=f"zone {z} {c}: PCA9685 output {ch}"),
                resistor(f"R{q}", "100", f"PWM{ch}", f"G{ch}", note="gate resistor"),
                resistor(f"R{16 + q}", "47k", f"G{ch}", "GND", note="gate pull-down"),
            ]

    # ---------------- the zone outputs and their fuses ----------------
    for z in range(1, ZONES + 1):
        parts += [
            Part(f"J{2 + z}", "Connector_Generic:Conn_01x05", f"ZONE {z}",
                 "Connector_Phoenix_MSTB:PhoenixContact_MSTB_2,5_5-GF-5,08_1x05_P5.08mm_Horizontal_ThreadedFlange",
                 {"1": f"Z{z}_12V", "2": f"Z{z}_R", "3": f"Z{z}_G", "4": f"Z{z}_B", "5": f"Z{z}_W"},
                 mpn="Phoenix Contact 1776537 (MSTB 2,5/ 5-GF-5,08); plug 1778014 (MSTB 2,5/ 5-STF-5,08)",
                 note=f"RGBW strip, common anode: 1 +12V, 2 R, 3 G, 4 B, 5 W (PCA9685 outputs "
                      f"{channel(z, 'R')}-{channel(z, 'W')}); 12 A per pin"),
            Part(f"F{z}", "Device:Fuse", "ATO", FUSE_FP, {"1": "+12V", "2": f"Z{z}_12V"}, mpn=FUSE_MPN,
                 note=f"zone {z}'s fuse: a standard blade (ATO/ATC) fuse sized for the strip's wire -- 10 A "
                      f"for 5 m of RGBW on 16 AWG"),
        ]

    # ---------------- the RP2040, and the four addressable outputs ----------------
    # The RP2040 is an I2C target on the same bus as the PCA9685: the Pi tells it what each
    # output shows (a colour, an effect, a brightness), and its PIO makes the four WS28xx data
    # streams. It runs at 3.3 V, so Q17/Q18 (the usual BSS138 pair) join its pins to the 5 V bus.
    # Its minimum circuit is Raspberry Pi's ("Hardware design with RP2040"): U3 holds the
    # program, Y1 is the 12 MHz clock, J11 (USB-C) loads the firmware -- hold SW1 (BOOTSEL) while
    # pressing SW2 (RESET) and it appears on a computer as a USB drive.
    mcu = {"1": "+3V3", "10": "+3V3", "22": "+3V3", "33": "+3V3", "42": "+3V3", "49": "+3V3",
           "43": "+3V3", "44": "+3V3", "48": "+3V3", "45": "+1V1", "23": "+1V1", "50": "+1V1",
           "19": "GND", "57": "GND", "20": "XIN", "21": "XOUT", "26": "RUN",
           "46": "USB_DM", "47": "USB_DP",
           "51": "QSPI_SD3", "52": "QSPI_SCLK", "53": "QSPI_SD0", "54": "QSPI_SD2", "55": "QSPI_SD1", "56": "QSPI_SS"}
    mcu[RP2040_GPIO_PIN[GPIO_SDA]] = "MCU_SDA"
    mcu[RP2040_GPIO_PIN[GPIO_SCL]] = "MCU_SCL"
    mcu[RP2040_GPIO_PIN[GPIO_STATUS]] = "STATUS"
    for n, g in GPIO_PIXEL.items():
        mcu[RP2040_GPIO_PIN[g]] = f"PIX{n}"
    parts += [
        Part("U2", "MCU_RaspberryPi:RP2040", "RP2040", "Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm_ThermalVias",
             mcu, mpn="RP2040", note=f"runs the addressable outputs; I2C target at 0x{ADDR_RP2040:02X}"),
        Part("U3", "Memory_Flash:W25Q16JVSS", "W25Q16JV", "Package_SO:SOIC-8_5.3x5.3mm_P1.27mm",
             {"1": "QSPI_SS", "2": "QSPI_SD1", "3": "QSPI_SD2", "4": "GND", "5": "QSPI_SD0", "6": "QSPI_SCLK",
              "7": "QSPI_SD3", "8": "+3V3"}, mpn="W25Q16JVSSIQ", note="U2's program (2 MB QSPI flash)"),
        capacitor("C20", "100n", "+3V3", "GND", size="0402", note="U3 decoupling"),
        Part("Y1", "Device:Crystal_GND24", "12MHz", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
             {"1": "XIN", "2": "GND", "3": "XTAL_O", "4": "GND"}, mpn="ABM8-272-T3",
             note="U2's clock: the crystal Raspberry Pi specify, with its 15 pF loads and 1k in XOUT"),
        resistor("R48", "1k", "XOUT", "XTAL_O", size="0402", note="limits the crystal's drive"),
        capacitor("C21", "15p", "XIN", "GND", size="0402", note="crystal load"),
        capacitor("C22", "15p", "XTAL_O", "GND", size="0402", note="crystal load"),
        # Decoupling: 100 nF at the IOVDD and DVDD pins, 1 uF on the core regulator's input and output.
        capacitor("C23", "100n", "+3V3", "GND", size="0402", note="U2 IOVDD (pin 1)"),
        capacitor("C24", "100n", "+3V3", "GND", size="0402", note="U2 IOVDD (pin 10)"),
        capacitor("C25", "100n", "+3V3", "GND", size="0402", note="U2 IOVDD (pin 22)"),
        capacitor("C26", "100n", "+3V3", "GND", size="0402", note="U2 IOVDD (pin 33)"),
        capacitor("C27", "100n", "+3V3", "GND", size="0402", note="U2 IOVDD and ADC_AVDD (pins 42, 43)"),
        capacitor("C28", "100n", "+3V3", "GND", size="0402", note="U2 USB_VDD and IOVDD (pins 48, 49)"),
        capacitor("C29", "1u", "+3V3", "GND", size="0402", note="U2 VREG_VIN (pin 44)"),
        capacitor("C30", "1u", "+1V1", "GND", size="0402", note="U2 VREG_VOUT (pin 45): the 1.1 V core supply"),
        capacitor("C31", "100n", "+1V1", "GND", size="0402", note="U2 DVDD (pin 23)"),
        capacitor("C32", "100n", "+1V1", "GND", size="0402", note="U2 DVDD (pin 50)"),
        resistor("R49", "10k", "RUN", "+3V3", note="RUN pull-up"),
        Part("SW2", "Switch:SW_Push", "RESET", "Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2", {"1": "RUN", "2": "GND"},
             mpn="KMR221GLFS", note="resets U2"),
        resistor("R50", "1k", "QSPI_SS", "BOOTSEL", note="BOOTSEL button to the flash's chip select"),
        Part("SW1", "Switch:SW_Push", "BOOTSEL", "Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2", {"1": "BOOTSEL", "2": "GND"},
             mpn="KMR221GLFS", note="held during reset: U2 starts as a USB drive, to load firmware"),
        # USB, for loading the firmware only: the board runs from its 12 V, so VBUS is not used.
        Part("J11", "Connector:USB_C_Receptacle_USB2.0_16P", "USB",
             "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
             {"A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND", "SH": "GND",
              "A5": "USB_CC1", "B5": "USB_CC2", "A6": "USB_D+", "B6": "USB_D+", "A7": "USB_D-", "B7": "USB_D-"},
             mpn="GCT USB4105-GF-A", note="firmware loading only (VBUS unused: the board must have its 12 V)"),
        resistor("R51", "27", "USB_DP", "USB_D+", size="0402", note="USB series resistor"),
        resistor("R52", "27", "USB_DM", "USB_D-", size="0402", note="USB series resistor"),
        resistor("R53", "5.1k", "USB_CC1", "GND", note="USB-C: identifies the board as a device"),
        resistor("R54", "5.1k", "USB_CC2", "GND", note="USB-C: identifies the board as a device"),
        resistor("R55", "1k", "STATUS", "STATUS_LED", note="status LED"),
        Part("D8", "Device:LED", "yellow", "LED_SMD:LED_0805_2012Metric", {"1": "GND", "2": "STATUS_LED"},
             mpn="LTST-C171YKT", note="the RP2040's status: blinks while its firmware runs"),
        # The I2C level shift: each BSS138's gate at 3.3 V, source on the RP2040's side (pulled up
        # to 3.3 V by R41/R42), drain on the 5 V bus.
        Part("Q17", "Transistor_FET:BSS138", "BSS138", "Package_TO_SOT_SMD:SOT-23",
             {"1": "+3V3", "2": "MCU_SDA", "3": "SDA"}, mpn="BSS138", note="SDA level shift, 3.3 V to 5 V"),
        Part("Q18", "Transistor_FET:BSS138", "BSS138", "Package_TO_SOT_SMD:SOT-23",
             {"1": "+3V3", "2": "MCU_SCL", "3": "SCL"}, mpn="BSS138", note="SCL level shift, 3.3 V to 5 V"),
        resistor("R41", "10k", "MCU_SDA", "+3V3", note="I2C pull-up, RP2040 side"),
        resistor("R42", "10k", "MCU_SCL", "+3V3", note="I2C pull-up, RP2040 side"),
    ]
    # Each addressable output: U7-U10 buffer the RP2040's 3.3 V data to the 5 V a WS28xx strip
    # wants (the AHCT family reads 3.3 V as a high); 330 ohm in series at the connector (1206: it
    # takes the dissipation if the data wire is ever shorted to 12 V) and a clamp to the rails.
    # J7-J10 are 4-way: +12V, DATA, BI (a WS2815's backup data input, which wants ground at the
    # strip's start; a 3-wire strip leaves it empty) and GND.
    for n in range(1, PIXELS + 1):
        parts += [
            Part(f"U{6 + n}", "74xGxx:74AHCT1G125", "74AHCT1G125", "Package_TO_SOT_SMD:SOT-23-5",
                 {"1": "GND", "2": f"PIX{n}", "3": "GND", "4": f"PIX{n}_5V", "5": "+5V"},
                 mpn="SN74AHCT1G125DBVR", note=f"pixel output {n}: 3.3 V data to 5 V"),
            capacitor(f"C{13 + n}", "100n", "+5V", "GND", note=f"U{6 + n} decoupling"),
            resistor(f"R{42 + n}", "330", f"PIX{n}_5V", f"P{n}_DATA", size="1206", note=f"J{6 + n} data series resistor"),
            Part(f"D{2 + n}", "Diode:BAT54S", "BAT54S", "led-board:SOT-23_NoSilk", {"1": "GND", "2": "+5V", "3": f"P{n}_DATA"},
                 mpn="BAT54S,215", note=f"clamps J{6 + n}'s data line between GND and 5 V"),
            Part(f"J{6 + n}", "Connector_Generic:Conn_01x04", f"PIXEL {n}",
                 "Connector_Phoenix_MSTB:PhoenixContact_MSTB_2,5_4-GF-5,08_1x04_P5.08mm_Horizontal_ThreadedFlange",
                 {"1": f"P{n}_12V", "2": f"P{n}_DATA", "3": "GND", "4": "GND"},
                 mpn="Phoenix Contact 1776524 (MSTB 2,5/ 4-GF-5,08); plug 1778001 (MSTB 2,5/ 4-STF-5,08)",
                 note="12 V addressable strip (WS2815...): 1 +12V, 2 DATA, 3 BI (ground), 4 GND; 12 A per pin"),
            Part(f"F{4 + n}", "Device:Fuse", "ATO", FUSE_FP, {"1": "+12V", "2": f"P{n}_12V"}, mpn=FUSE_MPN,
                 note=f"J{6 + n}'s fuse: a standard blade (ATO/ATC) fuse -- 10 A for 5 m of WS2815 on 16 AWG"),
        ]

    # ---------------- 12 V in, and what it's measured with ----------------
    # J2 takes the strips' 12 V and their return, 10 AWG (6 mm2) from a 25 A fuse or breaker at the
    # supply, and the ground to the same ground point as the Pi's supply. D1 clamps surges; wired
    # backwards, it conducts and blows that fuse. R47 is a four-terminal shunt: U4 reads the voltage
    # across its inner (Kelvin) terminals, so the tracks' own resistance doesn't count.
    parts += [
        Part("J2", "Connector_Generic:Conn_01x02", "12V IN",
             "TerminalBlock_MetzConnect:TerminalBlock_MetzConnect_Type703_RT10N02HGLU_1x02_P9.52mm_Horizontal",
             {"1": "VIN", "2": "GND"}, mpn="METZ CONNECT RT10N02HGLU",
             note="the strips' 12 V, 20 A (the terminal is rated 24 A, 10 AWG), from its own 25 A fuse"),
        Part("D1", "Diode:1.5SMCxxA", "SMCJ18A", "Diode_SMD:D_SMC", {"1": "VIN", "2": "GND"},
             mpn="SMCJ18A", note="1500 W surge clamp on the 12 V input (18 V stand-off)"),
        Part("R47", "Device:R_Shunt", "1m", "Resistor_SMD:R_Shunt_Vishay_WSK2512_6332Metric_T2.21mm",
             {"1": "VIN", "2": "ISENSE_P", "3": "ISENSE_N", "4": "+12V"}, mpn="WSK25121L000FEA",
             note="1 mOhm, 1 W, four-terminal: 20 A makes 20 mV and 0.4 W"),
        Part("U4", "Sensor_Energy:INA226", "INA226", "Package_SO:TSSOP-10_3x3mm_P0.5mm",
             {"1": "+5V", "2": "+5V", "4": "SDA", "5": "SCL", "6": "+5V", "7": "GND", "8": "ISENSE_N",
              "9": "ISENSE_N", "10": "ISENSE_P"},
             mpn="INA226AIDGSR", note=f"the board's current and voltage, I2C address 0x{ADDR_INA226:02X}"),
        capacitor("C18", "100n", "+5V", "GND", note="U4 decoupling"),
        Part("C7", "Device:C_Polarized", "100u 35V", "Capacitor_SMD:CP_Elec_8x10", {"1": "+12V", "2": "GND"},
             mpn="EEE-FK1V101P", note="12 V bulk: the PWM's current steps, and damping for the feed wire"),
        capacitor("C8", "10u", "+12V", "GND", size="1210", note="12 V bypass, zone side"),
        capacitor("C9", "10u", "+12V", "GND", size="1210", note="12 V bypass, pixel side"),
    ]

    # ---------------- the logic supplies: 5 V (a buck converter) and 3.3 V ----------------
    # About 100 mA of logic. From up to 14.8 V a linear regulator would burn 1 W, so U6 is a small
    # buck (Diodes' AP63205, fixed 5 V, with its evaluation board's parts), behind D2 against a
    # reversed feed. U11 makes the RP2040's 3.3 V from the 5 V.
    parts += [
        Part("D2", "Diode:SS14", "SS14", "Diode_SMD:D_SMA", {"1": "V12_LOGIC", "2": "+12V"},
             mpn="SS14", note="reverse polarity protection for the logic supply"),
        capacitor("C10", "10u", "V12_LOGIC", "GND", size="1210", note="U6 input (50 V)"),
        capacitor("C11", "100n", "V12_LOGIC", "GND", note="U6 input, high frequency"),
        Part("U6", "Regulator_Switching:AP63205WU", "AP63205", "Package_TO_SOT_SMD:TSOT-23-6",
             {"1": "+5V", "2": "V12_LOGIC", "3": "V12_LOGIC", "4": "GND", "5": "SW5", "6": "BST5"},
             mpn="AP63205WU-7", note="5 V for the logic, 2 A buck (about 100 mA used)"),
        capacitor("C12", "100n", "BST5", "SW5", note="U6 bootstrap"),
        Part("L1", "Device:L", "6.8u", "Inductor_SMD:L_Bourns_SRN6045TA", {"1": "SW5", "2": "+5V"},
             mpn="SRN6045TA-6R8M", note="U6's inductor"),
        capacitor("C13", "22u", "+5V", "GND", size="1206", note="U6 output"),
        capacitor("C19", "22u", "+5V", "GND", size="1206", note="U6 output"),
        Part("U11", "Regulator_Linear:AP2112K-3.3", "AP2112K-3.3", "Package_TO_SOT_SMD:SOT-23-5",
             {"1": "+5V", "2": "GND", "3": "+5V", "5": "+3V3"}, mpn="AP2112K-3.3TRG1",
             note="3.3 V for the RP2040 and its flash"),
        capacitor("C33", "1u", "+5V", "GND", note="U11 input"),
        capacitor("C34", "10u", "+3V3", "GND", note="U11 output, the 3.3 V bulk"),
        resistor("R56", "2.2k", "+5V", "PWR_LED", note="power LED"),
        Part("D7", "Device:LED", "green", "LED_SMD:LED_0805_2012Metric", {"1": "GND", "2": "PWR_LED"},
             mpn="LTST-C171GKT", note="lit when the board has 12 V"),
    ]

    # ---------------- the link connector, and the holes ----------------
    parts.append(Part("J1", "Connector:RJ45", "LINK", "Connector_RJ:RJ45_Amphenol_54602-x08_Horizontal", dict(LINK_PINS),
                      mpn="Amphenol 54602-908LF",
                      note="to the sensor board's J6: a straight-through Cat5e/Cat6 patch cable. NOT Ethernet: "
                           "never plug either end into a network switch or a PoE injector"))
    for ref in ("H1", "H2", "H3", "H4", "H5", "H6"):
        parts.append(Part(ref, "Mechanical:MountingHole", "MountingHole", "MountingHole:MountingHole_3.2mm_M3", {},
                          note="M3"))
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
    refs = [p.ref for p in PARTS]
    assert len(refs) == len(set(refs)), "duplicate references"
    for net, members in sorted(n.items()):
        flag = "  <-- only one connection" if len(members) < 2 else ""
        print(f"{net:12} {len(members):2}  {', '.join(f'{r}.{p}' for r, p in members)}{flag}")
    print(f"\n{len(PARTS)} parts, {len(n)} nets")
