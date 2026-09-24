"""Draws led-board.kicad_sch from design.py.

Symbols come from KiCad's own libraries, flattened where a library symbol inherits from another.
The sheet is laid out in blocks -- the link, the PWM, each zone, the RP2040, each addressable
output, the 12 V input and the logic supplies -- and every pin connects through a short stub to
a net label or a power symbol, so each block reads on its own and the net names are the
connections. Where a block's parts go is worked out from their symbols' sizes (the length of
the labels included), so the sheet stays tidy when design.py changes. `kicad-cli sch erc`
(build.py) checks it.
"""
import copy
import math
import uuid
from collections import Counter
from pathlib import Path

import design
from kicadpaths import SYMBOLS as SYMBOL_DIR
from sexpr import dump, find, find_all, num, parse, q

PROJECT = "led-board"
STUB = 2.54
GRID = 2.54
_NS = uuid.UUID("3f0c7a52-1b7e-4d0e-9a51-2c7d8e4b6a13")
POWER_SYMBOLS = {"GND": "power:GND", "+5V": "power:+5V", "+3V3": "power:+3V3", "+12V": "power:+12V",
                 "+1V1": "power:+1V1"}


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
    # un-overridden ones makes KiCad flag the copy as out of date.)
    own = {p[1]: p for p in find_all(node, "property")}
    out = ["symbol", q(lib_id)]
    # Copies, not the parent's own lists: the unit renaming below edits them in place.
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
    """pin number -> (x, y, angle, length) in library coordinates (y up), across all units."""
    pins = {}
    for unit in find_all(sym, "symbol"):
        for pin in find_all(unit, "pin"):
            at = find(pin, "at")
            length = find(pin, "length")
            pins[find(pin, "number")[1].strip('"')] = (float(at[1]), float(at[2]), float(at[3]),
                                                      float(length[1]) if length else 0.0)
    return pins


def symbol_box(sym):
    """The body's extent in library coordinates (y up): its drawing and its pins."""
    xs, ys = [], []
    for unit in find_all(sym, "symbol"):
        for g in unit:
            if not isinstance(g, list) or not g:
                continue
            if g[0] == "rectangle":
                for key in ("start", "end"):
                    p = find(g, key)
                    xs.append(float(p[1]))
                    ys.append(float(p[2]))
            elif g[0] in ("polyline", "bezier"):
                for p in find_all(find(g, "pts"), "xy"):
                    xs.append(float(p[1]))
                    ys.append(float(p[2]))
            elif g[0] == "circle":
                c, r = find(g, "center"), float(find(g, "radius")[1])
                xs += [float(c[1]) - r, float(c[1]) + r]
                ys += [float(c[2]) - r, float(c[2]) + r]
            elif g[0] == "arc":
                for key in ("start", "mid", "end"):
                    p = find(g, key)
                    xs.append(float(p[1]))
                    ys.append(float(p[2]))
    for x, y, _, _ in symbol_pins(sym).values():
        xs.append(x)
        ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


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


def snap(v):
    return round(round(v / GRID) * GRID, 4)


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


def label_length(net):
    """How far a net label or power symbol reaches beyond its stub."""
    return 6.0 if net in POWER_SYMBOLS else 1.2 * len(net) + 1.5


