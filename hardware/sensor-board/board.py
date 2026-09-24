"""Lays out and routes sensor-board.kicad_pcb from design.py. Run with KiCad's bundled Python.

    python board.py              under KiCad: writes sensor-board.kicad_pcb, .kicad_pro, .kicad_dru
    python board.py --dry-run    anywhere with numpy (and matplotlib, for the picture): the same
                                 placement and routing from the footprint files alone (padgeom.py),
                                 checked for unrouted nets and overlapping parts, drawn to a PNG

Placement is a table below; routing is router.py under the design rules:
  * 0.2 mm tracks and clearance (the ADS1115's 0.5 mm pin pitch needs no finer);
  * 3 mm between the tach's ignition side and everything else, 0.6 mm inside its resistor chain;
  * 2 mm between the NMEA 2000 network's side of the CAN isolator and everything else, with its
    own ground pour, kept 4 mm from the Pi's mounting hole beside it;
  * the top layer preferred, so the bottom stays a ground plane; GND pours on both layers, kept
    out of the ignition side; a stitching via beside every surface-mount GND pad.
Footprints link to their schematic symbols (same UUIDs as schematic.py), so KiCad's
schematic-parity check can compare the two.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import design
import schematic
import sexpr
from kicadpaths import CLI, FOOTPRINTS as FP_DIR, TEMPLATES
from padgeom import Library, rotate
from router import BOTTOM, TOP, Item, Router

try:
    import pcbnew
except ImportError:          # the dry run needs none of it
    pcbnew = None

HERE = Path(__file__).parent
OX, OY = 100.0, 50.0                 # where the board sits on KiCad's page
# The Raspberry Pi HAT outline (KiCad's own HAT template), 20 mm longer on the side away from the
# header: rev 1.0 was the plain 65 x 56 HAT and had no room left for the NMEA 2000 interface.
# The extra 20 mm reaches past the Pi's USB-C / micro-HDMI edge, about 5 mm above those plugs.
W, H, HAT_H = 65.0, 76.0, 56.0
# Rev 1.2 powers the Pi through the header's 5 V pins, which are in its top-left corner, with the
# helm connector and a mounting hole already on either side. So the 5 V input (J7) and its ideal
# diode sit on a tab above the left half of the header, reaching past the Pi's GPIO edge: TAB_W
# wide, TAB_T tall, at negative y in board coordinates.
TAB_W, TAB_T = 35.2, 12.0
# Rev 1.3's link to the LED board is an RJ45 (J6), facing up past the Pi's GPIO edge on a taller
# tab of its own, right of J7's: from TAB_W to the right-hand edge, LINK_T tall. An RJ45 is 13 mm
# tall and its pins go through the board, so nowhere over the Pi would do.
LINK_T = 17.6
TRACK, HV_TRACK, VIA_D, VIA_DRILL = 0.2, 0.25, 0.7, 0.3
HV = set(design.TACH_HV_NETS)
SERIES = {"TACH_IN", "TACH_A", "TACH_B"}   # the nets the ignition spike is divided across
N2K = set(design.N2K_BUS_NETS)
N2K_CLEAR = 2.0
# Room the network side's tracks get beyond its pads, left and right; none upward, where the
# isolator's Pi-side pins are only 3.4 mm away.
N2K_ROUTE_MARGIN = 1.5
# The network side's power is carried on wider tracks.
WIDE = {"N2K_NET_S": 0.4, "N2K_12V": 0.4, "N2K_GND": 0.4, "N2K_5V": 0.4, "VIN": 0.8, "+5V": 0.8}
# The Pi's supply current only flows J7 -> Q1 -> header pins 2/4: those pads get the wide track.
# The ideal diode's sense pins, its caps and the TVS join on thin ones (U7's pins are 0.95 mm apart).
MAIN_PATH = {"VIN": {("J7", "1"), ("Q1", "2")}, "+5V": {("Q1", "3"), ("J4", "2"), ("J4", "4")}}
# ...and the 5 V one goes to the header on the bottom layer: on top it runs along the header's
# outer row and walls off the GND pin beside the 5 V pins (pin 6) from the top pour.
MAIN_BOTTOM = {"+5V"}
THIN = 0.3
HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5), (61.5, 72.5)]

# ---------------------------------------------------------------- placement (board mm, degrees CCW)
LANE_Y = [11.5, 16.5, 21.5, 26.5, 31.5, 36.5]    # one per channel, top to bottom
PLACE = {
    "J4": (8.37, 4.77, -90, "B"),                 # the Pi header, underneath (as KiCad's HAT template)
    "H1": (3.5, 3.5, 0, "F"), "H2": (61.5, 3.5, 0, "F"), "H3": (3.5, 52.5, 0, "F"), "H4": (61.5, 52.5, 0, "F"),
    "H5": (61.5, 72.5, 0, "F"),                   # supports the part past the Pi's edge
    # Helm plug on the left edge (over the Pi's SD-card end): plug faces out, pin 1 at the top.
    "J1": (8.7, 14.665, -90, "F"),
    # Converters and their decoupling.
    "U1": (38.8, 18.0, 0, "F"), "U2": (38.8, 29.0, 0, "F"),
    "C7": (43.6, 17.6, -90, "F"), "C8": (45.7, 17.6, -90, "F"),
    "C9": (43.6, 28.6, -90, "F"), "C10": (45.7, 28.6, -90, "F"),
    # Tach, bottom right, its plug on the bottom edge. The ignition side (left of U3) is kept
    # 3 mm from everything else.
    "J2": (42.0, 67.3, 0, "F"),
    "R19": (36.0, 60.9, 90, "F"), "R20": (38.8, 60.9, -90, "F"), "R21": (41.6, 60.9, 90, "F"),
    "D7": (44.4, 60.9, -90, "F"), "C11": (47.3, 60.9, -90, "F"),
    "U3": (55.5, 61.5, 0, "F"),
    "R22": (63.3, 58.6, 90, "F"), "R23": (63.3, 62.6, 90, "F"),
    # NMEA 2000, bottom left: the drop-cable plug on the bottom edge, the network side in the row
    # above it, the isolator across the boundary (Pi-side pins up, network-side pins down), and
    # the CAN controller above that, where rev 1.0 had its pinout legend.
    "J5": (9.0, 67.3, 0, "F"),
    "D9": (5.5, 58.2, 0, "F"), "D10": (5.5, 62.0, 0, "F"),
    "U6": (13.5, 59.0, 0, "F"), "C16": (17.8, 59.0, 90, "F"),
    "C17": (20.3, 59.0, 90, "F"), "C18": (21.6, 55.2, 0, "F"),
    "D11": (25.0, 61.5, 0, "F"), "R26": (28.5, 58.0, 90, "F"), "JP1": (28.5, 61.8, 90, "F"),
    "U5": (16.5, 52.0, -90, "F"), "C15": (22.0, 49.8, 0, "F"),
    "U4": (20.5, 44.3, 90, "F"), "Y1": (28.5, 45.5, 0, "F"), "C14": (28.5, 42.0, 0, "F"),
    "C12": (13.8, 42.2, 90, "F"), "C13": (13.8, 45.6, 90, "F"),
    "R27": (32.2, 42.0, 90, "F"), "R28": (32.2, 46.0, 90, "F"),
    # Lights: the RJ45 on its tab, facing up, the pairs' terminations beside its pins 1-3 (0402s:
    # the tab is 10 mm wide there), and the differential-I2C buffer below the mounting hole, on
    # the way to GPIO5/6. The bus's pull-ups stay by the header.
    "J6": (51.0, -3.0, 180, "F"),
    "U8": (59.6, 10.6, 90, "F"), "C22": (56.4, 10.6, 90, "F"), "C23": (56.6, 7.3, 0, "F"),
    # Each pair's terminations in a chain down the tab: 5 V, 620, P, 120, M, 620, GND.
    "R32": (56.6, -8.8, -90, "F"), "R33": (56.6, -6.9, -90, "F"), "R34": (56.6, -5.0, -90, "F"),
    "R29": (59.4, -8.8, -90, "F"), "R30": (59.4, -6.9, -90, "F"), "R31": (59.4, -5.0, -90, "F"),
    "R24": (50.8, 15.6, 180, "F"), "R25": (50.8, 18.8, 180, "F"),   # its I2C bus's pull-ups
    # 5 V in, on the tab: the plug faces up, pin 1 (+5V) right above the header's 5 V pins; the
    # ideal diode to its right, then the bulk capacitor and the TVS.
    "J7": (12.6, -3.3, 180, "F"),
    "Q1": (22.6, -2.2, 0, "F"), "U7": (22.7, -7.2, 270, "F"),
    "C19": (26.6, -8.2, 0, "F"), "C20": (26.9, -4.6, 90, "F"),
    "C21": (29.6, -2.6, 90, "F"), "D12": (32.6, -6.0, 90, "F"),
}


def lane(i, x, y, turn=0):
    """Channel i's five parts in a lane from its input at x to its ADC end 14.4 mm along; turn=180
    is the same lane turned round, running right to left."""
    s = -1 if turn else 1
    top, bot, clamp, ser, cap = design.channel_refs(i)
    for ref, dx, dy, rot in ((top, 0.0, 0.0, 0),        # top resistor: IN left, DIV right
                             (bot, 3.8, 1.7, -90),      # divider bottom: DIV up, GND down
                             (clamp, 7.2, -0.94, -90),  # clamp: COM down onto the lane, GND/3V3 up
                             (ser, 11.0, 0.0, 0),       # series: DIV left, ADC right
                             (cap, 14.4, 1.7, -90)):    # filter: ADC up, GND down
        PLACE[ref] = (x + s * dx, y + s * dy, rot + turn, "F")


for i, y in enumerate(LANE_Y):
    lane(i, 14.8, y)
# The house battery (rev 1.3) has no room in that column, where the CAN controller is: its lane
# runs right to left beside U2, whose AIN2 it feeds, and its input crosses the board from J1.8.
lane(6, 62.3, 33.4, turn=180)
# C22's GND pad is boxed in by U8's 3.3 V and a link pair, with room for one thermal spoke: it
# joins GND through its own via only (stitch_gnd), not the top pour.
NO_POUR_JOIN = {("C22", "2")}

# What each connector pin is for, printed beside the pin (silk()). Pin order.
PIN_LABELS = {
    "J1": ["FUEL", "TRIM", "ENG+", "IGN", "OIL", "TEMP", "GND", "HSE+"],
    "J2": ["TACH", "GND"],
    "J5": ["BARE", "RED", "BLK", "WHT", "BLU"],     # the drop cable's wire colours
    "J7": ["+5V", "GND"],
}

# Routing order: the constrained nets first.
# The LED link's pairs go first: its corner of the tab is small, and the 5 V feeding its
# terminations would otherwise wall them off.
ORDER = (["LINK_SCLP", "LINK_SCLM", "LINK_SDAP", "LINK_SDAM"] + ["VIN", "+5V", "IDEAL_G", "VCAP"] +
         ["TACH_IN", "TACH_A", "TACH_B", "TACH_LED", "TACH_GND"] +
         ["N2K_NET_S", "N2K_12V", "N2K_GND", "N2K_5V", "N2K_H", "N2K_L", "N2K_TERM"] +
         ["CAN_TXD", "CAN_RXD", "CAN_CLK"] +
         [f"{n}_{s}" for n, *_ in design.CHANNELS if n != "HOUSE" for s in ("IN", "DIV", "ADC")] +
         ["SDA", "SCL",
          "CAN_INT", "SPI_CE0", "SPI_MOSI", "SPI_MISO", "SPI_SCLK",
          "TACH_OUT", "TACH_GPIO", "LIGHT_SDA", "LIGHT_SCL",
          # the house battery's input crosses the board from J1.8: after everything it could wall off
          "HOUSE_DIV", "HOUSE_ADC", "HOUSE_IN", "+3V3"])
# The SPI bus runs the length of the board, from the header down to the CAN controller, across
# every input lane and past the converters: it prefers the bottom layer, leaving the top to the
# parts it passes.
PREFER_BOTTOM = {"CAN_INT", "SPI_CE0", "SPI_MOSI", "SPI_MISO", "SPI_SCLK"}


def mm(v):
    return pcbnew.FromMM(v)


def V(x, y):
    return pcbnew.VECTOR2I(mm(x + OX), mm(y + OY))


def unused_pins():
    """(ref, pad) -> net name for every pin the schematic leaves unconnected, named as KiCad
    names them ("unconnected-(U1-ALERT/RDY-Pad2)"), from the schematic's exported netlist."""
    out = HERE / "sensor-board.net"
    subprocess.run([str(CLI), "sch", "export", "netlist", "-o", str(out),
                    str(HERE / "sensor-board.kicad_sch")], check=True, capture_output=True)
    found = {}
    tree = sexpr.parse(out.read_text(encoding="utf-8"))
    for net in sexpr.find_all(sexpr.find(tree, "nets"), "net"):
        name = unquote(sexpr.find(net, "name")[1])
        if name.startswith("unconnected-("):
            for node in sexpr.find_all(net, "node"):
                found[(unquote(sexpr.find(node, "ref")[1]), unquote(sexpr.find(node, "pin")[1]))] = name
    out.unlink()
    return found


