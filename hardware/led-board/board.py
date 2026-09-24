"""Lays out and routes led-board.kicad_pcb from design.py. Run with KiCad's bundled Python.

    python board.py              under KiCad: writes led-board.kicad_pcb, .kicad_pro, .kicad_dru
    python board.py --dry-run    anywhere with numpy (and matplotlib, for the picture): the same
                                 placement and routing from the footprint files alone, checked
                                 for unrouted nets and overlapping parts, drawn to a PNG

The board, 178 x 94 mm, two layers, 2 oz copper:
  * the four RGBW zone connectors along the top edge, each with its four MOSFETs right behind
    its colour pins; the four addressable outputs along the bottom edge; the 12 V input on the
    right edge, then its surge clamp, its shunt and the current monitor;
  * a 12 V bus across the middle (a top-layer pour), with the eight fuses standing in two rows
    along it -- the zones' above, the addressable outputs' below -- each straight in line with
    its output's +12V pin;
  * the PCA9685 in the middle, just above the bus, its outputs running out to the MOSFETs along
    a channel under their gate resistors; the RP2040 and the link (RJ45, differential I2C) in
    the left-hand column, the pixel data running out along a channel above the bottom outputs;
  * the strips' current on tracks 1.5-6 mm wide on the top layer only (no vias in its path),
    everything else on 0.2 mm tracks at 0.15 mm spacing; ground on both layers everywhere else,
    a stitching via beside every surface-mount ground pad.
Footprints link to their schematic symbols (same UUIDs as schematic.py), so KiCad's
schematic-parity check can compare the two.
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import design
import schematic
import sexpr
from kicadpaths import CLI, FOOTPRINTS, TEMPLATES
from padgeom import Library, rotate
from router import BOTTOM, TOP, Item, Router

try:
    import pcbnew
except ImportError:          # the dry run needs none of it
    pcbnew = None

HERE = Path(__file__).parent
PROJECT = "led-board"
OX, OY = 50.0, 50.0                  # where the board sits on KiCad's page
W, H = 178.0, 94.0
TRACK, VIA_D, VIA_DRILL = 0.2, 0.6, 0.3
CLEAR = 0.15                         # everything else: the RP2040's and USB-C's pins are 0.4-0.5 mm apart
POWER = set(design.POWER_NETS)
# Track widths for the strips' current (IPC-2221, 2 oz outer copper, 20 C rise): 6 mm carries
# about 20 A, 3 mm a 10 A output, 1.5 mm a colour's 3 A. The 12 V bus and the input also have
# pours under their tracks.
WIDE = {"VIN": 4.0, "+12V": 4.0, "+5V": 0.3}   # (5 V and 3.3 V are ~100 mA: thin enough to reach fine-pitch pins)
WIDE.update({f"Z{z}_12V": 3.0 for z in range(1, design.ZONES + 1)})
WIDE.update({f"P{n}_12V": 3.0 for n in range(1, design.PIXELS + 1)})
WIDE.update({f"Z{z}_{c}": 1.5 for z in range(1, design.ZONES + 1) for c in design.COLOURS})
POWER_CLEAR = 0.3
# Nets whose pours join their pads: the 12 V bus and the input (see pours()), and each fuse's
# output side, a pour round its clip's pads.
FUSE_OUT = {f"F{z}": f"Z{z}_12V" for z in range(1, design.ZONES + 1)}
FUSE_OUT.update({f"F{4 + n}": f"P{n}_12V" for n in range(1, design.PIXELS + 1)})
POURED = {"+12V", "VIN"} | set(FUSE_OUT.values())

# ---------------------------------------------------------------- placement (board mm, degrees CCW)
TOP_Y, BOT_Y = 10.5, 83.5            # the connectors' pin rows: their bodies reach the edge
FET_Y = 17.0                         # the zones' MOSFETs, right behind their pins
FUSE_Z_Y, FUSE_P_Y = 47.1, 55.3      # the fuses' +12V clips, either side of the bus
ZONE_CELL, PIXEL_CELL = 36.6, 31.5   # a connector's width, flanges and all
ZONE_PIN1 = {z: 7.0 + ZONE_CELL * z - 8.1 for z in range(1, design.ZONES + 1)}    # x of each +12V pin
PIXEL_PIN1 = {n: 36.7 + PIXEL_CELL * (n - 1) for n in range(1, design.PIXELS + 1)}
MIDDLE_HOLES = [(51.8, 51.2), (120.0, 51.2)]
HOLES = [(3.5, 3.5), (W - 3.5, 3.5), (W - 3.5, H - 3.5), (3.5, H - 3.5)] + MIDDLE_HOLES


def output_place():
    """The outputs. A zone: its connector along the top edge (turned over, so pin 1, its +12V, is
    at the right-hand end), a MOSFET behind each colour pin with its gate resistor and pull-down
    side by side behind it, and its fuse below, in line with pin 1. An addressable output: its
    connector along the bottom edge (pin 1 at the left-hand end), its fuse above, in line with
    pin 1, and the data buffer, series resistor and clamp between them, beside the fuse's track."""
    out = {}
    for z in range(1, design.ZONES + 1):
        a = ZONE_PIN1[z]
        out[f"J{2 + z}"] = (a, TOP_Y, 180)
        out[f"F{z}"] = (a - 0.5, FUSE_Z_Y, 90)           # the zone's clip up, toward the pin
        for k, c in enumerate(design.COLOURS, start=1):
            ch = design.channel(z, c)
            x = a - 5.08 * k
            out[f"Q{ch + 1}"] = (x, FET_Y, 90)               # drain (the tab) up, toward the pin
            out[f"R{ch + 1}"] = (x + 0.975, FET_Y + 4.4, 90)  # gate resistor under the gate: G up, PWM down
            out[f"R{ch + 17}"] = (x - 1.3, FET_Y + 4.4, -90)  # pull-down beside it: G up, GND down
    for n in range(1, design.PIXELS + 1):
        a = PIXEL_PIN1[n]
        out[f"J{6 + n}"] = (a, BOT_Y, 0)
        out[f"F{4 + n}"] = (a + 0.5, FUSE_P_Y, -90)       # the output's clip down, toward the pin
        out[f"R{42 + n}"] = (a + 5.08, BOT_Y - 6.6, -90)  # over pin 2: PIXn_5V up, DATA down
        out[f"D{2 + n}"] = (a + 8.6, BOT_Y - 5.6, 0)      # the clamp beside it
        out[f"U{6 + n}"] = (a + 9.0, BOT_Y - 9.6, 180)    # the buffer, output toward the resistor
        out[f"C{13 + n}"] = (a + 12.8, BOT_Y - 9.6, 90)
    return out