class Sheet:
    def __init__(self):
        ids = sorted({p.symbol for p in design.PARTS} | set(POWER_SYMBOLS.values()) | {"power:PWR_FLAG"})
        self.syms = {lid: flat_symbol(lid) for lid in ids}
        self.items = []
        self.points = Counter()      # connection points: wire ends and pins, for junction dots
        self.pin_at = {}             # (ref, pin) -> (x, y, outward)
        self.npwr = 0

    # -- symbols
    def symbol(self, lib_id, ref, value, x, y, rot=0, fields=(), small=False, hide_value=False):
        sym = self.syms[lib_id]
        pins = symbol_pins(sym)
        fa = 90 if rot in (90, 270) else 0          # keeps the text horizontal on a turned symbol
        if small:                                   # two-pin parts: reference and value beside the body
            if rot in (90, 270):
                rpos, vpos, just = (x, y - 2.54), (x, y + 2.54), None
                fa = 0
            else:
                rpos, vpos, just = (x + 2.54, y - 1.27), (x + 2.54, y + 1.27), ["left"]
        else:
            rpos = on_sheet(x, y, *lib_property_at(sym, "Reference"), rot)
            vpos = on_sheet(x, y, *lib_property_at(sym, "Value"), rot)
            just = None
        if rot == 180 and just == ["left"]:
            just = ["right"]                        # KiCad mirrors the justification of a symbol turned over
        node = ["symbol", ["lib_id", q(lib_id)], ["at", num(x), num(y), str(rot)], ["unit", "1"],
                ["exclude_from_sim", "no"], ["in_bom", "no" if ref.startswith(("#", "H")) else "yes"],
                ["on_board", "no" if ref.startswith("#") else "yes"], ["dnp", "no"], ["uuid", q(uid("sym", ref))],
                prop("Reference", ref, *rpos, justify=just, angle=fa),
                prop("Value", value, *vpos, hide=hide_value, justify=just, angle=fa)]
        for key, val in fields:
            node.append(prop(key, val, x, y, hide=True))
        for n in sorted(pins, key=lambda k: (0, int(k)) if k.isdigit() else (1, k)):
            node.append(["pin", q(n), ["uuid", q(uid("pin", ref, n))]])
        node.append(["instances", ["project", q(PROJECT), ["path", q("/" + ROOT), ["reference", q(ref)], ["unit", "1"]]]])
        self.items.append(node)
        for n, (px, py, angle, _) in pins.items():
            sx, sy = on_sheet(x, y, px, py, rot)
            self.pin_at[(ref, n)] = (sx, sy, OUTWARD[int(angle + rot) % 360])
            self.points[(sx, sy)] += 1

    def power(self, net, x, y, direction):
        """A power symbol whose graphic points `direction` (away from what it connects to)."""
        self.npwr += 1
        lid = POWER_SYMBOLS[net]
        natural = "down" if net == "GND" else "up"
        rot = {("down", "down"): 0, ("down", "right"): 90, ("down", "up"): 180, ("down", "left"): 270,
               ("up", "up"): 0, ("up", "left"): 90, ("up", "down"): 180, ("up", "right"): 270}[(natural, direction)]
        dx, dy = STEP[direction]
        reach = 5.8 if direction in ("left", "right") else 4.6
        vx, vy = x + dx * reach, y + dy * reach      # the name just beyond the graphic
        fa = 90 if rot in (90, 270) else 0
        ref = f"#PWR{self.npwr:03d}"
        self.items.append(["symbol", ["lib_id", q(lid)], ["at", num(x), num(y), str(rot)], ["unit", "1"],
                           ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"], ["dnp", "no"],
                           ["uuid", q(uid("sym", ref))], prop("Reference", ref, x, y, hide=True),
                           prop("Value", net, vx, vy, angle=fa), prop("Footprint", "", x, y, hide=True),
                           prop("Datasheet", "", x, y, hide=True), ["pin", q("1"), ["uuid", q(uid("pin", ref, "1"))]],
                           ["instances", ["project", q(PROJECT),
                                          ["path", q("/" + ROOT), ["reference", q(ref)], ["unit", "1"]]]]])
        self.points[(x, y)] += 1

    def flag(self, n, net, x, y):
        """A PWR_FLAG on a net no pin of the design drives as a supply, joined to it by a label or
        a power symbol below."""
        ref = f"#FLG{n:02d}"
        self.items.append(["symbol", ["lib_id", q("power:PWR_FLAG")], ["at", num(x), num(y), "0"], ["unit", "1"],
                           ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"], ["dnp", "no"],
                           ["uuid", q(uid("sym", ref))], prop("Reference", ref, x, y, hide=True),
                           prop("Value", "PWR_FLAG", x, y - 3.81), prop("Footprint", "", x, y, hide=True),
                           prop("Datasheet", "", x, y, hide=True), ["pin", q("1"), ["uuid", q(uid("pin", ref, "1"))]],
                           ["instances", ["project", q(PROJECT),
                                          ["path", q("/" + ROOT), ["reference", q(ref)], ["unit", "1"]]]]])
        self.points[(x, y)] += 1
        self.path((x, y), (x, y + STUB))
        if net in POWER_SYMBOLS:
            self.power(net, x, y + STUB, "down")
        else:
            self.items.append(label(net, x, y + STUB, "down"))

    # -- wiring
    def path(self, *pts):
        xy = [(round(p[0], 4), round(p[1], 4)) for p in pts]
        for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
            self.items.append(wire(x1, y1, x2, y2))
            self.points[(x1, y1)] += 1
            self.points[(x2, y2)] += 1

    def stub(self, ref, number, net):
        """A pin's connection: a stub out to a net label or power symbol."""
        x, y, out = self.pin_at[(ref, number)]
        dx, dy = STEP[out]
        ex, ey = round(x + dx * STUB, 4), round(y + dy * STUB, 4)
        self.path((x, y), (ex, ey))
        if net in POWER_SYMBOLS:
            self.power(net, ex, ey, out)
        else:
            self.items.append(label(net, ex, ey, out))

    def junctions(self):
        for (x, y), n in self.points.items():
            if n >= 3:
                self.items.append(["junction", ["at", num(x), num(y)], ["diameter", "0"], ["color", "0", "0", "0", "0"],
                                   ["uuid", q(uid("junction", x, y))]])