def unquote(atom):
    return atom[1:-1].replace('\\"', '"').replace("\\\\", "\\") if atom.startswith('"') else atom


def pcb_net_name(name):
    return name if name in ("GND", "+3V3", "+5V") else "/" + name


def clearance(a, b):
    if (a in HV) != (b in HV):
        return 3.0
    if a in HV and b in HV and (a in SERIES or b in SERIES):
        return 0.6
    # The network side against the Pi side. (An unused pad -- net None -- is neither: the drop
    # connector's shield pin sits 3.81 mm from NET-S and must not force 2 mm around it.)
    if a is not None and b is not None and (a in N2K) != (b in N2K):
        return N2K_CLEAR
    return 0.2


def load_fp(fpid):
    lib, name = fpid.split(":")
    folder = HERE / f"{lib}.pretty" if lib == "sensor-board" else FP_DIR / f"{lib}.pretty"
    fp = pcbnew.FootprintLoad(str(folder), name)
    if fp is None:
        raise KeyError(fpid)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def pad_item(pad, net):
    """The router's picture of a pad: its copper as an axis-aligned (rounded) rectangle or circle."""
    pos = pad.GetPosition()
    x, y = pcbnew.ToMM(pos.x) - OX, pcbnew.ToMM(pos.y) - OY
    try:
        size = pad.GetSize(pcbnew.F_Cu)
        shape = pad.GetShape(pcbnew.F_Cu)
    except TypeError:
        size, shape = pad.GetSize(), pad.GetShape()
    sx, sy = pcbnew.ToMM(size.x), pcbnew.ToMM(size.y)
    ang = round(pad.GetOrientationDegrees()) % 180
    if ang == 90:
        sx, sy = sy, sx
    if shape == pcbnew.PAD_SHAPE_CUSTOM:
        # A custom pad (SOT-89's tab, a solder jumper's half-moons) is far bigger than its anchor
        # size says: the router has to see the whole of it, or it runs tracks straight across.
        bb = pad.GetBoundingBox()
        x0, y0 = pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetY()) - OY
        w, h = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())
        layers = [l for l, cu in ((TOP, pcbnew.F_Cu), (BOTTOM, pcbnew.B_Cu)) if pad.IsOnLayer(cu)]
        return Item(net, layers, "rect", x=x0 + w / 2, y=y0 + h / 2, hw=w / 2, hh=h / 2, corner=0.0)
    layers = []
    if pad.IsOnLayer(pcbnew.F_Cu):
        layers.append(TOP)
    if pad.IsOnLayer(pcbnew.B_Cu):
        layers.append(BOTTOM)
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