PLACE = output_place()
PLACE.update({
    # The link: the RJ45 on the left edge, its differential-I2C buffer and the pairs' terminations
    # to its right, and the I2C level shift for the RP2040 below them.
    "J1": (14.6, 33.0, -90),
    "U1": (24.6, 37.6, 180), "C1": (24.6, 33.6, 90),
    "R34": (20.8, 42.2, 90), "R33": (23.0, 42.2, 90), "R35": (25.2, 42.2, 90),
    "R37": (20.8, 46.0, 90), "R36": (23.0, 46.0, 90), "R38": (25.2, 46.0, 90),
    "R39": (28.6, 33.6, 90), "R40": (30.8, 33.6, 90),
    "Q18": (26.3, 61.8, 90), "Q17": (29.8, 61.8, 90), "R42": (26.3, 65.6, 90), "R41": (29.8, 65.6, 90),
    # The RP2040, turned so its GPIO0-6 face right (the I2C up to the level shift, the pixel data
    # down to its channel), its USB and flash pins down, its crystal up.
    "U2": (17.0, 62.0, 180),
    "Y1": (19.4, 52.8, 180), "R48": (17.0, 55.6, 90), "C21": (22.6, 51.8, 90), "C22": (15.6, 53.0, 90),
    "C25": (13.4, 55.8, 0), "C31": (13.4, 54.4, 0),
    "C24": (24.0, 60.0, 90), "C23": (23.8, 67.0, 0),
    "C26": (10.2, 61.0, 90), "C27": (10.2, 64.6, 90), "C29": (13.4, 69.0, 90), "C30": (12.2, 69.0, 90),
    "C28": (15.0, 70.8, 0), "C32": (25.6, 70.4, 90),
    "U3": (21.6, 73.8, -90), "C20": (25.6, 77.6, 90),
    "J11": (4.3, 72.0, -90), "R52": (11.4, 72.8, 0), "R51": (11.4, 74.0, 0),
    "R53": (11.6, 76.2, 0), "R54": (11.6, 78.2, 0),
    "R49": (10.8, 57.4, 90), "R50": (21.6, 80.2, 0),
    "SW1": (5.2, 82.2, 0), "SW2": (11.8, 82.2, 0),
    "R55": (29.8, 52.4, 90), "D8": (29.8, 49.0, 90),
    "U11": (26.4, 56.6, 0), "C33": (29.8, 56.6, 90), "C34": (26.4, 53.0, 0),
    # PWM, in the middle just above the bus: LED0-7 down its left side go to zones 1 and 2,
    # LED8-15 up its right side to zones 3 and 4.
    "U5": (91.0, 36.2, 0), "C5": (96.4, 31.2, 90), "C6": (98.6, 31.2, 90),
    # 12 V in on the right edge, its surge clamp below it, the shunt and the current monitor
    # between it and the bus.
    "J2": (W - 9.33, 56.0, 90), "D1": (171.4, 67.8, -90),
    "R47": (156.0, 56.0, 180), "U4": (156.0, 49.4, 0), "C18": (160.4, 49.4, 90),
    # Bulk capacitance along the bus, between the fuses.
    "C7": (82.8, 51.2, 0), "C8": (45.0, 51.2, 90), "C9": (138.4, 51.2, 90),
    # The logic's 5 V, below the input: D2 off the bus, the buck and its parts.
    "D2": (156.2, 66.4, 180), "C10": (158.4, 72.6, 90), "C11": (161.2, 72.6, 90), "U6": (163.0, 76.6, 0),
    "C12": (166.2, 73.8, 0), "L1": (169.4, 79.6, -90), "C13": (164.4, 84.6, 0), "C19": (169.8, 84.8, 0),
    "R56": (160.0, 88.6, 90), "D7": (162.2, 88.6, 90),
})
for k, (hx, hy) in enumerate(HOLES, start=1):
    PLACE[f"H{k}"] = (hx, hy, 0)

# The +12V bus: a top-layer pour across the middle, under the fuses' +12V clips, out to the
# shunt. The input's own pour, from J2 to the shunt.
BUS = [(31.4, 41.6), (151.0, 41.6), (151.0, 53.6), (155.4, 53.6), (155.4, 58.2), (151.0, 58.2),
       (151.0, 61.0), (31.4, 61.0)]
VIN_POUR = [(157.6, 52.4), (W - 1.0, 52.4), (W - 1.0, 67.6), (167.0, 67.6), (167.0, 60.2), (157.6, 60.2)]

# What each connector pin is for, printed beside the pin. Pin order.
PIN_LABELS = {f"J{2 + z}": ["+12V", "R", "G", "B", "W"] for z in range(1, design.ZONES + 1)}
PIN_LABELS.update({f"J{6 + n}": ["+12V", "DATA", "BI", "GND"] for n in range(1, design.PIXELS + 1)})
PIN_LABELS["J2"] = ["+12V", "GND"]

# Routing order: the strips' current first (top layer, no vias), then the fine-pitch parts'
# nets while there's room round them, then the rest, and the long tracks last.
ZONE_NETS = [f"Z{z}_{c}" for z in range(1, design.ZONES + 1) for c in design.COLOURS]
ORDER = (["VIN", "+12V"] + [f"Z{z}_12V" for z in range(1, design.ZONES + 1)] +
         [f"P{n}_12V" for n in range(1, design.PIXELS + 1)] + ZONE_NETS +
         ["ISENSE_P", "ISENSE_N", "V12_LOGIC", "SW5", "BST5", "PWR_LED"] +
         # The RP2040's supplies before its signals: its supply pins sit between signal pins, and
         # need their way out first.
         ["USB_D+", "USB_D-", "+3V3", "+1V1", "USB_DM", "USB_DP", "XIN", "XOUT", "XTAL_O", "USB_CC1", "USB_CC2",
          "QSPI_SS", "QSPI_SCLK", "QSPI_SD0", "QSPI_SD1", "QSPI_SD2", "QSPI_SD3", "RUN", "BOOTSEL",
          "STATUS", "STATUS_LED", "MCU_SDA", "MCU_SCL"] +
         # The SDA pair's pins (3, 6) are in the middle of the RJ45's rows, the SCL pair's (1, 2) at
         # the end: SDA first, or SCL's tracks shut it in.
         ["LINK_SDAP", "LINK_SDAM", "LINK_SCLP", "LINK_SCLM"] +
         # The 5 V before the I2C: U1's enable pin (5 V) sits between its SDA and SCL pins. Then the
         # long runs across the board before the gate tracks fill the channels.
         ["+5V", "SDA", "SCL"] + [f"PIX{n}" for n in range(design.PIXELS, 0, -1)] +
         [f"PIX{n}_5V" for n in range(1, design.PIXELS + 1)] + [f"P{n}_DATA" for n in range(1, design.PIXELS + 1)] +
         [f"G{ch}" for ch in range(16)] + [f"PWM{ch}" for ch in range(16)])
# The RP2040's supplies drop to the bottom layer at its pins: on top, a supply joining two pins
# on one side would run across the signal pins between them and shut them in.
PREFER_BOTTOM = {"+1V1", "+3V3"}
# ...and the long signal runs, the pixel data along the bottom channel and the PWM along the top
# one: the strips' current (top layer only) crosses both channels, and underneath is clear.
PREFER_BOTTOM |= {f"PIX{n}" for n in range(1, design.PIXELS + 1)} | {f"PWM{ch}" for ch in range(16)}
ORDER_FANOUT = "USB_D+"           # the net before which the RP2040 is fanned out


def pcb_net_name(name):
    return name if name in schematic.POWER_SYMBOLS else "/" + name


def clearance(a, b):
    if a is not None and b is not None and a != b and (a in POWER or b in POWER):
        return POWER_CLEAR
    return CLEAR


# ---------------------------------------------------------------- pad geometry
LIB = Library(FOOTPRINTS, {PROJECT: HERE / f"{PROJECT}.pretty"})


def geo_item(pad, net):
    layers = ([TOP] if pad.top else []) + ([BOTTOM] if pad.bottom else [])
    if pad.shape == "circle":
        return Item(net, layers, "circle", x=pad.x, y=pad.y, r=pad.hw)
    return Item(net, layers, "rect", x=pad.x, y=pad.y, hw=pad.hw, hh=pad.hh, corner=pad.corner)


def pcbnew_item(pad, net):
    """The router's picture of a pcbnew pad (as sensor-board/board.py)."""
    pos = pad.GetPosition()
    x, y = pcbnew.ToMM(pos.x) - OX, pcbnew.ToMM(pos.y) - OY
    try:
        size, shape = pad.GetSize(pcbnew.F_Cu), pad.GetShape(pcbnew.F_Cu)
    except TypeError:
        size, shape = pad.GetSize(), pad.GetShape()
    sx, sy = pcbnew.ToMM(size.x), pcbnew.ToMM(size.y)
    if round(pad.GetOrientationDegrees()) % 180 == 90:
        sx, sy = sy, sx
    layers = [l for l, cu in ((TOP, pcbnew.F_Cu), (BOTTOM, pcbnew.B_Cu)) if pad.IsOnLayer(cu)]
    if shape == pcbnew.PAD_SHAPE_CUSTOM:
        bb = pad.GetBoundingBox()
        x0, y0 = pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetY()) - OY
        w, h = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())
        return Item(net, layers, "rect", x=x0 + w / 2, y=y0 + h / 2, hw=w / 2, hh=h / 2, corner=0.0)
    if shape == pcbnew.PAD_SHAPE_CIRCLE:
        return Item(net, layers, "circle", x=x, y=y, r=sx / 2)
    corner = 0.0
    if shape == pcbnew.PAD_SHAPE_OVAL:
        corner = min(sx, sy) / 2
    elif shape == pcbnew.PAD_SHAPE_ROUNDRECT:
        try:
            corner = pcbnew.ToMM(pad.GetRoundRectCornerRadius(pcbnew.F_Cu))
        except TypeError:
            corner = pcbnew.ToMM(pad.GetRoundRectCornerRadius())
    return Item(net, layers, "rect", x=x, y=y, hw=sx / 2, hh=sy / 2, corner=corner)