# ---------------------------------------------------------------- layout
TWO_PIN = {"Device:R", "Device:C", "Device:C_Polarized", "Device:L", "Device:Fuse", "Device:LED", "Diode:SS14",
           "Diode:1.5SMCxxA", "Switch:SW_Push"}
SUPPLY_ORDER = ["+12V", "+5V", "+3V3", "+1V1", "GND"]     # top of a vertical part to bottom


def orientation(part):
    """How to turn a part: two-pin parts stand up (supply at the top, ground at the bottom) when
    either end is a supply rail, and lie down between two signals; everything else as drawn."""
    if part.symbol not in TWO_PIN or len(part.pins) != 2:
        return 0
    a, b = part.pins.get("1"), part.pins.get("2")
    if a in POWER_SYMBOLS or b in POWER_SYMBOLS:
        ra = SUPPLY_ORDER.index(a) if a in SUPPLY_ORDER else 2.5
        rb = SUPPLY_ORDER.index(b) if b in SUPPLY_ORDER else 2.5
        # Device:R/C/L/Fuse draw pin 1 at the top; the LED and diodes draw pin 1 (K) on the left,
        # so they stand up turned the other way.
        horizontal = part.symbol in ("Device:LED", "Diode:SS14", "Diode:1.5SMCxxA", "Switch:SW_Push")
        if horizontal:
            return 270 if ra < rb else 90
        return 0 if ra <= rb else 180
    return 0 if part.symbol in ("Device:LED", "Diode:SS14", "Diode:1.5SMCxxA", "Switch:SW_Push") else 90


def extents(sheet, part, rot):
    """The room a part needs around its origin, labels included: (left, up, right, down)."""
    sym = sheet.syms[part.symbol]
    x0, y0, x1, y1 = symbol_box(sym)
    corners = [on_sheet(0, 0, x, y, rot) for x in (x0, x1) for y in (y0, y1)]
    left = -min(c[0] for c in corners)
    right = max(c[0] for c in corners)
    up = -min(c[1] for c in corners)
    down = max(c[1] for c in corners)
    for n, (px, py, angle, _) in symbol_pins(sym).items():
        sx, sy = on_sheet(0, 0, px, py, rot)
        out = OUTWARD[int(angle + rot) % 360]
        net = part.pins.get(n)
        reach = STUB + (label_length(net) if net else 1.0)
        if out == "left":
            left = max(left, -sx + reach)
        elif out == "right":
            right = max(right, sx + reach)
        elif out == "up":
            up = max(up, -sy + reach)
        else:
            down = max(down, sy + reach)
    # The reference and value beside a small part: to its right, or above and below one lying down.
    if part.symbol in TWO_PIN:
        if rot in (90, 270):
            up, down = max(up, 3.81), max(down, 3.81)
        else:
            right = max(right, 2.54 + 1.0 * max(len(part.ref), len(part.value)) * 1.27)
    return left, up, right, down