LIB = Library(FP_DIR, {"sensor-board": HERE / "sensor-board.pretty"})


def geo_item(pad, net):
    layers = ([TOP] if pad.top else []) + ([BOTTOM] if pad.bottom else [])
    if pad.shape == "circle":
        return Item(net, layers, "circle", x=pad.x, y=pad.y, r=pad.hw)
    return Item(net, layers, "rect", x=pad.x, y=pad.y, hw=pad.hw, hh=pad.hh, corner=pad.corner)


def same_pad(a, b, tol=0.02):
    """pcbnew's pad and padgeom's agree (the dry run is only worth anything if they do)."""
    ga, gb = a.geo, b.geo
    if a.kind != b.kind or a.layers != b.layers:
        return False
    keys = ("x", "y", "r") if a.kind == "circle" else ("x", "y", "hw", "hh")
    return all(abs(ga[k] - gb[k]) <= tol for k in keys)


class Board:
    def __init__(self, dry_run=False):
        self.dry = dry_run or pcbnew is None
        self.tracks, self.vias = [], []     # what the dry run draws
        if not self.dry:
            self.board = pcbnew.BOARD()
            self.board.SetCopperLayerCount(2)
        self.nets = {}
        self.fps = {}
        # A 0.03 mm margin: enough for grid rounding, and small enough that a 0.2 mm track can still
        # reach the middle pins of a 0.5 mm-pitch package (the real gap there is 0.225 mm).
        self.router = Router(W, H + LINK_T, clearance, track=TRACK, via_d=VIA_D, margin=0.03, origin=(0.0, -LINK_T))
        self.router.outside.append((0.0, -LINK_T, TAB_W, -TAB_T))   # above J7's tab: not board
        self.pad_items = {}          # (ref, pad number) -> Item
        self.unused = {} if self.dry else unused_pins()
        self.tree = {}               # net -> list of (i, j, layer) cells already part of it
        self.failed = []
        self.stitched = set()        # GND pads that already have their via

    # ---------------------------------------------------------------- setup
    def net(self, name, raw=False):
        """The board net for a design.py net name. Label-connected nets are named with the root
        sheet's path, as KiCad names them from the schematic; power nets are global."""
        if name not in self.nets:
            ni = pcbnew.NETINFO_ITEM(self.board, name if raw else pcb_net_name(name))
            self.board.Add(ni)
            self.nets[name] = ni
        return self.nets[name]

    def outline(self):
        """The HAT with the tab on top, every corner rounded: 3 mm, except 1 mm at the tab's
        top-left, where J7's body reaches almost to the corner, and in the inside corner beside the
        tab (no router bit cuts a sharp one)."""
        corners = [((0.0, -TAB_T), 1.0), ((TAB_W, -TAB_T), 1.0), ((TAB_W, -LINK_T), 3.0),
                   ((W, -LINK_T), 3.0), ((W, H), 3.0), ((0.0, H), 3.0)]
        ends = []
        for k, ((px, py), r) in enumerate(corners):
            (ax, ay), (bx, by) = corners[k - 1][0], corners[(k + 1) % len(corners)][0]
            d1 = ((px - ax) / abs(px - ax + py - ay), (py - ay) / abs(px - ax + py - ay))   # edges are axis-aligned
            d2 = ((bx - px) / abs(bx - px + by - py), (by - py) / abs(bx - px + by - py))
            a = (px - d1[0] * r, py - d1[1] * r)
            b = (px + d2[0] * r, py + d2[1] * r)
            cx, cy = a[0] + d2[0] * r, a[1] + d2[1] * r                  # the fillet's centre
            m = (cx + (px - cx) / 2 ** 0.5, cy + (py - cy) / 2 ** 0.5)   # its midpoint, toward the corner
            s = pcbnew.PCB_SHAPE(self.board)
            s.SetShape(pcbnew.SHAPE_T_ARC)
            s.SetArcGeometry(V(*a), V(*m), V(*b))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.board.Add(s)
            ends.append((a, b))
        for k in range(len(ends)):
            s = pcbnew.PCB_SHAPE(self.board)
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(V(*ends[k][1]))
            s.SetEnd(V(*ends[(k + 1) % len(ends)][0]))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.board.Add(s)

    def place(self):
        for part in design.PARTS:
            x, y, rot, side = PLACE[part.ref]
            geo = LIB.pads(part.footprint, x, y, rot, flip=side == "B")
            mine = {}
            for pad in geo:
                if pad.npth:
                    self.router.holes.append((pad.x, pad.y, pad.hw + 0.25))
                    continue
                item = geo_item(pad, part.pins.get(pad.number))
                self.router.items.append(item)
                mine.setdefault(pad.number, item)
            self.pad_items.update({(part.ref, n): it for n, it in mine.items()})
            if not self.dry:
                self.place_pcbnew(part, x, y, rot, side, [geo_item(p, part.pins.get(p.number)) for p in geo if not p.npth])
        for hx, hy in HOLES:
            self.router.holes.append((hx, hy, 2.75 / 2 + 0.5))
        self.router.keepouts.append(self.link_keepout())
        # The header's GND pins reach the pours through thermal spokes aimed at the gaps between
        # the pins around them. A track squeezed past one -- between the rows, between two pins, or
        # along the board edge above the outer row -- cuts those off, and KiCad's DRC rightly calls
        # the pin starved. So other nets stay out of the half-pitch square round each one, up to the
        # board edge for the outer row, on both layers.
        for (ref, num), item in self.pad_items.items():
            if ref == "J4" and item.net == "GND":
                x, y = item.geo["x"], item.geo["y"]
                top = -1.0 if y < 3.5 else y - 1.3
                self.router.keepouts.append((x - 1.3, top, x + 1.3, y + 1.3, lambda n: n == "GND"))
        # Nothing but the ignition side inside the tach's isolation zone, and nothing but the
        # NMEA 2000 network's side inside its zone (both set after placement, from the parts that
        # are actually there).
        self.router.keepouts.append((*self.hv_zone(), lambda n: n in HV))
        self.router.keepouts.append((*self.n2k_zone(), lambda n: n in N2K))
        # ...and the network side's own tracks stay inside their routing area, so every one of
        # them is at least 2 mm from the Pi's ground pour outside it.
        rx0, ry0, rx1, _ = self.n2k_route()
        not_n2k = lambda n: n not in N2K
        self.router.keepouts += [(0.0, -LINK_T, W, ry0, not_n2k), (0.0, ry0, rx0, H, not_n2k), (rx1, ry0, W, H, not_n2k)]

    def link_keepout(self):
        """Nothing on top under the RJ45's front, between its pins and the tab's edge, where the plug
        goes. (Underneath is free: its middle pins' tracks leave that way.)"""
        x0, y0, x1, y1 = self.courtyard("J6")
        pads = [it.geo for (r, _), it in self.pad_items.items() if r == "J6"]
        return (x0, -LINK_T, x1, min(g["y"] - g.get("hh", g.get("r", 0)) for g in pads) - 0.1, lambda n: False, (TOP,))

    def place_pcbnew(self, part, x, y, rot, side, mine):
        fp = load_fp(part.footprint)
        fp.SetReference(part.ref)
        fp.SetValue(part.value)
        self.board.Add(fp)
        fp.SetPosition(V(x, y))
        if side == "B":
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(rot)
        fp.SetPath(pcbnew.KIID_PATH("/" + schematic.uid("sym", part.ref)))
        # The same fields the schematic symbol carries, so the parity check can match them.
        for key, val in (("MPN", part.mpn), ("Note", part.note)):
            fp.SetField(key, val)
            fp.GetField(key).SetVisible(False)
        # References live on the assembly drawing; this board is packed at courtyard spacing
        # and there is no room for them on the silkscreen without landing on pads.
        fp.Reference().SetLayer(pcbnew.B_Fab if side == "B" else pcbnew.F_Fab)
        for pad in fp.Pads():
            num = pad.GetNumber()
            netname = part.pins.get(num)
            if netname:
                pad.SetNet(self.net(netname))
            elif (part.ref, num) in self.unused:
                # An unused pin gets the schematic's own name for it, unconnected-(...)
                pad.SetNet(self.net(self.unused[(part.ref, num)], raw=True))
            if (part.ref, num) in NO_POUR_JOIN:
                pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_NONE)
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                continue
            theirs = pad_item(pad, netname)
            if not any(same_pad(theirs, m) for m in mine):
                sys.exit(f"{part.ref} pad {num}: padgeom and pcbnew disagree ({theirs.geo}) -- fix padgeom.py first")
        self.fps[part.ref] = fp

    def courtyard(self, ref):
        part = next(p for p in design.PARTS if p.ref == ref)
        cy = LIB.courtyard(part.footprint)
        x, y, rot, side = PLACE[ref]
        ys = (-cy[3], -cy[1]) if side == "B" else (cy[1], cy[3])
        pts = [rotate(px, py, rot) for px in (cy[0], cy[2]) for py in ys]
        return (x + min(p[0] for p in pts), y + min(p[1] for p in pts), x + max(p[0] for p in pts),
                y + max(p[1] for p in pts))

    def overlaps(self):
        """Pairs of same-side parts whose courtyard rectangles overlap."""
        refs = [p.ref for p in design.PARTS]
        boxes = {r: self.courtyard(r) for r in refs}
        out = []
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                if PLACE[a][3] != PLACE[b][3]:
                    continue
                A, B = boxes[a], boxes[b]
                if A[0] < B[2] - 1e-6 and B[0] < A[2] - 1e-6 and A[1] < B[3] - 1e-6 and B[1] < A[3] - 1e-6:
                    out.append((a, b))
        return out

    def zone_of(self, nets, grow):
        """The rectangle around every pad on these nets, grown by `grow`, down to the bottom edge."""
        xs, ys = [], []
        for (ref, num), it in self.pad_items.items():
            if it.net in nets:
                g = it.geo
                hw, hh = (g["hw"], g["hh"]) if it.kind == "rect" else (g["r"], g["r"])
                xs += [g["x"] - hw, g["x"] + hw]
                ys += [g["y"] - hh, g["y"] + hh]
        return (max(0.0, min(xs) - grow), max(0.0, min(ys) - grow), min(W, max(xs) + grow), H)

    def hv_zone(self):
        return self.zone_of(HV, 3.0)

    def n2k_route(self):
        """Where the network side's tracks may run: its pads, plus a margin left and right."""
        x0, y0, x1, y1 = self.zone_of(N2K, 0.0)
        return max(0.0, x0 - N2K_ROUTE_MARGIN), y0, min(W, x1 + N2K_ROUTE_MARGIN), y1

    def n2k_zone(self):
        """The routing area plus the 2 mm isolation: no Pi-side copper at all inside this."""
        x0, y0, x1, y1 = self.n2k_route()
        return max(0.0, x0 - N2K_CLEAR), y0 - N2K_CLEAR, min(W, x1 + N2K_CLEAR), y1

    # ---------------------------------------------------------------- routing
    def add_track(self, net, a, b, layer, width):
        self.tracks.append((net, a, b, layer, width))
        self.router.items.append(Item(net, [layer], "seg", x1=a[0], y1=a[1], x2=b[0], y2=b[1], w=width))
        if self.dry:
            return
        t = pcbnew.PCB_TRACK(self.board)
        t.SetStart(V(*a))
        t.SetEnd(V(*b))
        t.SetWidth(mm(width))
        t.SetLayer(pcbnew.F_Cu if layer == TOP else pcbnew.B_Cu)
        t.SetNet(self.net(net))
        self.board.Add(t)

    def add_via(self, net, x, y):
        self.vias.append((net, x, y))
        self.router.items.append(Item(net, [TOP, BOTTOM], "circle", x=x, y=y, r=VIA_D / 2))
        if self.dry:
            return
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

    def cells_of(self, item):
        out = []
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

    def route_net(self, net):
        members = [(r, p) for r, p in design.nets()[net] if (r, p) in self.pad_items]
        width = HV_TRACK if net in HV else WIDE.get(net, TRACK)
        self.router.layer_cost = (3, 1) if net in PREFER_BOTTOM else (1, 3)
        if len(members) < 2:
            return
        # A net with a current path (MAIN_PATH) routes that first at full width, then the rest thin.
        main = MAIN_PATH.get(net)
        stages = [(members, width)] if main is None else [
            ([m for m in members if m in main], width), ([m for m in members if m not in main], THIN)]
        # Grow a tree: start at the first pad, then join the nearest remaining pad each time.
        tree_cells = list(self.cells_of(self.pad_items[stages[0][0][0]]))
        for k, (group, w) in enumerate(stages):
            self.router.track = w
            if k == 0 and net in MAIN_BOTTOM:
                self.router.layer_cost = (3, 1)
            elif k == 1:
                self.router.layer_cost = (3, 1) if net in PREFER_BOTTOM else (1, 3)
            todo = [self.pad_items[m] for m in (group[1:] if k == 0 else group)]
            while todo:
                def centre(it):
                    return it.geo["x"], it.geo["y"]
                tx, ty = self.router.xy(sum(c[0] for c in tree_cells) / len(tree_cells),
                                        sum(c[1] for c in tree_cells) / len(tree_cells))
                todo.sort(key=lambda it: (centre(it)[0] - tx) ** 2 + (centre(it)[1] - ty) ** 2)
                target = todo.pop(0)
                goal_cells = set(self.cells_of(target))
                if set(tree_cells) & goal_cells:        # already touching (a shared pad)
                    tree_cells += list(goal_cells)
                    continue
                path = self.router.search(net, tree_cells, lambda i, j, l: (i, j, l) in goal_cells, centre(target))
                if path is None:
                    self.failed.append((net, target.geo.get("x"), target.geo.get("y")))
                    continue
                self.commit(net, path, w)
                tree_cells += path + list(goal_cells)

    def stitch_gnd(self, refs=None):
        """A via beside every surface-mount GND pad (of these parts, or all), into the bottom plane."""
        self.router.track = TRACK
        for (ref, num), item in self.pad_items.items():
            if item.net != "GND" or item.layers != {TOP} or (ref, num) in self.stitched:
                continue
            if refs is not None and ref not in refs:
                continue
            self.stitched.add((ref, num))
            g = item.geo
            hw, hh = (g["hw"], g["hh"]) if item.kind == "rect" else (g["r"], g["r"])
            via_maps = self.router.blocked("GND", VIA_D / 2)

            def ok(i, j, layer):
                if layer != TOP or via_maps[TOP][i, j] or via_maps[BOTTOM][i, j]:
                    return False
                x, y = self.router.xy(i, j)
                dx, dy = max(abs(x - g["x"]) - hw, 0), max(abs(y - g["y"]) - hh, 0)
                return (dx * dx + dy * dy) ** 0.5 >= VIA_D / 2 + 0.1   # clear of the pad itself
            path = self.router.search("GND", self.cells_of(item), ok, (g["x"], g["y"]), via_ok=False,
                                      allow_layers=(TOP,))
            if path is None:
                self.failed.append(("GND via", ref, num))
                continue
            end = path[-1]
            if len(path) > 1:
                self.commit("GND", path, TRACK)
            self.add_via("GND", *self.router.xy(end[0], end[1]))

    # ---------------------------------------------------------------- copper pours, text
    def pours(self):
        gnd = self.net("GND")
        inset = 0.3
        # The Pi's ground pour everywhere except the NMEA 2000 network's corner, which gets a pour
        # of the network's own ground instead, 2 mm in from it all round -- and, at the top, 4 mm
        # clear of the Pi's mounting hole there, so a metal standoff or screw head can never bridge
        # the isolation.
        nx0, ny0, nx1, _ = self.n2k_zone()
        rx0, ry0, rx1, _ = self.n2k_route()
        hole_y = max(hy for hx, hy in HOLES if hx < nx1 and hy < ny0 + 6) + 4.0
        pts = [(inset, -TAB_T + inset), (TAB_W + inset, -TAB_T + inset), (TAB_W + inset, -LINK_T + inset),
               (W - inset, -LINK_T + inset), (W - inset, H - inset), (nx1, H - inset), (nx1, ny0), (inset, ny0)]
        n2k_pts = [(max(inset, rx0), max(ry0, hole_y)), (rx1, max(ry0, hole_y)),
                   (rx1, H - inset), (max(inset, rx0), H - inset)]
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            z = pcbnew.ZONE(self.board)
            z.SetLayer(layer)
            z.SetNet(self.net("N2K_GND"))
            ol = z.Outline()
            ol.NewOutline()
            for x, y in n2k_pts:
                ol.Append(mm(x + OX), mm(y + OY))
            z.SetMinThickness(mm(0.2))
            z.SetThermalReliefGap(mm(0.4))
            z.SetThermalReliefSpokeWidth(mm(0.4))
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
            z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
            self.board.Add(z)
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            z = pcbnew.ZONE(self.board)
            z.SetLayer(layer)
            z.SetNet(gnd)
            ol = z.Outline()
            ol.NewOutline()
            for x, y in pts:
                ol.Append(mm(x + OX), mm(y + OY))
            z.SetMinThickness(mm(0.2))
            z.SetThermalReliefGap(mm(0.4))
            z.SetThermalReliefSpokeWidth(mm(0.4))
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
            z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
            try:
                z.SetLocalClearance(mm(0.3))
            except TypeError:
                pass
            self.board.Add(z)
        # No pour anywhere near the ignition side.
        x0, y0, x1, y1 = self.hv_zone()
        k = pcbnew.ZONE(self.board)
        k.SetIsRuleArea(True)
        for name in ("SetDoNotAllowZoneFills", "SetDoNotAllowCopperPour"):
            if hasattr(k, name):
                getattr(k, name)(True)
        for name in ("SetDoNotAllowTracks", "SetDoNotAllowVias", "SetDoNotAllowPads", "SetDoNotAllowFootprints"):
            if hasattr(k, name):
                getattr(k, name)(False)
        ls = pcbnew.LSET()
        ls.AddLayer(pcbnew.F_Cu)
        ls.AddLayer(pcbnew.B_Cu)
        k.SetLayerSet(ls)
        ol = k.Outline()
        ol.NewOutline()
        for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
            ol.Append(mm(x + OX), mm(y + OY))
        k.SetZoneName("tach ignition side: no pour")
        self.board.Add(k)
        # The filler finds the islands to remove through the board's connectivity, which has to
        # know about every track first; otherwise slivers between pads survive as floating copper.
        self.board.BuildConnectivity()
        pcbnew.ZONE_FILLER(self.board).Fill(self.board.Zones())

    def silk(self):
        def text(s, x, y, size=1.0, layer=pcbnew.F_SilkS, rot=0, just=None):
            t = pcbnew.PCB_TEXT(self.board)
            t.SetText(s)
            t.SetPosition(V(x, y))
            t.SetLayer(layer)
            t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
            t.SetTextThickness(mm(max(0.15, size * 0.15)))   # PCBWay: 0.15 mm stroke, 0.8 mm height minimum
            t.SetTextAngleDegrees(rot)
            if just == "left":
                t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
            elif just == "right":
                t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_RIGHT)
            self.board.Add(t)
        # Every connector labelled at its own pins: what each one connects to, printed on the side
        # the wires come from (the plug side is hidden under the connector body once it's fitted).
        # Connectors along the left and right edges get vertical labels, one per 3.81 mm pin.
        INSET = 3.45   # pin centre to label centre: clear of the connector body and its pin-1 triangle
        for ref, names in PIN_LABELS.items():
            pads = sorted((p for p in self.fps[ref].Pads() if p.GetNumber().isdigit()), key=lambda p: int(p.GetNumber()))
            pts = [(pcbnew.ToMM(p.GetPosition().x) - OX, pcbnew.ToMM(p.GetPosition().y) - OY) for p in pads]
            pitch = abs(pts[1][1] - pts[0][1]) + abs(pts[1][0] - pts[0][0])
            if abs(pts[0][0] - pts[-1][0]) < 0.1 and pitch < 2:   # fine pitch (J6): one line of text per pin
                for (px, py), name in zip(pts, names):
                    text(name, px - 1.45, py, 0.8, just="right")
            elif abs(pts[0][0] - pts[-1][0]) < 0.1:          # a column of pins: an edge connector, left or right
                x = pts[0][0] + (INSET if pts[0][0] < W / 2 else -INSET)
                for (px, py), name in zip(pts, names):
                    text(name, x, py, 0.8, rot=90)
            else:                                            # a row along the bottom edge, or J7's on the tab
                side = -1 if pts[0][1] > H / 2 else 1
                for (px, py), name in zip(pts, names):
                    text(name, px, py + side * INSET, 0.8)
        # Which connector is which, beside its labels.
        text("J1", 12.15, 11.6, 0.9, rot=90)
        text("J2", 40.2, 63.85, 0.9, just="right")
        text("J5 NMEA 2000", 15.6, 62.6, 0.9)
        # The RJ45's, in the strip between it and the tab's left edge (its right is the pairs'
        # terminations): what it is, and the one thing never to do with it.
        x0, y0, x1, y1 = self.courtyard("J6")
        text("J6 LED LINK", x0 - 2.0, -1.2, 0.8, rot=90, just="left")
        text("NOT ETHERNET", x0 - 0.8, -1.2, 0.8, rot=90, just="left")
        text("J7 5V IN", 15.0, 0.15, 0.9, just="left")
        # The rules the pin labels can't carry, in plain words. (Rev 1.0-1.1 had a numbered pinout
        # here instead, which said less than the labels now do.)
        lines = ["WIRING", "FUEL TRIM OIL TEMP: that gauge's S terminal", "IGN: any gauge's I terminal",
                 "ENG+ HSE+: each battery's +, 1A fuse there",
                 "GND: gauge G terminal / battery -", "TACH: gray wire, coil TACH terminal",
                 "NMEA: drop cable, bare shield unused", "LIGHTS: J6, patch cable to LED board J1",
                 "5V IN: J7, from the converter at 5.1V",
                 "", f"{design.TITLE} r{design.REVISION}", "github.com/erod998/boatmfd"]
        y = 36.0   # below the house battery's lane
        for body in lines:
            if body:
                text(body, 34.6, y, 1.0 if body == "WIRING" else 0.8, just="left")
            y += 1.5 if body == "WIRING" else 1.35 if body else 0.6
        # The bench-only terminator: say what the pads are for, and what not to do with them.
        text("TERM", 30.7, 61.8, 0.8, rot=90)

    # ---------------------------------------------------------------- all of it
    def titles(self):
        """The title block, for the PDFs; and the place/drill origin at the board's bottom-left
        corner, so the Gerbers, drill file and pick-and-place file all share it."""
        tb = pcbnew.TITLE_BLOCK()
        tb.SetTitle(design.TITLE)
        tb.SetRevision(design.REVISION)
        tb.SetDate(design.DATE)
        tb.SetComment(0, "Passive taps on the existing gauges; Delco EST tach; isolated NMEA 2000")
        tb.SetComment(1, "Generated from design.py by board.py; edit those, not this file")
        self.board.SetTitleBlock(tb)
        self.board.GetDesignSettings().SetAuxOrigin(V(0, H))

    def build(self):
        if not self.dry:
            self.titles()
            self.outline()
        self.place()
        rest = sorted(n for n in design.nets() if n not in ORDER and n != "GND")
        for net in ORDER + rest:
            self.route_net(net)
            print(f"  routed {net}", flush=True)
        self.stitch_gnd()
        if not self.dry:
            self.pours()
            self.silk()
        return self.failed

    def preview(self, path, zoom=None):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, Rectangle
        x0, y0, x1, y1 = zoom or (-2, -LINK_T - 2, W + 2, H + 2)
        fig, ax = plt.subplots(figsize=(12, 12 * (y1 - y0) / (x1 - x0)))
        ol = [(0, -TAB_T), (TAB_W, -TAB_T), (TAB_W, -LINK_T), (W, -LINK_T), (W, H), (0, H), (0, -TAB_T)]
        ax.plot([p[0] for p in ol], [p[1] for p in ol], "k-", lw=1.5)
        for net, a, b, layer, w in self.tracks:
            ax.plot([a[0], b[0]], [a[1], b[1]], color="tab:red" if layer == TOP else "tab:blue", lw=w * 12,
                    solid_capstyle="round", alpha=0.7)
        for it in self.router.items:
            if it.kind == "seg":
                continue
            g = it.geo
            col = "goldenrod" if len(it.layers) == 2 else ("darkgoldenrod" if TOP in it.layers else "tan")
            if it.kind == "circle":
                ax.add_patch(Circle((g["x"], g["y"]), g["r"], fc=col, ec="none"))
            else:
                ax.add_patch(Rectangle((g["x"] - g["hw"], g["y"] - g["hh"]), 2 * g["hw"], 2 * g["hh"], fc=col, ec="none"))
        for p in design.PARTS:
            a0, b0, a1, b1 = self.courtyard(p.ref)
            ax.add_patch(Rectangle((a0, b0), a1 - a0, b1 - b0, fill=False, ec="purple" if PLACE[p.ref][3] == "F" else "gray",
                                   lw=0.4))
            ax.text((a0 + a1) / 2, (b0 + b1) / 2, p.ref, fontsize=6, ha="center", va="center", color="purple", clip_on=True)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y1, y0)
        ax.set_aspect("equal")
        ax.set_title(f"{design.TITLE} r{design.REVISION} -- dry run (red: top, blue: bottom)")
        fig.savefig(path, dpi=110, bbox_inches="tight")

    def save(self, path):
        pcbnew.SaveBoard(str(path), self.board)