def same_pad(a, b, tol=0.02):
    """pcbnew's pad and padgeom's agree (the dry run is only worth anything if they do)."""
    ga, gb = a.geo, b.geo
    if a.kind != b.kind or a.layers != b.layers:
        return False
    keys = ("x", "y", "r") if a.kind == "circle" else ("x", "y", "hw", "hh")
    return all(abs(ga[k] - gb[k]) <= tol for k in keys)


# ---------------------------------------------------------------- the board
class Board:
    def __init__(self, dry_run=False):
        self.dry = dry_run or pcbnew is None
        self.router = Router(W, H, clearance, track=TRACK, via_d=VIA_D, margin=0.03)
        self.pad_items = {}          # (ref, pad number) -> [Item] (a fuse clip has two pads per pin)
        self.tree = {}
        self.failed = []
        self.warnings = []
        self.stitched = set()
        self.gnd_vias = {}           # ref -> its ground pads' stitching vias
        self.recording = None        # while fan_out/via_out lay stubs: the list they go in (prune_dangling)
        self.stubs = []
        self.tracks, self.vias = [], []     # (net, a, b, layer, width), (net, x, y): what the dry run draws
        if not self.dry:
            self.board = pcbnew.BOARD()
            self.board.SetCopperLayerCount(2)
            self.nets = {}
            self.fps = {}
            self.unused = unused_pins()

    # ---------------------------------------------------------------- setup
    def net(self, name, raw=False):
        if name not in self.nets:
            ni = pcbnew.NETINFO_ITEM(self.board, name if raw else pcb_net_name(name))
            self.board.Add(ni)
            self.nets[name] = ni
        return self.nets[name]

    def place(self):
        for part in design.PARTS:
            x, y, rot = PLACE[part.ref]
            geo = LIB.pads(part.footprint, x, y, rot)
            if not self.dry:
                self.place_pcbnew(part, x, y, rot, geo)
            for pad in geo:
                if pad.npth:
                    self.router.holes.append((pad.x, pad.y, pad.hw + 0.25))
                    continue
                net = part.pins.get(pad.number) or (None if self.dry else self.unused.get((part.ref, pad.number)))
                self.pad_items.setdefault((part.ref, pad.number), []).append(geo_item(pad, net))
        for items in self.pad_items.values():
            self.router.items.extend(items)
        for hx, hy in HOLES:
            # The middle holes sit in the 12 V bus: a screw head's width of bare board round them.
            self.router.holes.append((hx, hy, 3.5 if (hx, hy) in MIDDLE_HOLES else 3.2 / 2 + 0.5))
        # Nothing runs under the edge connectors' bodies, between their pins and the edge: the
        # strips' wires come in there.
        edge_refs = ([f"J{2 + z}" for z in range(1, design.ZONES + 1)] + [f"J{6 + n}" for n in range(1, design.PIXELS + 1)] +
                     ["J1", "J2", "J11"])
        for ref in edge_refs:
            x0, y0, x1, y1 = self.courtyard(ref)
            pads = [it.geo for (r, _), its in self.pad_items.items() if r == ref for it in its]
            if y0 < 1.0:
                box = (x0, 0.0, x1, min(g["y"] - g.get("hh", g.get("r", 0)) for g in pads) - 0.1)
            elif y1 > H - 1.0:
                box = (x0, max(g["y"] + g.get("hh", g.get("r", 0)) for g in pads) + 0.1, x1, H)
            elif x0 < 1.0:
                box = (0.0, y0, min(g["x"] - g.get("hw", g.get("r", 0)) for g in pads) - 0.1, y1)
            else:
                box = (max(g["x"] + g.get("hw", g.get("r", 0)) for g in pads) + 0.1, y0, W, y1)
            self.router.keepouts.append((*box, lambda n: False))
        # The bus's pour is the 12 V's alone on the top layer: other nets cross it underneath.
        # (Ground may put a stitching via in it, beside a capacitor's ground pad.)
        bx = [x for x, _ in BUS]
        by = [y for _, y in BUS]
        self.router.keepouts.append((min(bx), min(by), 151.0, max(by), lambda n: n in ("+12V", "GND"), (TOP,)))

    def place_pcbnew(self, part, x, y, rot, geo):
        fp = load_fp(part.footprint)
        fp.SetReference(part.ref)
        fp.SetValue(part.value)
        self.board.Add(fp)
        fp.SetPosition(V(x, y))
        fp.SetOrientationDegrees(rot)
        fp.SetPath(pcbnew.KIID_PATH("/" + schematic.uid("sym", part.ref)))
        for key, val in (("MPN", part.mpn), ("Note", part.note)):
            fp.SetField(key, val)
            fp.GetField(key).SetVisible(False)
        fp.Reference().SetLayer(pcbnew.F_Fab)
        # A fuse holder's clip is one piece of metal with four legs, and a push-button's two pin-1
        # (and pin-2) pads are one contact: pads that share a number are joined inside the part.
        if part.ref.startswith(("F", "SW")):
            fp.SetDuplicatePadNumbersAreJumpers(True)
        mine = [geo_item(p, part.pins.get(p.number)) for p in geo if not p.npth]
        for pad in fp.Pads():
            num = pad.GetNumber()
            netname = part.pins.get(num)
            if netname:
                pad.SetNet(self.net(netname))
            elif (part.ref, num) in self.unused:
                pad.SetNet(self.net(self.unused[(part.ref, num)], raw=True))
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                continue
            # Paste-only apertures (the LFPAK33's twelve unnumbered stencil openings) have no copper.
            if not (pad.IsOnLayer(pcbnew.F_Cu) or pad.IsOnLayer(pcbnew.B_Cu)):
                continue
            theirs = pcbnew_item(pad, netname)
            if not any(same_pad(theirs, m) for m in mine):
                sys.exit(f"{part.ref} pad {num}: padgeom and pcbnew disagree ({theirs.geo}) -- fix padgeom.py first")
        self.fps[part.ref] = fp

    def courtyard(self, ref):
        """A part's courtyard as a board rectangle (x0, y0, x1, y1)."""
        part = next(p for p in design.PARTS if p.ref == ref)
        cy = LIB.courtyard(part.footprint)
        x, y, rot = PLACE[ref]
        pts = [rotate(px, py, rot) for px in (cy[0], cy[2]) for py in (cy[1], cy[3])]
        return (x + min(p[0] for p in pts), y + min(p[1] for p in pts), x + max(p[0] for p in pts),
                y + max(p[1] for p in pts))

    def overlaps(self):
        """Pairs of parts whose courtyard rectangles overlap (KiCad's DRC checks the real outlines)."""
        refs = [p.ref for p in design.PARTS]
        boxes = {r: self.courtyard(r) for r in refs}
        out = []
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                A, B = boxes[a], boxes[b]
                if A[0] < B[2] - 1e-6 and B[0] < A[2] - 1e-6 and A[1] < B[3] - 1e-6 and B[1] < A[3] - 1e-6:
                    out.append((a, b))
        return out

    # ---------------------------------------------------------------- routing
    def add_track(self, net, a, b, layer, width):
        self.tracks.append((net, a, b, layer, width))
        self.router.items.append(Item(net, [layer], "seg", x1=a[0], y1=a[1], x2=b[0], y2=b[1], w=width))
        if not self.dry:
            t = pcbnew.PCB_TRACK(self.board)
            t.SetStart(V(*a))
            t.SetEnd(V(*b))
            t.SetWidth(mm(width))
            t.SetLayer(pcbnew.F_Cu if layer == TOP else pcbnew.B_Cu)
            t.SetNet(self.net(net))
            self.board.Add(t)
            if self.recording is not None:
                self.recording.append(t)

    def add_via(self, net, x, y):
        # The net's own via already here, or so close its pad covers this spot (a layer change
        # landing on a supply's via): one is enough, and the tracks ending here still meet it.
        if any(n == net and (x - vx) ** 2 + (y - vy) ** 2 < 0.29 ** 2 for n, vx, vy in self.vias):
            return
        self.vias.append((net, x, y))
        self.router.items.append(Item(net, [TOP, BOTTOM], "circle", x=x, y=y, r=VIA_D / 2))
        if not self.dry:
            v = pcbnew.PCB_VIA(self.board)
            v.SetPosition(V(x, y))
            v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetDrill(mm(VIA_DRILL))
            try:
                v.SetWidth(mm(VIA_D))
            except TypeError:
                v.SetWidth(pcbnew.F_Cu, mm(VIA_D))
            v.SetNet(self.net(net))
            self.board.Add(v)
            if self.recording is not None:
                self.recording.append(v)

    def cells_of(self, items):
        out = []
        for item in items:
            for layer in item.layers:
                ii, jj = self.router.inside(item, layer).nonzero()
                out += [(int(i), int(j), layer) for i, j in zip(ii, jj)]
        return out

    def commit(self, net, path, width):
        segs, vias = self.router.to_geometry(path)
        for a, b, layer in segs:
            if a != b:
                self.add_track(net, a, b, layer, width)
        for x, y in vias:
            self.add_via(net, x, y)
        self.tree.setdefault(net, []).extend(path)

    def route_net(self, net, members=None, every_pad=False):
        """Join the net's pins. every_pad: each pad of a pin that has several, not just one."""
        if members is None:
            members = [m for m in design.nets()[net] if m in self.pad_items]
        if every_pad:
            members = [(m, k) for m in members for k in range(len(self.pad_items[m]))]
        else:
            members = [(m, None) for m in members]
        if len(members) < 2:
            return
        width = WIDE.get(net, TRACK)
        power = net in POWER
        self.router.track = width
        self.router.layer_cost = (3, 1) if net in PREFER_BOTTOM else (1, 3)
        def items(member):
            m, k = member
            return self.pad_items[m] if k is None else [self.pad_items[m][k]]
        tree_cells = list(self.cells_of(items(members[0])))
        todo = list(members[1:])
        while todo:
            tx, ty = self.router.xy(sum(c[0] for c in tree_cells) / len(tree_cells),
                                    sum(c[1] for c in tree_cells) / len(tree_cells))

            def dist(m):
                g = items(m)[0].geo
                return (g["x"] - tx) ** 2 + (g["y"] - ty) ** 2
            todo.sort(key=dist)
            target = todo.pop(0)
            goal_cells = set(self.cells_of(items(target)))
            if set(tree_cells) & goal_cells:
                tree_cells += list(goal_cells)
                continue
            g = items(target)[0].geo
            # The strips' current stays on the top layer: no via in its way.
            path = self.router.search(net, tree_cells, lambda i, j, l: (i, j, l) in goal_cells, (g["x"], g["y"]),
                                      via_ok=not power, allow_layers=(TOP,) if power else (TOP, BOTTOM))
            if path is None:
                self.failed.append((net, target[0]))
                print(f"    could not reach {target[0][0]}.{target[0][1]} on {net}", flush=True)
                continue
            self.commit(net, path, width)
            tree_cells += path + list(goal_cells)

    def fan_out(self, ref, inward_nets, short=0.4, via_ring=2.3, ep=1.4):
        """A straight stub from every connected pin of a quad package, before anything is routed
        round it: outward for the signals, and inward for the supplies, whose vias go in the ring
        between the pins and the exposed pad -- the bottom layer takes the supplies out under the
        chip. With every pin's way claimed, no track can run along a row of pins and shut the ones
        behind it in, and no supply via sits in a signal's way out."""
        x, y, _ = PLACE[ref]
        self.recording = self.stubs
        for (r, num), items in self.pad_items.items():
            item = items[0]
            if r != ref or item.net is None or item.net.startswith("unconnected-") or len(item.layers) != 1:
                continue
            g = item.geo
            dx, dy = g["x"] - x, g["y"] - y
            if item.net == "GND":
                if num == "57":             # the exposed pad itself
                    continue
                if abs(dx) > abs(dy):
                    end = (round(x + ep * (1 if dx > 0 else -1), 2), g["y"])
                else:
                    end = (g["x"], round(y + ep * (1 if dy > 0 else -1), 2))
                self.add_track("GND", (g["x"], g["y"]), end, TOP, TRACK)
                continue
            if abs(dx) > abs(dy):
                tip = x + via_ring * (1 if dx > 0 else -1) if item.net in inward_nets else \
                    g["x"] + (g["hw"] + short) * (1 if dx > 0 else -1)
                end = (round(tip, 2), g["y"])
            else:
                tip = y + via_ring * (1 if dy > 0 else -1) if item.net in inward_nets else \
                    g["y"] + (g["hh"] + short) * (1 if dy > 0 else -1)
                end = (g["x"], round(tip, 2))
            self.add_track(item.net, (g["x"], g["y"]), end, TOP, TRACK)
        self.recording = None

    def via_out(self, ref, nets):
        """A short stub and a via for each of this part's pads on these nets, before anything else
        is routed round it. For the RP2040's supplies: their pins sit between signal pins, and
        once the signals are out there's no room left beside them for a via."""
        self.router.track = TRACK
        self.recording = self.stubs
        done = []
        for (r, num), items in self.pad_items.items():
            item = items[0]
            if r != ref or item.net not in nets or len(item.layers) != 1:
                continue
            g = item.geo
            # Two pins side by side on one rail share a via: they join each other directly.
            if any(n == item.net and abs(x - g["x"]) + abs(y - g["y"]) < 0.5 for n, x, y in done):
                continue
            done.append((item.net, g["x"], g["y"]))
            via_maps = self.router.blocked(item.net, VIA_D / 2, own_smd=True)
            hw, hh = g["hw"], g["hh"]
            cx0, cy0, cx1, cy1 = self.courtyard(ref)
            outside = [False]           # under the part first: the supplies' stubs point there
            spacing = VIA_DRILL + 0.45

            def ok(i, j, layer):
                if layer != TOP or via_maps[TOP][i, j] or via_maps[BOTTOM][i, j]:
                    return False
                x, y = self.router.xy(i, j)
                for n, vx, vy in self.vias:     # clear of every other via, or right on the rail's own
                    d2 = (x - vx) ** 2 + (y - vy) ** 2
                    if d2 < spacing ** 2 and not (n == item.net and d2 < 0.0025):
                        return False
                inside = cx0 < x < cx1 and cy0 < y < cy1
                if inside == outside[0]:
                    return False
                dx, dy = max(abs(x - g["x"]) - hw, 0), max(abs(y - g["y"]) - hh, 0)
                return (dx * dx + dy * dy) ** 0.5 >= VIA_D / 2 + 0.1
            path = self.router.search(item.net, self.cells_of([item]), ok, (g["x"], g["y"]), via_ok=False,
                                      allow_layers=(TOP,))
            if path is None:            # no room under it: outside, then
                outside[0] = True
                path = self.router.search(item.net, self.cells_of([item]), ok, (g["x"], g["y"]), via_ok=False,
                                          allow_layers=(TOP,))
            if path is None:
                self.failed.append((item.net + " via", (r, num)))
                continue
            if len(path) > 1:
                self.commit(item.net, path, TRACK)
            end = path[-1]
            ex, ey = self.router.xy(end[0], end[1])
            if not any(n == item.net and (ex - vx) ** 2 + (ey - vy) ** 2 < 0.0025 for n, vx, vy in self.vias):
                self.add_via(item.net, ex, ey)
        self.recording = None

    def stitch_gnd(self, refs=None):
        """A via beside every surface-mount GND pad (of these parts, or all), into the bottom plane."""
        self.router.track = TRACK
        for (ref, num), items in self.pad_items.items():
            item = items[0]
            if item.net != "GND" or item.layers != {TOP} or (ref, num) in self.stitched:
                continue
            if any(len(it.layers) == 2 for it in items):
                self.stitched.add((ref, num))    # a pad with its own vias (the RP2040's exposed pad)
                continue
            if refs is not None and ref not in refs:
                continue
            self.stitched.add((ref, num))
            g = item.geo
            hw, hh = (g["hw"], g["hh"]) if item.kind == "rect" else (g["r"], g["r"])

            def gap(x, y):
                dx, dy = max(abs(x - g["x"]) - hw, 0), max(abs(y - g["y"]) - hh, 0)
                return (dx * dx + dy * dy) ** 0.5
            # A ground via this close that serves a neighbouring ground pin of the same part (the
            # MOSFETs' three source pins, side by side) serves this pad too: nothing runs between them.
            if any(gap(x, y) < 1.2 for x, y in self.gnd_vias.get(ref, [])):
                continue
            via_maps = self.router.blocked("GND", VIA_D / 2)
            spacing = VIA_DRILL + 0.45          # hole to hole: PCBWay's 0.41 mm, and a little

            def ok(i, j, layer):
                if layer != TOP or via_maps[TOP][i, j] or via_maps[BOTTOM][i, j]:
                    return False
                x, y = self.router.xy(i, j)
                if any((x - vx) ** 2 + (y - vy) ** 2 < spacing ** 2 for _, vx, vy in self.vias):
                    return False
                return gap(x, y) >= VIA_D / 2 + 0.1
            path = self.router.search("GND", self.cells_of([item]), ok, (g["x"], g["y"]), via_ok=False,
                                      allow_layers=(TOP,))
            if path is None:
                # No room for a via (a ground pin between others at 0.65 mm pitch): join it on
                # top to a ground pad of the same part that has one.
                others = [it for (r, n), its in self.pad_items.items() if r == ref and (r, n) in self.stitched
                          and (r, n) != (ref, num) for it in its if it.net == "GND"]
                if not others:              # none on its part (U4's one ground pin): one nearby
                    others = [it for (r, n), its in self.pad_items.items() if (r, n) in self.stitched
                              and (r, n) != (ref, num) for it in its if it.net == "GND" and it.layers == {TOP}
                              and (it.geo["x"] - g["x"]) ** 2 + (it.geo["y"] - g["y"]) ** 2 < 36.0]
                goal = set(self.cells_of(others))
                path = self.router.search("GND", self.cells_of([item]), lambda i, j, l: (i, j, l) in goal,
                                          (g["x"], g["y"]), via_ok=False, allow_layers=(TOP,)) if goal else None
                if path is None:
                    # No room for a via or a join: the pad still has the top ground pour round it,
                    # and KiCad's DRC checks that it reaches it.
                    self.warnings.append(("GND via", (ref, num)))
                else:
                    self.commit("GND", path, TRACK)
                continue
            end = path[-1]
            if len(path) > 1:
                self.commit("GND", path, TRACK)
            vx, vy = self.router.xy(end[0], end[1])
            self.add_via("GND", vx, vy)
            self.gnd_vias.setdefault(ref, []).append((vx, vy))

    def build(self, place_only=False, until=None):
        if not self.dry:
            self.titles()
            self.outline()
        self.place()
        if place_only:
            return self.failed
        self.tie_pads()
        rest = sorted(n for n in design.nets() if n not in ORDER and n != "GND")
        if rest:
            print("  (not in ORDER, routed last:", ", ".join(rest) + ")")
        for net in ORDER + rest:
            if net == "ISENSE_P":         # ground pins the tracks after this would box in: U4 (between its sense pins), D8 (SDA/SCL)
                self.stitch_gnd(refs={"U4", "D8"})
            if net == "LINK_SDAP":        # U1's 5 V enable pin sits between SDA and SCL: its via first
                self.via_out("U1", {"+5V"})
            if net == ORDER_FANOUT:      # the RP2040's pins claim their way out, and its supplies their vias
                self.fan_out("U2", {"+1V1", "+3V3"})
                self.via_out("U2", {"+1V1"})
                self.via_out("U2", {"+3V3"})
            self.route_net(net)
            print(f"  routed {net}", flush=True)
            if net == until:
                return self.failed
        self.join_unused()
        self.stitch_gnd()
        if not self.dry:
            self.prune_dangling()
            self.pours()
            self.silk()
        return self.failed

    def join_unused(self):
        """Unused pins the schematic names together (the USB-C's four VBUS pads: one symbol pin,
        four pads) are one net on the board: joined, or KiCad reports them unconnected."""
        if self.dry:
            return
        groups = {}
        for key, name in self.unused.items():
            if key in self.pad_items:
                groups.setdefault(name, []).append(key)
        for name, keys in groups.items():
            if len(keys) > 1 or any(len(self.pad_items[k]) > 1 for k in keys):
                self.route_net(name, members=keys, every_pad=True)

    def tie_pads(self):
        """A signal pin with several pads (a push-button's two pin-1 pads, 4 mm apart under its
        body): a straight track between them before anything is routed, so each is one pad to the
        router. (The fuses' clips are joined by pours, ground's by the ground pours.)"""
        for (ref, num), items in self.pad_items.items():
            net = items[0].net
            if len(items) < 2 or net is None or net in POURED or net == "GND" or net.startswith("unconnected-"):
                continue
            a = items[0].geo
            for it in items[1:]:
                b = it.geo
                layer = TOP if TOP in it.layers and TOP in items[0].layers else BOTTOM
                self.add_track(net, (a["x"], a["y"]), (b["x"], b["y"]), layer, TRACK)

    def prune_dangling(self):
        """Cut the stubs and vias fan_out and via_out laid before routing that it didn't use -- a
        pin reached another way, a supply via its net never came back to -- round by round, while
        KiCad's own connectivity calls any of them dangling. Only those: a routed track can end at
        the edge of a shaped pad's box, which that test calls dangling though it overlaps the pad."""
        stubs = list(self.stubs)
        while True:
            self.board.BuildConnectivity()
            conn = self.board.GetConnectivity()
            cut = [t for t in stubs if conn.TestTrackEndpointDangling(t, False)]
            if not cut:
                return
            for t in cut:
                self.board.Remove(t)
                stubs.remove(t)
            print(f"  pruned {len(cut)} unused stubs and vias", flush=True)

    # ---------------------------------------------------------------- pcbnew only
    def titles(self):
        tb = pcbnew.TITLE_BLOCK()
        tb.SetTitle(design.TITLE)
        tb.SetRevision(design.REVISION)
        tb.SetDate(design.DATE)
        tb.SetComment(0, "Four RGBW zones and four addressable outputs, 20 A; RJ45 link to the sensor board")
        tb.SetComment(1, "Generated from design.py by board.py; edit those, not this file")
        self.board.SetTitleBlock(tb)
        self.board.GetDesignSettings().SetAuxOrigin(V(0, H))

    def outline(self, r=3.0):
        """The board's edge: a rectangle with 3 mm rounded corners."""
        corners = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]
        ends = []
        for k, (px, py) in enumerate(corners):
            (ax, ay), (bx, by) = corners[k - 1], corners[(k + 1) % 4]
            d1 = ((px - ax) / abs(px - ax + py - ay), (py - ay) / abs(px - ax + py - ay))
            d2 = ((bx - px) / abs(bx - px + by - py), (by - py) / abs(bx - px + by - py))
            a = (px - d1[0] * r, py - d1[1] * r)
            b = (px + d2[0] * r, py + d2[1] * r)
            cx, cy = a[0] + d2[0] * r, a[1] + d2[1] * r
            m = (cx + (px - cx) / 2 ** 0.5, cy + (py - cy) / 2 ** 0.5)
            s = pcbnew.PCB_SHAPE(self.board)
            s.SetShape(pcbnew.SHAPE_T_ARC)
            s.SetArcGeometry(V(*a), V(*m), V(*b))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.board.Add(s)
            ends.append((a, b))
        for k in range(4):
            s = pcbnew.PCB_SHAPE(self.board)
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(V(*ends[k][1]))
            s.SetEnd(V(*ends[(k + 1) % 4][0]))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.board.Add(s)

    def zone(self, net, layer, pts, priority=0, connection=None, spoke=0.5, clearance_mm=None):
        z = pcbnew.ZONE(self.board)
        z.SetLayer(layer)
        z.SetNet(self.net(net))
        ol = z.Outline()
        ol.NewOutline()
        for x, y in pts:
            ol.Append(mm(x + OX), mm(y + OY))
        z.SetMinThickness(mm(0.25))
        z.SetThermalReliefGap(mm(0.4))
        z.SetThermalReliefSpokeWidth(mm(spoke))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL if connection is None else connection)
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        z.SetAssignedPriority(priority)
        if clearance_mm is not None:
            try:
                z.SetLocalClearance(mm(clearance_mm))
            except TypeError:
                pass
        self.board.Add(z)
        return z

    def pours(self):
        inset = 0.3
        whole = [(inset, inset), (W - inset, inset), (W - inset, H - inset), (inset, H - inset)]
        # The 12 V bus on top, solidly joined to its pads (the fuse clips and J2 carry amps);
        # ground everywhere else on top, and on the whole of the bottom. Surface-mount ground pads
        # (the MOSFETs' sources) join the pour solidly too; through-hole ones through wide spokes,
        # so they can still be soldered by hand.
        self.zone("+12V", pcbnew.F_Cu, BUS, priority=1, connection=pcbnew.ZONE_CONNECTION_FULL, clearance_mm=POWER_CLEAR)
        self.zone("VIN", pcbnew.F_Cu, VIN_POUR, priority=1, connection=pcbnew.ZONE_CONNECTION_FULL, clearance_mm=POWER_CLEAR)
        ground_zones = [self.zone("GND", layer, whole, priority=0, connection=pcbnew.ZONE_CONNECTION_THT_THERMAL,
                                  spoke=1.0, clearance_mm=0.3) for layer in (pcbnew.F_Cu, pcbnew.B_Cu)]
        power_areas = [(min(x for x, _ in poly), min(y for _, y in poly), max(x for x, _ in poly), max(y for _, y in poly))
                       for poly in (BUS, VIN_POUR)]
        # The input's ground and the outputs' ground pins carry amps: solidly into the pour.
        for ref, net in FUSE_OUT.items():
            clip = [p for p in self.fps[ref].Pads() if p.GetNumber() == "2"]
            bb = clip[0].GetBoundingBox()
            for p in clip[1:]:
                bb.Merge(p.GetBoundingBox())
            x0, y0 = pcbnew.ToMM(bb.GetLeft()) - OX - 1.0, pcbnew.ToMM(bb.GetTop()) - OY - 1.0
            x1, y1 = pcbnew.ToMM(bb.GetRight()) - OX + 1.0, pcbnew.ToMM(bb.GetBottom()) - OY + 1.0
            power_areas.append((x0, y0, x1, y1))
            for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
                self.zone(net, layer, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], priority=2,
                          connection=pcbnew.ZONE_CONNECTION_FULL, clearance_mm=POWER_CLEAR)
        for ref, num in ([("J2", "2"), ("U2", "57")] +
                         [(f"J{6 + n}", p) for n in range(1, design.PIXELS + 1) for p in ("3", "4")]):
            for pad in self.fps[ref].Pads():
                if pad.GetNumber() == num:
                    pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
        # Bare board round the middle mounting holes, on both layers: they're in the 12 V bus.
        for hx, hy in MIDDLE_HOLES:
            ra = pcbnew.ZONE(self.board)
            ra.SetIsRuleArea(True)
            ra.SetDoNotAllowZoneFills(True)
            ra.SetDoNotAllowTracks(True)
            ra.SetDoNotAllowVias(True)
            ra.SetDoNotAllowPads(False)          # the hole itself is a pad
            ra.SetDoNotAllowFootprints(False)
            ra.SetLayerSet(pcbnew.LSET.AllCuMask())
            ol = ra.Outline()
            ol.NewOutline()
            for k in range(16):
                a = 2 * math.pi * k / 16
                ol.Append(mm(hx + OX + 3.4 * math.cos(a)), mm(hy + OY + 3.4 * math.sin(a)))
            self.board.Add(ra)
        self.board.BuildConnectivity()
        pcbnew.ZONE_FILLER(self.board).Fill(self.board.Zones())
        if self.stitch_islands(ground_zones, power_areas):
            self.board.BuildConnectivity()
            pcbnew.ZONE_FILLER(self.board).Fill(self.board.Zones())
        for _ in range(4):
            if not self.bridge_ground(power_areas):
                break
            self.board.BuildConnectivity()
            pcbnew.ZONE_FILLER(self.board).Fill(self.board.Zones())

    def ground_groups(self):
        """The ground copper after a fill, grouped by what touches what -- the pours' pieces on
        each layer, the pads, the vias, the tracks. Returns (main, others): each group's pad keys
        and via positions, main being the one with the 12 V input's ground (J2.2)."""
        gnd = self.nets["GND"].GetNetCode()
        parent = {}

        def find(a):
            while parent.setdefault(a, a) != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def join(a, c):
            parent[find(a)] = find(c)
        frags = []
        for z in self.board.Zones():
            if z.GetNetCode() != gnd:
                continue
            layer = z.GetLayer()
            polys = z.GetFilledPolysList(layer)
            for k in range(polys.OutlineCount()):
                node = ("zone", layer, k)
                find(node)
                frags.append((node, layer, polys.Outline(k)))

        def touches(ch, pad):
            if ch.PointInside(pad.GetPosition()):
                return True
            # a thermal spoke reaches the pad's edge: points round just inside it
            bb = pad.GetBoundingBox()
            cx, cy = bb.GetCenter().x, bb.GetCenter().y
            rx, ry = bb.GetWidth() / 2 - mm(0.05), bb.GetHeight() / 2 - mm(0.05)
            for k in range(24):
                a = 2 * math.pi * k / 24
                pt = pcbnew.VECTOR2I(int(cx + rx * math.cos(a)), int(cy + ry * math.sin(a)))
                if pad.HitTest(pt) and ch.PointInside(pt):
                    return True
            return False
        pads = []
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetCode() != gnd:
                    continue
                node = ("pad", fp.GetReference(), p.GetNumber())
                find(node)
                pads.append((node, p))
                for fnode, layer, ch in frags:
                    if p.IsOnLayer(layer) and touches(ch, p):
                        join(node, fnode)
        tracks = [t for t in self.board.GetTracks() if t.GetNetCode() == gnd]
        vias = [t for t in tracks if t.Type() == pcbnew.PCB_VIA_T]
        for v in vias:
            node = ("via", v.GetPosition().x, v.GetPosition().y)
            find(node)
            for fnode, layer, ch in frags:
                if ch.PointInside(v.GetPosition()):
                    join(node, fnode)
            for pnode, p in pads:
                if p.HitTest(v.GetPosition()):
                    join(node, pnode)
        for t in tracks:
            if t.Type() == pcbnew.PCB_VIA_T:
                continue
            node = ("track", t.GetStart().x, t.GetStart().y, t.GetEnd().x, t.GetEnd().y)
            find(node)
            for end in (t.GetStart(), t.GetEnd()):
                for fnode, layer, ch in frags:
                    if layer == t.GetLayer() and ch.PointInside(end):
                        join(node, fnode)
                for pnode, p in pads:
                    if p.IsOnLayer(t.GetLayer()) and p.HitTest(end):
                        join(node, pnode)
                for v in vias:
                    if v.GetPosition() == end:
                        join(node, ("via", end.x, end.y))
        groups = {}
        for node in list(parent):
            groups.setdefault(find(node), []).append(node)
        main_root = find(("pad", "J2", "2"))
        out = []
        for root, nodes in groups.items():
            keys = sorted({(n[1], n[2]) for n in nodes if n[0] == "pad"})
            at = [(pcbnew.ToMM(n[1]) - OX, pcbnew.ToMM(n[2]) - OY) for n in nodes if n[0] == "via"]
            out.append((root == main_root, keys, at))
        main = next(g for g in out if g[0])
        return main, [g for g in out if not g[0] and (g[1] or g[2])]

    def bridge_ground(self, power_areas):
        """A ground track from each group of ground copper the tracks have cut off to the main
        ground -- on either layer, clear of the 12 V pours. Returns how many it laid."""
        main, others = self.ground_groups()
        if not others:
            return 0
        via_item = lambda x, y: Item("GND", [TOP, BOTTOM], "circle", x=x, y=y, r=VIA_D / 2)
        goal = set(self.cells_of([it for k in main[1] if k in self.pad_items for it in self.pad_items[k]] +
                                 [via_item(x, y) for x, y in main[2]]))
        # the 12 V pours are the 12 V's alone: ground doesn't cross them -- the bus and the input on
        # top (underneath is ground), the fuses' outputs on both layers
        saved = list(self.router.keepouts)
        for k, (x0, y0, x1, y1) in enumerate(power_areas):
            layers = (TOP,) if k < 2 else (TOP, BOTTOM)
            self.router.keepouts.append((x0 - 0.3, y0 - 0.3, x1 + 0.3, y1 + 0.3, lambda n: n != "GND", layers))
        self.router.track = 0.3
        self.router.layer_cost = (1, 1)
        laid = 0
        for _, keys, at in others:
            items = [it for k in keys if k in self.pad_items for it in self.pad_items[k]] + [via_item(x, y) for x, y in at]
            start = self.cells_of(items)
            if not start:
                continue
            g = items[0].geo
            path = self.router.search("GND", start, lambda i, j, l: (i, j, l) in goal, (g["x"], g["y"]),
                                      via_ok=True, allow_layers=(TOP, BOTTOM))
            if path is None:
                self.warnings.append(("ground cut off, no way to the rest", keys[:4]))
                continue
            self.commit("GND", path, 0.3)
            goal |= set(path)
            laid += 1
        self.router.keepouts = saved
        print(f"  {laid} ground tracks to pieces of ground cut off from the rest", flush=True)
        return laid

    def stitch_islands(self, zones, power_areas):
        """A via into every piece of the ground pours that tracks have cut off, at a spot clear of
        everything else and of the 12 V areas (where the other layer's ground doesn't reach): each
        piece then joins the other layer's ground. Returns how many it added."""
        via_maps = self.router.blocked("GND", VIA_D / 2)
        spacing = VIA_DRILL + 0.45
        margin = VIA_D / 2 + 0.3

        def in_power(x, y):
            return any(x0 - margin < x < x1 + margin and y0 - margin < y < y1 + margin for x0, y0, x1, y1 in power_areas)
        added = 0
        for zone in zones:
            layer = zone.GetLayer()
            polys = zone.GetFilledPolysList(layer)
            for k in range(polys.OutlineCount()):
                chain = polys.Outline(k)
                bb = chain.BBox()
                x0, y0 = pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetY()) - OY
                x1, y1 = x0 + pcbnew.ToMM(bb.GetWidth()), y0 + pcbnew.ToMM(bb.GetHeight())
                if (x1 - x0) < 2 * margin or (y1 - y0) < 2 * margin:
                    continue
                if any(x0 <= vx <= x1 and y0 <= vy <= y1 and chain.PointInside(V(vx, vy)) for n, vx, vy in self.vias
                       if n == "GND"):
                    continue                # it has a ground via already
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                found = None
                i0, j0 = self.router.cell(x0, y0)
                i1, j1 = self.router.cell(x1, y1)
                cells = sorted(((i, j) for i in range(max(i0, 0), min(i1 + 1, self.router.nx))
                                for j in range(max(j0, 0), min(j1 + 1, self.router.ny))),
                               key=lambda c: (self.router.xy(*c)[0] - cx) ** 2 + (self.router.xy(*c)[1] - cy) ** 2)
                for i, j in cells:
                    if via_maps[TOP][i, j] or via_maps[BOTTOM][i, j]:
                        continue
                    x, y = self.router.xy(i, j)
                    if in_power(x, y) or any((x - vx) ** 2 + (y - vy) ** 2 < spacing ** 2 for _, vx, vy in self.vias):
                        continue
                    if all(chain.PointInside(V(x + dx, y + dy)) for dx, dy in
                           ((0, 0), (margin, 0), (-margin, 0), (0, margin), (0, -margin))):
                        found = (x, y)
                        break
                if found:
                    self.add_via("GND", *found)
                    added += 1
        print(f"  {added} ground vias into pieces of pour cut off by tracks", flush=True)
        return added

    def silk(self):
        """The labels. Each goes where it's meant to, or, if that lands on a pad, another label, a
        part's own outline or the board's edge, the nearest clear spot within 3 mm of it."""
        def box(bb, grow):
            return (pcbnew.ToMM(bb.GetLeft()) - OX - grow, pcbnew.ToMM(bb.GetTop()) - OY - grow,
                    pcbnew.ToMM(bb.GetRight()) - OX + grow, pcbnew.ToMM(bb.GetBottom()) - OY + grow)
        blocked = []
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                if p.IsOnLayer(pcbnew.F_Cu) or p.IsOnLayer(pcbnew.F_Mask):
                    blocked.append(box(p.GetBoundingBox(), 0.2))
            for g in fp.GraphicalItems():
                if g.GetLayer() == pcbnew.F_SilkS:
                    blocked.append(box(g.GetBoundingBox(), 0.15))
        placed = []
        steps = sorted(((dx * 0.2, dy * 0.2) for dx in range(-15, 16) for dy in range(-15, 16)
                        if (dx * dx + dy * dy) * 0.04 <= 9.0), key=lambda d: (d[0] ** 2 + d[1] ** 2, abs(d[0])))

        def clear(b):
            if b[0] < 0.6 or b[1] < 0.6 or b[2] > W - 0.6 or b[3] > H - 0.6:
                return False
            return not any(b[0] < o[2] and o[0] < b[2] and b[1] < o[3] and o[1] < b[3] for o in blocked + placed)

        def text(s, x, y, size=1.0, rot=0, just=None, search=True):
            t = pcbnew.PCB_TEXT(self.board)
            t.SetText(s)
            t.SetLayer(pcbnew.F_SilkS)
            t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
            t.SetTextThickness(mm(max(0.15, size * 0.15)))
            t.SetTextAngleDegrees(rot)
            if just == "left":
                t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
            elif just == "right":
                t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_RIGHT)
            for dx, dy in (steps if search else [(0.0, 0.0)]):
                t.SetPosition(V(x + dx, y + dy))
                if clear(box(t.GetBoundingBox(), 0.0)):
                    break
            else:
                t.SetPosition(V(x, y))
                self.warnings.append(("no clear spot for the label", s))
            placed.append(box(t.GetBoundingBox(), 0.15))
            self.board.Add(t)
        # Every output pin labelled, on the board side of its connector, in the gap before the
        # parts behind it.
        for ref, names in PIN_LABELS.items():
            pads = sorted((p for p in self.fps[ref].Pads() if p.GetNumber().isdigit()), key=lambda p: int(p.GetNumber()))
            pts = [(pcbnew.ToMM(p.GetPosition().x) - OX, pcbnew.ToMM(p.GetPosition().y) - OY) for p in pads]
            if abs(pts[0][0] - pts[-1][0]) < 0.1:            # a column on the right edge (J2)
                x0 = self.courtyard(ref)[0]
                for (px, py), name in zip(pts, names):
                    text(name, x0 - 0.4, py, 0.8, just="right")
            else:                                            # a row on the top or bottom edge
                side = 1 if pts[0][1] < H / 2 else -1
                for (px, py), name in zip(pts, names):
                    text(name, px, py + side * 2.6, 0.8)
        # Which connector and which fuse is which.
        for z in range(1, design.ZONES + 1):
            x, y, _ = PLACE[f"J{2 + z}"]
            text(f"ZONE {z}", x - 24.6, y + 2.6, 0.9)
            fx, fy, _ = PLACE[f"F{z}"]
            text(f"F{z}", fx + 5.6, fy - 8.0, 0.9, rot=90)
        for n in range(1, design.PIXELS + 1):
            x, y, _ = PLACE[f"J{6 + n}"]
            text(f"PIXEL {n}", x + 21.4, y - 2.6, 0.9)
            fx, fy, _ = PLACE[f"F{4 + n}"]
            text(f"F{4 + n}", fx + 3.6, fy + 8.0, 0.9, rot=90)
        x0, y0, x1, y1 = self.courtyard("J1")
        text("J1 LINK: sensor board J6", x0 + 0.5, y0 - 1.9, 0.8, just="left")
        text("patch cable - NOT ETHERNET", x0 + 0.5, y0 - 0.7, 0.8, just="left")
        x0, y0, x1, y1 = self.courtyard("J11")
        text("USB: firmware", x0 + 0.3, y1 + 0.8, 0.8, just="left")
        for ref, name in (("SW1", "BOOTSEL"), ("SW2", "RESET")):
            x0, y0, x1, y1 = self.courtyard(ref)
            text(name, (x0 + x1) / 2, y1 + 0.9, 0.8)
        # The rules the labels can't carry, between F6 and F7 below the bus.
        lines = ["FUSES: blade ATO/ATC, each", "sized for its output's wire:", "10 A for 5 m on 16 AWG",
                 "12V IN: 10 AWG, own 25 A fuse", "ZONES: common-anode RGBW",
                 "PIXELS: 12 V WS2815, BI = GND", f"{design.TITLE} r{design.REVISION}"]
        y = 62.6
        for body in lines:
            text(body, 73.0, y, 0.8, just="left", search=False)
            y += 1.4

    def save(self, path):
        pcbnew.SaveBoard(str(path), self.board)

    # ---------------------------------------------------------------- dry run
    def preview(self, path, zoom=None):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle
        zw, zh = (zoom[2] - zoom[0], zoom[3] - zoom[1]) if zoom else (W, H)
        fig, ax = plt.subplots(figsize=(14, 14 * zh / zw))
        ax.add_patch(Rectangle((0, 0), W, H, fill=False, lw=1.5, ec="k"))
        for poly in (BUS, VIN_POUR):
            ax.add_patch(Polygon(poly, closed=True, fc=(1, 0.85, 0.6), ec="orange", lw=0.5, alpha=0.5))
        for net, a, b, layer, w in self.tracks:
            ax.plot([a[0], b[0]], [a[1], b[1]], color="tab:red" if layer == TOP else "tab:blue", lw=w * 7.2 * 0.8,
                    solid_capstyle="round", alpha=0.75 if layer == TOP else 0.6)
        for items in self.pad_items.values():
            for it in items:
                g = it.geo
                col = "goldenrod" if len(it.layers) == 2 else "darkgoldenrod"
                if it.kind == "circle":
                    ax.add_patch(Circle((g["x"], g["y"]), g["r"], fc=col, ec="none"))
                else:
                    ax.add_patch(Rectangle((g["x"] - g["hw"], g["y"] - g["hh"]), 2 * g["hw"], 2 * g["hh"], fc=col,
                                           ec="none"))
        for net, x, y in self.vias:
            ax.add_patch(Circle((x, y), VIA_D / 2, fc="gray", ec="k", lw=0.3))
        for p in design.PARTS:
            x0, y0, x1, y1 = self.courtyard(p.ref)
            ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec="purple", lw=0.4))
            ax.text((x0 + x1) / 2, (y0 + y1) / 2, p.ref, fontsize=5 if zoom is None else 8, ha="center", va="center",
                    color="purple", clip_on=True)
        for hx, hy in HOLES:
            ax.add_patch(Circle((hx, hy), 1.6, fc="white", ec="k"))
        x0, y0, x1, y1 = zoom or (-2, -2, W + 2, H + 2)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y1, y0)
        ax.set_aspect("equal")
        ax.set_title(f"{design.TITLE} r{design.REVISION} -- dry run (red: top, blue: bottom)")
        fig.savefig(path, dpi=110, bbox_inches="tight")