BLOCKS = [
    # (title, x, y, width, refs)
    ("LINK: RJ45 to the sensor board's J6, differential I2C (PCA9615)", 20.32, 30.48, 200.0,
     ["J1", "U1", "C1", "R33", "R34", "R35", "R36", "R37", "R38", "R39", "R40"]),
    ("PWM: PCA9685 at 0x40, 16 channels", 20.32, 125.0, 200.0, ["U5", "C5", "C6"]),
    ("12 V IN (20 A) and its current monitor: INA226 at 0x45", 20.32, 225.0, 200.0,
     ["J2", "D1", "R47", "U4", "C18", "C7", "C8", "C9"]),
    ("LOGIC SUPPLIES: 5 V buck, 3.3 V for the RP2040", 20.32, 330.0, 200.0,
     ["D2", "C10", "C11", "U6", "C12", "L1", "C13", "C19", "U11", "C33", "C34", "R56", "D7"]),
    ("RP2040: the addressable outputs, I2C target at 0x30", 250.0, 30.48, 330.0,
     ["U2", "U3", "C20", "Y1", "R48", "C21", "C22", "C23", "C24", "C25", "C26", "C27", "C28", "C29", "C30",
      "C31", "C32", "R49", "SW2", "R50", "SW1", "J11", "R51", "R52", "R53", "R54", "R55", "D8",
      "Q17", "Q18", "R41", "R42"]),
]
ZONE_X, ZONE_Y, ZONE_W = 609.6, 30.48, 210.0   # x on the 1.27 mm grid: the power flags are placed from it directly
PIXEL_X, PIXEL_Y, PIXEL_W = 250.0, 300.0, 330.0


def zone_refs(z):
    refs = [f"J{2 + z}", f"F{z}"]
    for c in design.COLOURS:
        ch = design.channel(z, c)
        refs += [f"Q{ch + 1}", f"R{ch + 1}", f"R{ch + 17}"]
    return refs


def pixel_refs(n):
    return [f"F{4 + n}", f"J{6 + n}", f"U{6 + n}", f"C{13 + n}", f"R{42 + n}", f"D{2 + n}"]


def flow(sheet, refs, x0, y0, width, parts, place):
    """Places parts left to right in rows inside a block; returns the block's bottom edge."""
    x, y, row_h = x0, y0, 0.0
    for ref in refs:
        part = parts[ref]
        rot = orientation(part)
        left, up, right, down = extents(sheet, part, rot)
        w, h = left + right + 5.08, up + down + 5.08
        if x + w > x0 + width and x > x0:
            x, y, row_h = x0, y + row_h, 0.0
        place(ref, snap(x + left), snap(y + up), rot)
        x += w
        row_h = max(row_h, h)
    return y + row_h