def write_rules(path):
    patterns = " || ".join(f"A.NetName == '{pcb_net_name(n)}'" for n in sorted(SERIES))
    Path(path).write_text(f"""(version 1)

# The tach's ignition side (Delco EST gray wire: 12 V between sparks, a few hundred volts at each
# spark) stays 3 mm from every other net on the board.
(rule "tach ignition side isolation"
  (constraint clearance (min 3mm))
  (condition "A.NetClass == 'TACH_HV' && B.NetClass != 'TACH_HV'"))

# Inside the ignition side, the spike is divided across the three series resistors: keep those
# nets 0.6 mm from each other and from the LED side.
(rule "tach series chain"
  (constraint clearance (min 0.6mm))
  (condition "A.NetClass == 'TACH_HV' && B.NetClass == 'TACH_HV' && A.NetName != B.NetName && ({patterns})"))

# The NMEA 2000 network's side of the CAN isolator (NET-S, NET-C, NET-H, NET-L and its 5 V) is
# referenced to the network's ground, not the Pi's: 2 mm from every other net. Unused pads (the
# drop connector's shield pin) and unnetted mounting features have no net and are not counted.
(rule "nmea 2000 isolation"
  (constraint clearance (min 2mm))
  (condition "A.NetClass == 'N2K_BUS' && B.NetClass != 'N2K_BUS' && B.NetClass != 'TACH_HV' && B.NetName != '' && B.NetName != 'unconnected-(J5-Pin_1-Pad1)'"))
""", encoding="utf-8")