def mm(v):
    return pcbnew.FromMM(v)


def V(x, y):
    return pcbnew.VECTOR2I(mm(x + OX), mm(y + OY))


def load_fp(fpid):
    lib, name = fpid.split(":")
    folder = HERE / f"{lib}.pretty" if lib == PROJECT else FOOTPRINTS / f"{lib}.pretty"
    fp = pcbnew.FootprintLoad(str(folder), name)
    if fp is None:
        raise KeyError(fpid)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def unquote(atom):
    return atom[1:-1].replace('\\"', '"').replace("\\\\", "\\") if atom.startswith('"') else atom


def unused_pins():
    """(ref, pad) -> net name for every pin the schematic leaves unconnected, named as KiCad names
    them ("unconnected-(U4-NC-Pad1)"), from the schematic's exported netlist."""
    out = HERE / f"{PROJECT}.net"
    subprocess.run([str(CLI), "sch", "export", "netlist", "-o", str(out), str(HERE / f"{PROJECT}.kicad_sch")],
                   check=True, capture_output=True)
    found = {}
    tree = sexpr.parse(out.read_text(encoding="utf-8"))
    for net in sexpr.find_all(sexpr.find(tree, "nets"), "net"):
        name = unquote(sexpr.find(net, "name")[1])
        if name.startswith("unconnected-("):
            for node in sexpr.find_all(net, "node"):
                found[(unquote(sexpr.find(node, "ref")[1]), unquote(sexpr.find(node, "pin")[1]))] = name
    out.unlink()
    return found


