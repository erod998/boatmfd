"""Draws sensor-board.kicad_sch from design.py.

Symbols come from KiCad's own libraries, flattened where a library symbol inherits from another
(the ADS1115 and both diodes do). The two circuits that matter most to a reader -- each input's
divider and clamp, and the tach's optocoupler input -- are drawn as wired circuits with junction
dots. Everything that crosses between blocks (connector pins, the ADC inputs, the Pi header, GND
and +3V3) connects through net labels and power symbols. `kicad-cli sch erc` (build.py) checks it.
"""
import copy
import math
import uuid
from collections import Counter
from pathlib import Path

import design
from sexpr import dump, find, find_all, num, parse, q

SYMBOL_DIR = Path(r"C:\Users\erod9\AppData\Local\Programs\KiCad\10.0\share\kicad\symbols")
PROJECT = "sensor-board"
STUB = 2.54
_NS = uuid.UUID("6b1d6a0e-9d1c-4a5e-8e0b-5e2f0c9a7a11")


def uid(*key):
    """A stable UUID, so regenerating the design does not churn every identifier."""
    return str(uuid.uuid5(_NS, "/".join(str(k) for k in key)))


ROOT = uid("root")

# ---------------------------------------------------------------- library symbols
_libs = {}


def _lib(name):
    if name not in _libs:
        _libs[name] = parse((SYMBOL_DIR / f"{name}.kicad_sym").read_text(encoding="utf-8"))
    return _libs[name]


def _raw_symbol(lib, name):
    for node in find_all(_lib(lib), "symbol"):
        if node[1] == q(name):
            return node
    raise KeyError(f"{lib}:{name}")


def _rename_units(node, old, new):
    for child in node:
        if isinstance(child, list) and child and child[0] == "symbol" and child[1].startswith(q(old)[:-1] + "_"):
            child[1] = q(new)[:-1] + child[1][len(q(old)) - 1:]
    return node


def flat_symbol(lib_id):
    """The library symbol as a schematic's lib_symbols needs it: named lib:name, fully resolved."""
    lib, name = lib_id.split(":")
    node = _raw_symbol(lib, name)
    parent = find(node, "extends")
    if parent is None:
        out = [c for c in node]
        out[1] = q(lib_id)
        return out
    base = flat_symbol(f"{lib}:{parent[1].strip(chr(34))}")
    base_name = parent[1].strip('"')
    # KiCad's flattening: the parent's properties, each overridden by the derived symbol's own
    # value where it defines one, plus any the derived symbol adds. (Dropping the parent's
    # un-overridden ones -- ki_fp_filters, say -- makes KiCad flag the copy as out of date.)
    own = {p[1]: p for p in find_all(node, "property")}
    out = ["symbol", q(lib_id)]
    # Copies, not the parent's own lists: the unit renaming below edits them in place, and two
    # symbols derived from one parent (SMAJ5.0A and SMAJ18A both extend SM6T6V8A) would otherwise
    # both end up with the second one's unit names -- a duplicate KiCad refuses to load.
    for child in copy.deepcopy(base[2:]):
        if isinstance(child, list) and child[0] == "property":
            out.append(own.pop(child[1], child))
        else:
            out.append(child)
    extra = list(own.values())
    insert_at = next((i for i, c in enumerate(out) if isinstance(c, list) and c[0] == "symbol"), len(out))
    for p in reversed(extra):
        out.insert(insert_at, p)
    return _rename_units(out, base_name, name)


def symbol_pins(sym):
    """pin number -> (x, y, angle) in library coordinates (y up), across all units."""
    pins = {}
    for unit in find_all(sym, "symbol"):
        for pin in find_all(unit, "pin"):
            at = find(pin, "at")
            pins[find(pin, "number")[1].strip('"')] = (float(at[1]), float(at[2]), float(at[3]))
    return pins


def lib_property_at(sym, key):
    for p in find_all(sym, "property"):
        if p[1] == q(key):
            at = find(p, "at")
            return float(at[1]), float(at[2])
    return 0.0, 0.0


# ---------------------------------------------------------------- geometry
def on_sheet(x, y, lx, ly, rot):
    """A library point (y up) of a symbol placed at (x, y) rotated `rot` degrees counter-clockwise."""
    dx, dy = lx, -ly
    c, s = round(math.cos(math.radians(rot))), round(math.sin(math.radians(rot)))
    return round(x + dx * c + dy * s, 4), round(y - dx * s + dy * c, 4)