# ---------------------------------------------------------------- the sheet
def build():
    sh = Sheet()
    parts = {p.ref: p for p in design.PARTS}

    def place(ref, x, y, rot=0):
        p = parts[ref]
        fields = [("Footprint", p.footprint), ("Datasheet", "~"), ("MPN", p.mpn), ("Note", p.note)]
        sh.symbol(p.symbol, ref, p.value, x, y, rot, fields, small=p.symbol in TWO_PIN,
                  hide_value=ref.startswith("H"))

    bottoms = []
    for title, x, y, width, refs in BLOCKS:
        sh.items.append(text(title, x, y, size=2.0, bold=True))
        bottoms.append(flow(sh, refs, x, y + 5.08, width, parts, place))
    y = ZONE_Y
    for z in range(1, design.ZONES + 1):
        c0 = design.channel(z, "R")
        sh.items.append(text(f"ZONE {z}: RGBW strip, PCA9685 outputs {c0}-{c0 + 3}", ZONE_X, y, size=2.0, bold=True))
        y = flow(sh, zone_refs(z), ZONE_X, y + 5.08, ZONE_W, parts, place) + 7.62
    y = PIXEL_Y
    for n in range(1, design.PIXELS + 1):
        sh.items.append(text(f"PIXEL {n}: 12 V addressable strip, RP2040 GPIO{design.GPIO_PIXEL[n]}", PIXEL_X, y,
                             size=2.0, bold=True))
        y = flow(sh, pixel_refs(n), PIXEL_X, y + 5.08, PIXEL_W, parts, place) + 5.08
    holes = [p.ref for p in design.PARTS if p.ref.startswith("H")]
    sh.items.append(text("MOUNTING HOLES, POWER FLAGS", ZONE_X, 520.0, size=2.0, bold=True))
    for k, ref in enumerate(holes):
        place(ref, ZONE_X + 10.16 * k + 5.08, 530.86)
    # Supplies that no pin of the design drives as one: the input, and the 5 V (a buck
    # converter's output is its inductor, a passive pin).
    for n, net in enumerate(["GND", "+5V", "+12V", "VIN", "V12_LOGIC"], start=1):
        sh.flag(n, net, ZONE_X + 15.24 * (n - 1) + 5.08, 553.72)

    covered = {ref for _, _, _, _, refs in BLOCKS for ref in refs}
    covered |= {r for z in range(1, design.ZONES + 1) for r in zone_refs(z)}
    covered |= {r for n in range(1, design.PIXELS + 1) for r in pixel_refs(n)}
    covered |= set(holes)
    missing = [p.ref for p in design.PARTS if p.ref not in covered]
    assert not missing, f"parts in no block: {missing}"

    for part in design.PARTS:
        for number in {k for (r, k) in sh.pin_at if r == part.ref}:
            net = part.pins.get(number)
            if net is None:
                x, y, _ = sh.pin_at[(part.ref, number)]
                sh.items.append(["no_connect", ["at", num(x), num(y)], ["uuid", q(uid("nc", x, y))]])
            else:
                sh.stub(part.ref, number, net)

    notes = [
        ("The link: any straight-through Cat5e/Cat6 patch cable to the sensor board's J6 -- NOT Ethernet: never plug\n"
         "either end into a switch or a PoE injector. Pairs 1/2 (SCL) and 3/6 (SDA) carry the I2C bus as differential\n"
         "I2C; 4/5 and 7/8 are reserved. Each pair is terminated at both ends, 620/120/620 ohm. No ground in the cable:\n"
         "the strips' current returns on the 12 V input's own ground wire, never through the link or the Pi.\n"
         "The bus: PCA9685 0x40, INA226 0x45, RP2040 0x30. Keep it at 100 kHz.", 20.32, 440.0),
        ("Fuses: F1-F8 are standard blade (ATO/ATC) fuses, one per output, sized for that output's wire -- 10 A for\n"
         "5 m of 12 V strip on 16 AWG. The whole board is 20 A: J2 takes 10 AWG from a 25 A fuse or breaker at the\n"
         "supply, and the software holds the total under 20 A with the INA226's reading (everything at full white\n"
         "would draw about 60 A).", 20.32, 470.0),
        ("Zones: common-anode 12 V RGBW strips; zone z colour c is PCA9685 output 4(z-1) + (R 0, G 1, B 2, W 3).\n"
         "Pixels: 12 V strips (WS2815...); BI is a WS2815's backup data input, grounded here for the strip's first pixel.\n"
         "Firmware: hold BOOTSEL, press RESET, and the RP2040 appears on a computer (USB-C, J11) as a drive: copy\n"
         "the firmware's .uf2 onto it. The board must have its 12 V while it's plugged in: J11's VBUS is unused.",
         20.32, 495.0),
    ]
    for body, x, y in notes:
        sh.items.append(text(body, x, y, size=1.5))
    sh.junctions()

    return ["kicad_sch", ["version", "20250114"], ["generator", q("eeschema")], ["generator_version", q("9.0")],
            ["uuid", q(ROOT)], ["paper", q("A1")],
            ["title_block", ["title", q(design.TITLE)], ["date", q(design.DATE)], ["rev", q(design.REVISION)],
             ["comment", "1", q("Four RGBW zones and four addressable outputs, 20 A, each output fused")],
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