def write_rules(path):
    Path(path).write_text("""(version 1)

# The strips' current: its nets keep 0.3 mm from everything else.
(rule "strip power clearance"
  (constraint clearance (min 0.3mm))
  (condition "A.NetClass == 'POWER' && B.Net != A.Net"))

# Holes 0.25 mm from copper -- except the USB-C socket's locating pegs from its own ground pads:
# 0.19 mm in GCT's land pattern for the USB4105, which is what the socket is made to fit.
(rule "hole clearance"
  (constraint hole_clearance (min 0.25mm))
  (condition "A.Parent != 'J11' || B.Parent != 'J11'"))
""", encoding="utf-8")


def write_project(path):
    tpl = TEMPLATES / "RaspberryPi-HAT" / "RaspberryPi-HAT.kicad_pro"
    pro = json.loads(tpl.read_text(encoding="utf-8"))
    pro["meta"]["filename"] = Path(path).name
    default = dict(pro["net_settings"]["classes"][0])
    default.update({"name": "Default", "clearance": CLEAR, "track_width": TRACK, "via_diameter": VIA_D, "via_drill": VIA_DRILL})
    power = dict(default, name="POWER", clearance=POWER_CLEAR, track_width=1.5, priority=0)
    pro["net_settings"]["classes"] = [default, power]
    pro["net_settings"]["netclass_patterns"] = [{"netclass": "POWER", "pattern": pcb_net_name(n)} for n in design.POWER_NETS]
    rules = pro["board"]["design_settings"]["rules"]
    # PCBWay's standard 2-layer limits, as the sensor board (see sensor-board/board.py).
    rules.update({"min_clearance": 0.15, "min_track_width": 0.15, "min_via_diameter": 0.6, "min_via_annular_width": 0.15,
                  "min_through_hole_diameter": 0.3, "min_copper_edge_clearance": 0.3, "min_hole_to_hole": 0.41,
                  "min_hole_clearance": 0.15, "min_text_height": 0.8, "min_text_thickness": 0.15})
    pro["sheets"] = [[schematic.ROOT, "Root"]]
    Path(path).write_text(json.dumps(pro, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="place and route without KiCad, and draw a preview")
    ap.add_argument("--place-only", action="store_true", help="with --dry-run: placement only, no routing")
    ap.add_argument("--preview", default=str(HERE / "reports" / f"{PROJECT}-dry-run.png"))
    ap.add_argument("--until", help="with --dry-run: stop after routing this net (to look at a problem)")
    ap.add_argument("--zoom", help="x0,y0,x1,y1: draw only this part of the board (the preview's name gets -zoom)")
    args = ap.parse_args()
    b = Board(dry_run=args.dry_run or args.place_only)
    failed = b.build(place_only=args.place_only, until=args.until)
    clash = b.overlaps()
    if b.dry:
        Path(args.preview).parent.mkdir(parents=True, exist_ok=True)
        b.preview(args.preview)
        print("preview:", args.preview)
        if args.zoom:
            zp = args.preview.replace(".png", "-zoom.png")
            b.preview(zp, tuple(float(v) for v in args.zoom.split(",")))
            print("zoomed:", zp)
        print(f"{len(b.tracks)} track segments, {len(b.vias)} vias")
    else:
        out = HERE / f"{PROJECT}.kicad_pcb"
        b.save(out)
        write_project(HERE / f"{PROJECT}.kicad_pro")
        write_rules(HERE / f"{PROJECT}.kicad_dru")
        print("wrote", out.name)
    if clash:
        print("OVERLAPPING COURTYARDS:", clash)
    if b.warnings:
        print("NO STITCHING VIA (joined by the top pour only):", b.warnings)
    if failed:
        print("UNROUTED:", failed)
    if failed or clash:
        sys.exit(1)


if __name__ == "__main__":
    main()