OUTWARD = {0: "left", 90: "down", 180: "right", 270: "up"}   # a pin's angle points into the body
STEP = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}


# ---------------------------------------------------------------- building blocks
def effects(size=1.27, hide=False, justify=None, bold=False):
    font = ["font", ["size", num(size), num(size)]]
    if bold:
        font.append(["bold", "yes"])
    e = ["effects", font]
    if justify:
        e.append(["justify", *justify])
    if hide:
        e.append(["hide", "yes"])
    return e


def prop(key, value, x, y, hide=False, justify=None, angle=0):
    return ["property", q(key), q(value), ["at", num(x), num(y), str(angle)], effects(hide=hide, justify=justify)]


def wire(x1, y1, x2, y2):
    return ["wire", ["pts", ["xy", num(x1), num(y1)], ["xy", num(x2), num(y2)]],
            ["stroke", ["width", "0"], ["type", "default"]], ["uuid", q(uid("wire", x1, y1, x2, y2))]]


def label(net, x, y, direction):
    angle = {"right": 0, "up": 90, "left": 180, "down": 270}[direction]
    justify = ["left", "bottom"] if direction in ("right", "up") else ["right", "bottom"]
    return ["label", q(net), ["at", num(x), num(y), str(angle)], effects(justify=justify),
            ["uuid", q(uid("label", net, x, y))]]


def text(body, x, y, size=1.27, bold=False):
    return ["text", q(body), ["exclude_from_sim", "no"], ["at", num(x), num(y), "0"],
            effects(size=size, bold=bold, justify=["left", "bottom"]), ["uuid", q(uid("text", body[:40], x, y))]]