def write_project(path):
    tpl = TEMPLATES / "RaspberryPi-HAT" / "RaspberryPi-HAT.kicad_pro"
    pro = json.loads(tpl.read_text(encoding="utf-8"))
    pro["meta"]["filename"] = Path(path).name
    default = dict(pro["net_settings"]["classes"][0])
    default.update({"name": "Default", "clearance": 0.2, "track_width": TRACK, "via_diameter": VIA_D, "via_drill": VIA_DRILL})
    hv = dict(default, name="TACH_HV", track_width=HV_TRACK, priority=0)
    n2k = dict(default, name="N2K_BUS", track_width=0.4, priority=1)
    pro["net_settings"]["classes"] = [default, hv, n2k]
    pro["net_settings"]["netclass_patterns"] = (
        [{"netclass": "TACH_HV", "pattern": pcb_net_name(n)} for n in design.TACH_HV_NETS] +
        [{"netclass": "N2K_BUS", "pattern": pcb_net_name(n)} for n in design.N2K_BUS_NETS])
    rules = pro["board"]["design_settings"]["rules"]
    # PCBWay's 2-layer limits (pcbway.com/capabilities.html) at 8/8 mil, the setting that allows 2 oz
    # copper (ordered with the LED board): tracks/gaps 0.2 mm, annular ring 0.15 mm, hole-to-hole
    # 16 mil, copper to a routed edge 0.25 mm, silkscreen 0.8 mm tall with a 0.15 mm stroke.
    rules.update({"min_clearance": 0.2, "min_track_width": 0.2, "min_via_diameter": 0.6, "min_via_annular_width": 0.15,
                  "min_through_hole_diameter": 0.3, "min_copper_edge_clearance": 0.3, "min_hole_to_hole": 0.41,
                  "min_hole_clearance": 0.25, "min_text_height": 0.8, "min_text_thickness": 0.15})
    pro["sheets"] = [[schematic.ROOT, "Root"]]
    Path(path).write_text(json.dumps(pro, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="place and route without KiCad, and draw a preview")
    ap.add_argument("--preview", default=str(HERE / "reports" / "sensor-board-dry-run.png"))
    ap.add_argument("--zoom", help="x0,y0,x1,y1: also draw just this part of the board")
    args = ap.parse_args()
    b = Board(dry_run=args.dry_run)
    failed = b.build()
    clash = b.overlaps()
    if b.dry:
        Path(args.preview).parent.mkdir(parents=True, exist_ok=True)
        b.preview(args.preview)
        if args.zoom:
            b.preview(args.preview.replace(".png", "-zoom.png"), tuple(float(v) for v in args.zoom.split(",")))
        print("preview:", args.preview)
        print(f"{len(b.tracks)} track segments, {len(b.vias)} vias")
    else:
        out = HERE / "sensor-board.kicad_pcb"
        b.save(out)
        write_project(HERE / "sensor-board.kicad_pro")
        write_rules(HERE / "sensor-board.kicad_dru")
        print("wrote", out.name)
    if clash:
        print("OVERLAPPING COURTYARDS:", clash)
    if failed:
        print("UNROUTED:", failed)
    if failed or clash:
        sys.exit(1)


if __name__ == "__main__":
    main()