class Sheet:
    def __init__(self):
        ids = sorted({p.symbol for p in design.PARTS} | {"power:GND", "power:+3V3", "power:+5V", "power:PWR_FLAG"})
        self.syms = {lid: flat_symbol(lid) for lid in ids}
        self.items = []
        self.points = Counter()      # connection points: wire ends and pins, for junction dots
        self.pin_at = {}             # (ref, pin) -> (x, y, outward)
        self.wired = set()           # (ref, pin) connected by hand-drawn wires
        self.npwr = 0

    # -- symbols
    def symbol(self, lib_id, ref, value, x, y, rot=0, fields=(), text_side=None, hide_value=False):
        sym = self.syms[lib_id]
        pins = symbol_pins(sym)
        if text_side == "right":          # small parts: reference and value beside the body
            rpos, vpos, just = (x + 2.54, y - 1.27), (x + 2.54, y + 1.27), ["left"]
        elif text_side == "below":
            rpos, vpos, just = (x, y - 2.54), (x, y + 3.81), None
        elif text_side == "clamp":
            rpos, vpos, just = (x, y - 2.54), (x + 2.54, y + 3.81), ["left"]
        else:
            rpos = on_sheet(x, y, *lib_property_at(sym, "Reference"), rot)
            vpos = on_sheet(x, y, *lib_property_at(sym, "Value"), rot)
            just = None
        hidden_ref = ref.startswith("#")
        fa = 90 if rot in (90, 270) else 0          # keeps the text horizontal on a turned symbol
        if rot == 180 and just == ["left"]:
            just = ["right"]                        # KiCad mirrors the justification of a symbol turned over
        node = ["symbol", ["lib_id", q(lib_id)], ["at", num(x), num(y), str(rot)], ["unit", "1"],
                ["exclude_from_sim", "no"], ["in_bom", "no" if ref.startswith(("#", "H", "JP")) else "yes"],
                ["on_board", "no" if ref.startswith("#") else "yes"], ["dnp", "no"], ["uuid", q(uid("sym", ref))],
                prop("Reference", ref, *rpos, hide=hidden_ref, justify=just, angle=fa),
                prop("Value", value, *vpos, hide=hide_value, justify=just, angle=fa)]
        for key, val in fields:
            node.append(prop(key, val, x, y, hide=True))
        for n in sorted(pins, key=lambda k: (0, int(k)) if k.isdigit() else (1, k)):
            node.append(["pin", q(n), ["uuid", q(uid("pin", ref, n))]])
        node.append(["instances", ["project", q(PROJECT), ["path", q("/" + ROOT), ["reference", q(ref)], ["unit", "1"]]]])
        self.items.append(node)
        for n, (px, py, angle) in pins.items():
            sx, sy = on_sheet(x, y, px, py, rot)
            self.pin_at[(ref, n)] = (sx, sy, OUTWARD[int(angle + rot) % 360])
            self.points[(sx, sy)] += 1

    def power(self, net, x, y, direction):
        """A power symbol whose graphic points `direction` (away from what it connects to)."""
        self.npwr += 1
        lid = {"GND": "power:GND", "+5V": "power:+5V"}.get(net, "power:+3V3")
        natural = "down" if net == "GND" else "up"
        rot = {("down", "down"): 0, ("down", "right"): 90, ("down", "up"): 180, ("down", "left"): 270,
               ("up", "up"): 0, ("up", "left"): 90, ("up", "down"): 180, ("up", "right"): 270}[(natural, direction)]
        dx, dy = STEP[direction]
        reach = 5.8 if direction in ("left", "right") else 4.6
        vx, vy = x + dx * reach, y + dy * reach      # the name just beyond the graphic
        fa = 90 if rot in (90, 270) else 0
        ref = f"#PWR{self.npwr:02d}"
        self.items.append(["symbol", ["lib_id", q(lid)], ["at", num(x), num(y), str(rot)], ["unit", "1"],
                           ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"], ["dnp", "no"],
                           ["uuid", q(uid("sym", ref))], prop("Reference", ref, x, y, hide=True),
                           prop("Value", net, vx, vy, angle=fa), prop("Footprint", "", x, y, hide=True),
                           prop("Datasheet", "", x, y, hide=True), ["pin", q("1"), ["uuid", q(uid("pin", ref, "1"))]],
                           ["instances", ["project", q(PROJECT),
                                          ["path", q("/" + ROOT), ["reference", q(ref)], ["unit", "1"]]]]])
        self.points[(x, y)] += 1

    def flag(self, n, x, y):
        ref = f"#FLG0{n}"
        self.items.append(["symbol", ["lib_id", q("power:PWR_FLAG")], ["at", num(x), num(y), "0"], ["unit", "1"],
                           ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"], ["dnp", "no"],
                           ["uuid", q(uid("sym", ref))], prop("Reference", ref, x, y, hide=True),
                           prop("Value", "PWR_FLAG", x, y - 3.81), prop("Footprint", "", x, y, hide=True),
                           prop("Datasheet", "", x, y, hide=True), ["pin", q("1"), ["uuid", q(uid("pin", ref, "1"))]],
                           ["instances", ["project", q(PROJECT),
                                          ["path", q("/" + ROOT), ["reference", q(ref)], ["unit", "1"]]]]])
        self.points[(x, y)] += 1

    # -- wiring
    def pin(self, ref, number):
        x, y, _ = self.pin_at[(ref, number)]
        return x, y

    def path(self, *pts):
        """A polyline through points; (ref, pin) tuples of strings mean that pin's connection point."""
        xy = []
        for p in pts:
            if isinstance(p[0], str):
                self.wired.add(p)
                xy.append(self.pin(*p))
            else:
                xy.append((round(p[0], 4), round(p[1], 4)))
        for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
            self.items.append(wire(x1, y1, x2, y2))
            self.points[(x1, y1)] += 1
            self.points[(x2, y2)] += 1

    def net_label(self, net, x, y, direction):
        self.items.append(label(net, x, y, direction))

    def stub(self, ref, number, net):
        """The default connection for a pin not drawn by hand: a stub out to a label or power symbol."""
        x, y, out = self.pin_at[(ref, number)]
        dx, dy = STEP[out]
        ex, ey = round(x + dx * STUB, 4), round(y + dy * STUB, 4)
        self.path((x, y), (ex, ey))
        if net in ("GND", "+3V3", "+5V"):
            self.power(net, ex, ey, out)
        else:
            self.net_label(net, ex, ey, out)

    def junctions(self):
        for (x, y), n in self.points.items():
            if n >= 3:
                self.items.append(["junction", ["at", num(x), num(y)], ["diameter", "0"], ["color", "0", "0", "0", "0"],
                                   ["uuid", q(uid("junction", x, y))]])


# ---------------------------------------------------------------- the sheet
def build():
    sh = Sheet()
    parts = {p.ref: p for p in design.PARTS}
    small = {"Device:R", "Device:C"}

    def place(ref, x, y, rot=0, **kw):
        p = parts[ref]
        fields = [("Footprint", p.footprint), ("Datasheet", "~"), ("MPN", p.mpn), ("Note", p.note)]
        side = kw.pop("text_side", "right" if p.symbol in small else None)
        sh.symbol(p.symbol, ref, p.value, x, y, rot, fields, text_side=side, **kw)

    # ---- each input: IN -> R_top -> node (R_bot to GND, clamp to GND/3V3) -> R_ser -> ADC (C to GND)
    for i, (name, pin, adc, adc_pin, _) in enumerate(design.CHANNELS):
        x, y0 = 88.9 + i * 40.64, 66.04
        rt, rb, rs, c, d = f"R{1 + i}", f"R{7 + i}", f"R{13 + i}", f"C{1 + i}", f"D{1 + i}"
        place(rt, x, y0)
        place(rb, x, y0 + 17.78)
        place(d, x + 17.78, y0 + 3.81, text_side="clamp")
        place(rs, x + 17.78, y0 + 17.78)
        place(c, x + 27.94, y0 + 36.83)
        node, a2 = (x, y0 + 8.89), (x + 17.78, y0 + 33.02)
        ax, ay = sh.pin(d, "1")
        sh.path((d, "1"), (ax - 2.54, ay))
        sh.power("GND", ax - 2.54, ay, "up")
        sh.path((rt, "2"), node, (rb, "1"))
        sh.path(node, (d, "3"))
        sh.net_label(f"{name}_DIV", node[0] + 5.08, node[1], "right")
        sh.path((d, "3"), (rs, "1"))
        sh.path((rs, "2"), a2, (c, "1"))
        cx, cy = sh.pin(c, "1")
        sh.path((cx, cy), (cx + 5.08, cy))
        sh.net_label(f"{name}_ADC", cx + 5.08, cy, "right")
        sh.items.append(text(f"{name}: J1.{pin} -> {adc} AIN{adc_pin - 4}", x - 7.62, 50.8, size=1.6, bold=True))

    # ---- tach: three resistors in series, then the LED with the capacitor and diode across it
    tx, ty = 88.9, 182.88
    place("R19", tx, ty)
    place("R20", tx, ty + 10.16)
    place("R21", tx, ty + 20.32)
    y_led, y_gnd = ty + 29.21, ty + 41.91
    place("U3", tx + 43.18, y_led + 2.54)
    place("C11", tx + 12.7, ty + 35.56)
    place("D7", tx + 25.4, ty + 35.56, rot=270, text_side="right")
    sh.path(("R19", "2"), ("R20", "1"))
    sh.path(("R20", "2"), ("R21", "1"))
    for net, ref in (("TACH_A", "R19"), ("TACH_B", "R20")):
        lx, ly = sh.pin(ref, "2")
        sh.net_label(net, lx, ly + 1.27, "right")
    sh.path(("R21", "2"), (tx, y_led), (tx + 12.7, y_led), (tx + 25.4, y_led), ("U3", "1"))
    sh.path((tx + 12.7, y_led), ("C11", "1"))
    sh.path((tx + 25.4, y_led), ("D7", "1"))
    sh.net_label("TACH_LED", tx + 5.08, y_led, "right")
    ux, uy = sh.pin("U3", "2")
    sh.path(("U3", "2"), (ux, y_gnd), (tx + 25.4, y_gnd), (tx + 12.7, y_gnd), (tx + 5.08, y_gnd))
    sh.path(("C11", "2"), (tx + 12.7, y_gnd))
    sh.path(("D7", "2"), (tx + 25.4, y_gnd))
    sh.net_label("TACH_GND", tx + 5.08, y_gnd, "left")
    sh.items.append(text("TACH: Delco EST gray wire -> optocoupler", tx - 7.62, 167.64, size=1.6, bold=True))

    # ---- everything else: placed, and connected by stubs
    others = {"J4": (35.56, 83.82), "J1": (35.56, 157.48), "J2": (35.56, 200.66), "J3": (35.56, 228.6),
              "U1": (355.6, 71.12), "U2": (355.6, 127.0),
              "C7": (386.08, 58.42), "C8": (398.78, 58.42), "C9": (386.08, 114.3), "C10": (398.78, 114.3),
              "R22": (175.26, 190.5), "R23": (190.5, 190.5),
              "D8": (254.0, 210.82), "R25": (269.24, 190.5), "R24": (284.48, 190.5),
              "H1": (340.36, 190.5), "H2": (350.52, 190.5), "H3": (360.68, 190.5), "H4": (370.84, 190.5),
              "H5": (381.0, 190.5)}
    # The NMEA 2000 block, in signal order from the drop cable to the Pi, at the positions design.py gives.
    others.update({ref: parts[ref].sch for ref in
                   ("J5", "D9", "D10", "C16", "U6", "C17", "C18", "D11", "R26", "JP1", "U5", "C15",
                    "U4", "Y1", "C12", "C13", "C14", "R27", "R28", "J6",
                    "J7", "U7", "Q1", "C19", "C20", "C21", "D12") if ref in parts})
    for ref, (x, y) in others.items():
        if ref == "D8":
            place(ref, x, y, text_side="below")
        elif ref.startswith("H"):
            place(ref, x, y, hide_value=True)
        elif ref in ("R22", "R24", "R27", "R28"):
            place(ref, x, y, rot=180)       # pull-ups: turned so +3V3 is at the top
        else:
            place(ref, x, y)
    sh.flag(1, 340.36, 215.9)
    sh.power("+3V3", 340.36, 215.9, "down")
    sh.flag(2, 355.6, 215.9)
    sh.power("GND", 355.6, 215.9, "down")
    # The network side is powered from NET-S, which comes in through a connector and a diode --
    # nothing KiCad recognises as a supply -- so its two rails get flags of their own.
    for n, (net, x) in enumerate((("N2K_12V", 60.96), ("N2K_GND", 45.72)), start=3):
        sh.flag(n, x, 350.52)
        sh.path((x, 350.52), (x, 355.6))
        sh.net_label(net, x, 355.6, "down")
    # The 5 V from J7 is a supply too, before and after the ideal diode.
    sh.flag(5, 530.86, 71.12)
    sh.power("+5V", 530.86, 71.12, "down")
    sh.flag(6, 543.56, 71.12)
    sh.path((543.56, 71.12), (543.56, 76.2))
    sh.net_label("VIN", 543.56, 76.2, "down")

    for part in design.PARTS:
        for number in {k for (r, k) in sh.pin_at if r == part.ref}:
            if (part.ref, number) in sh.wired:
                continue
            net = part.pins.get(number)
            if net is None:
                x, y, _ = sh.pin_at[(part.ref, number)]
                sh.items.append(["no_connect", ["at", num(x), num(y)], ["uuid", q(uid("nc", x, y))]])
            else:
                sh.stub(part.ref, number, net)

    for body, x, y in [("RASPBERRY PI HEADER", 20.32, 50.8), ("HELM: gauge taps and battery", 20.32, 142.24),
                       ("TACH: Delco EST (ignition side)", 20.32, 190.5), ("TEMPERATURE PROBES", 20.32, 218.44),
                       ("CONVERTERS", 342.9, 45.72), ("1-WIRE PROBES", 246.38, 167.64),
                       ("TACH OUTPUT", 167.64, 167.64), ("MOUNTING / POWER FLAGS", 330.2, 167.64),
                       ("LIGHTS: to a separate LED board", 254.0, 228.6),
                       ("5 V IN: from the 12 V -> 5 V converter, to the Pi's 5 V pins", 414.02, 55.88),
                       ("NMEA 2000: network side (isolated, powered by NET-S)", 20.32, 294.64),
                       ("NMEA 2000: Pi side", 213.36, 294.64)]:
        sh.items.append(text(body, x, y, size=2.0, bold=True))
    sh.items.append(text(
        "Each gauge tap: the S (or I) terminal -> 10k in heatshrink AT THE GAUGE -> J1 -> 39k -> node (10k to GND, BAT54S clamp to GND/3V3)\n"
        "-> 1k -> ADC, with 100n at the ADC pin. V_adc = V_tap x 10/59 (0-16 V -> 0-2.7 V). Battery: 47k on board, no remote resistor, x 10/57.\n"
        "Tach, Delco EST coil (BAT +12 V in, TACH out): J2-1 to the gray wire from the coil's TACH terminal, J2-2 to the tach gauge's G. The tach stays connected.\n"
        "TACH is the coil's switched side -- 12 V, ground while the coil charges, a few hundred volts at each spark: three 3.3k 1206 in series share the spike;\n"
        "1N4148W and 10n across the LED. One rising edge per ignition cycle on GPIO13; 2 per revolution on the 3.0 4-cylinder (BOAT_TACH_PPR=2).\n"
        "Keep every TACH_* net (the ignition side) 3 mm from all other copper: see sensor-board.kicad_dru.\n"
        "Software: BOAT_SENSORS=real BOAT_SENDER_WIRING=tap BOAT_TACH_PULL=none BOAT_TACH_GPIO=13 BOAT_OIL_SENDER=true BOAT_TEMP_SENDER=true;\n"
        "dtoverlay=w1-gpio,gpiopin=26. Channel map: fuel 0, trim 1, battery 2, gauge supply 3, oil 4, temp 5 -- see SENSOR_BOARD.md.",
        20.32, 256.54, size=1.5))
    sh.items.append(text(
        "NMEA 2000 (Fusion stereo, depth transducer): J5 takes a drop cable -- 1 shield (not connected), 2 NET-S, 3 NET-C, 4 NET-H, 5 NET-L.\n"
        "U5 isolates the network from the Pi: its network side runs from NET-S through D9 (reverse polarity), D10 (surges) and U6 (5 V), and N2K_GND is\n"
        "the network's 0 V, not the Pi's GND. Keep every N2K_* net 2 mm from all other copper (sensor-board.kicad_dru). About 1 LEN.\n"
        "JP1 (open) adds a 120 ohm terminator for a bench test with no backbone -- never bridge it on the boat: the backbone is terminated at its ends.\n"
        "Pi: dtparam=spi=on  dtoverlay=mcp251xfd,spi0-0,oscillator=40000000,interrupt=25  ->  can0 at 250 kbit/s;  BOAT_CAN=can0.",
        20.32, 381.0, size=1.5))
    sh.items.append(text(
        "J6 carries logic only: the strips' 12 V and current stay on the LED board.\n"
        "PWM strips: PCA9685s on SDA/SCL (not 0x48/0x49, the converters, or 0x70).\n"
        "Addressable strips: DAT1 = GPIO18 (PWM0), DAT2 = GPIO19 (PWM1), 3.3 V: buffer to 5 V there.\n"
        "AUX = GPIO21, spare. 3V3 for the LED board's logic, under 50 mA.",
        254.0, 269.24, size=1.5))
    sh.items.append(text(
        "J7 takes the converter's 5 V (set 5.1-5.2 V) and runs the Pi through header pins 2 and 4; this board's 3V3 comes from the Pi.\n"
        "U7 + Q1 are an ideal diode (~20 mV): J7 wired backwards is blocked, and the Pi's USB-C can't feed back into the converter.\n"
        "D12 clamps spikes. A converter failing with 12 V out is not caught here: use one with output overvoltage protection.",
        414.02, 139.7, size=1.5))
    sh.junctions()

    return ["kicad_sch", ["version", "20250114"], ["generator", q("eeschema")], ["generator_version", q("9.0")],
            ["uuid", q(ROOT)], ["paper", q("A2")],
            ["title_block", ["title", q(design.TITLE)], ["date", q(design.DATE)], ["rev", q(design.REVISION)],
             ["comment", "1", q("Passive taps on the existing analog gauges -- they stay connected and working")],
             ["comment", "2", q("Generated from design.py by schematic.py; edit those, not this file")]],
            ["lib_symbols", *[sh.syms[lid] for lid in sorted(sh.syms)]],
            *sh.items,
            ["sheet_instances", ["path", q("/"), ["page", q("1")]]],
            ["embedded_fonts", "no"]]


def write(path):
    Path(path).write_text(dump(build()) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write(Path(__file__).with_name(f"{PROJECT}.kicad_sch"))
    print("wrote", f"{PROJECT}.kicad_sch")
