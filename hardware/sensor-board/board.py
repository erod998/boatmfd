"""Lays out and routes sensor-board.kicad_pcb from design.py. Run with KiCad's bundled Python.

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
import json
import subprocess
import sys
from pathlib import Path

import pcbnew

import design
import schematic
import sexpr
from router import BOTTOM, TOP, Item, Router

HERE = Path(__file__).parent
KICAD = Path(r"C:\Users\erod9\AppData\Local\Programs\KiCad\10.0")
FP_DIR = KICAD / "share" / "kicad" / "footprints"
OX, OY = 100.0, 50.0                 # where the board sits on KiCad's page
# The Raspberry Pi HAT outline (KiCad's own HAT template), 20 mm longer on the side away from the
# header: rev 1.0 was the plain 65 x 56 HAT and had no room left for the NMEA 2000 interface.
# The extra 20 mm reaches past the Pi's USB-C / micro-HDMI edge, about 5 mm above those plugs.
W, H, HAT_H = 65.0, 76.0, 56.0
TRACK, HV_TRACK, VIA_D, VIA_DRILL = 0.2, 0.25, 0.7, 0.3
HV = set(design.TACH_HV_NETS)
SERIES = {"TACH_IN", "TACH_A", "TACH_B"}   # the nets the ignition spike is divided across
N2K = set(design.N2K_BUS_NETS)
N2K_CLEAR = 2.0
# Room the network side's tracks get beyond its pads, left and right; none upward, where the
# isolator's Pi-side pins are only 3.4 mm away.
N2K_ROUTE_MARGIN = 1.5
# The network side's power is carried on wider tracks.
WIDE = {"N2K_NET_S": 0.4, "N2K_12V": 0.4, "N2K_GND": 0.4, "N2K_5V": 0.4}
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
    # Probes on the right edge, and their protection.
    "J3": (56.3, 26.0, 90, "F"),
    "D8": (48.3, 22.2, 180, "F"), "R25": (50.8, 18.8, 180, "F"), "R24": (50.8, 15.6, 180, "F"),
    # Lights, on the right edge between the probes and the mounting hole: the cable enters from
    # the edge, pin 1 at the bottom (as J3). Its mounting tabs are kept 0.3 mm inside the edge.
    "J6": (62.0, 41.6, 90, "F"),
}
for i, y in enumerate(LANE_Y):
    PLACE[f"R{1 + i}"] = (14.8, y, 0, "F")            # top resistor: IN left, DIV right
    PLACE[f"R{7 + i}"] = (18.6, y + 1.7, -90, "F")    # divider bottom: DIV up, GND down
    PLACE[f"D{1 + i}"] = (22.0, y - 0.94, -90, "F")   # clamp: COM down onto the lane, GND/3V3 up
    PLACE[f"R{13 + i}"] = (25.8, y, 0, "F")           # series: DIV left, ADC right
    PLACE[f"C{1 + i}"] = (29.2, y + 1.7, -90, "F")    # filter: ADC up, GND down

# What each connector pin is for, printed beside the pin (silk()). Pin order.
PIN_LABELS = {
    "J1": ["FUEL", "TRIM", "BAT+", "IGN", "OIL", "TEMP", "GND", "GND"],
    "J2": ["TACH", "GND"],
    "J3": ["3V3", "DATA", "GND"],
    "J5": ["BARE", "RED", "BLK", "WHT", "BLU"],     # the drop cable's wire colours
    "J6": ["3V3", "SDA", "SCL", "GND", "DAT1", "DAT2", "GND", "AUX"],
}

# Routing order: the constrained nets first.
ORDER = (["TACH_IN", "TACH_A", "TACH_B", "TACH_LED", "TACH_GND"] +
         ["N2K_NET_S", "N2K_12V", "N2K_GND", "N2K_5V", "N2K_H", "N2K_L", "N2K_TERM"] +
         ["CAN_TXD", "CAN_RXD", "CAN_CLK"] +
         [f"{n}_{s}" for n, *_ in design.CHANNELS for s in ("IN", "DIV", "ADC")] +
         ["SDA", "SCL",
          "CAN_INT", "SPI_CE0", "SPI_MOSI", "SPI_MISO", "SPI_SCLK",
          "TACH_OUT", "TACH_GPIO", "OW_EXT", "OW", "LIGHT_DAT1", "LIGHT_DAT2", "LIGHT_AUX", "+3V3"])
# The SPI bus runs the length of the board, from the header down to the CAN controller, across
# every input lane and past the converters: it prefers the bottom layer, leaving the top to the
# parts it passes.
# So do the two lights lines from the header's far corner: on top they wrap round the header's
# GND pin 39 and cut it off from the pour.
PREFER_BOTTOM = {"CAN_INT", "SPI_CE0", "SPI_MOSI", "SPI_MISO", "SPI_SCLK", "LIGHT_DAT2", "LIGHT_AUX"}


def mm(v):
    return pcbnew.FromMM(v)


def V(x, y):
    return pcbnew.VECTOR2I(mm(x + OX), mm(y + OY))


def unused_pins():
    """(ref, pad) -> net name for every pin the schematic leaves unconnected, named as KiCad
    names them ("unconnected-(U1-ALERT/RDY-Pad2)"), from the schematic's exported netlist."""
    out = HERE / "sensor-board.net"
    subprocess.run([str(KICAD / "bin" / "kicad-cli.exe"), "sch", "export", "netlist", "-o", str(out),
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
    return name if name in ("GND", "+3V3") else "/" + name


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


class Board:
    def __init__(self):
        self.board = pcbnew.BOARD()
        self.board.SetCopperLayerCount(2)
        self.nets = {}
        self.fps = {}
        # A 0.03 mm margin: enough for grid rounding, and small enough that a 0.2 mm track can still
        # reach the middle pins of a 0.5 mm-pitch package (the real gap there is 0.225 mm).
        self.router = Router(W, H, clearance, track=TRACK, via_d=VIA_D, margin=0.03)
        self.pad_items = {}          # (ref, pad number) -> Item
        self.unused = unused_pins()
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
        r = 3.0
        lines = [((r, 0), (W - r, 0)), ((W, r), (W, H - r)), ((W - r, H), (r, H)), ((0, H - r), (0, r))]
        for (x1, y1), (x2, y2) in lines:
            s = pcbnew.PCB_SHAPE(self.board)
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(V(x1, y1))
            s.SetEnd(V(x2, y2))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.board.Add(s)
        k = r * (1 - 0.5 ** 0.5)      # the arc's midpoint, inset from the corner
        corners = [((0, r), (k, k), (r, 0)), ((W - r, 0), (W - k, k), (W, r)),
                   ((W, H - r), (W - k, H - k), (W - r, H)), ((r, H), (k, H - k), (0, H - r))]
        for a, m, b in corners:
            s = pcbnew.PCB_SHAPE(self.board)
            s.SetShape(pcbnew.SHAPE_T_ARC)
            s.SetArcGeometry(V(*a), V(*m), V(*b))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.board.Add(s)

    def place(self):
        for part in design.PARTS:
            x, y, rot, side = PLACE[part.ref]
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
                if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                    continue
                item = pad_item(pad, netname)
                self.router.items.append(item)
                self.pad_items[(part.ref, num)] = item
            self.fps[part.ref] = fp
        for hx, hy in HOLES:
            self.router.holes.append((hx, hy, 2.75 / 2 + 0.5))
        # Nothing but the ignition side inside the tach's isolation zone, and nothing but the
        # NMEA 2000 network's side inside its zone (both set after placement, from the parts that
        # are actually there).
        self.router.keepouts.append((*self.hv_zone(), lambda n: n in HV))
        self.router.keepouts.append((*self.n2k_zone(), lambda n: n in N2K))
        # ...and the network side's own tracks stay inside their routing area, so every one of
        # them is at least 2 mm from the Pi's ground pour outside it.
        rx0, ry0, rx1, _ = self.n2k_route()
        not_n2k = lambda n: n not in N2K
        self.router.keepouts += [(0.0, 0.0, W, ry0, not_n2k), (0.0, ry0, rx0, H, not_n2k), (rx1, ry0, W, H, not_n2k)]

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
        t = pcbnew.PCB_TRACK(self.board)
        t.SetStart(V(*a))
        t.SetEnd(V(*b))
        t.SetWidth(mm(width))
        t.SetLayer(pcbnew.F_Cu if layer == TOP else pcbnew.B_Cu)
        t.SetNet(self.net(net))
        self.board.Add(t)
        self.router.items.append(Item(net, [layer], "seg", x1=a[0], y1=a[1], x2=b[0], y2=b[1], w=width))

    def add_via(self, net, x, y):
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
        self.router.items.append(Item(net, [TOP, BOTTOM], "circle", x=x, y=y, r=VIA_D / 2))

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
        self.router.track = width
        self.router.layer_cost = (3, 1) if net in PREFER_BOTTOM else (1, 3)
        if len(members) < 2:
            return
        # Grow a tree: start at the first pad, then join the nearest remaining pad each time.
        first = self.pad_items[members[0]]
        tree_cells = list(self.cells_of(first))
        todo = [self.pad_items[m] for m in members[1:]]
        while todo:
            def centre(it):
                return it.geo["x"], it.geo["y"]
            tx = sum(c[0] for c in tree_cells) / len(tree_cells) * self.router.res
            ty = sum(c[1] for c in tree_cells) / len(tree_cells) * self.router.res
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
            self.commit(net, path, width)
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
            res = self.router.res

            def ok(i, j, layer):
                if layer != TOP or via_maps[TOP][i, j] or via_maps[BOTTOM][i, j]:
                    return False
                x, y = i * res, j * res
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
            self.add_via("GND", end[0] * res, end[1] * res)

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
        pts = [(inset, inset), (W - inset, inset), (W - inset, H - inset), (nx1, H - inset), (nx1, ny0), (inset, ny0)]
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
            else:                                            # a row of pins along the bottom edge
                for (px, py), name in zip(pts, names):
                    text(name, px, py - INSET, 0.8)
        # Which connector is which, beside its labels.
        text("J1", 12.15, 11.6, 0.9, rot=90)
        text("J3", 52.85, 29.4, 0.9, rot=90)
        text("J2", 40.2, 63.85, 0.9, just="right")
        text("J5 NMEA 2000", 15.6, 62.6, 0.9)
        text("J6", 58.7, 35.9, 0.9, just="right")
        # The rules the pin labels can't carry, in plain words. (Rev 1.0-1.1 had a numbered pinout
        # here instead, which said less than the labels now do.)
        # Lines beside J6 stop short of its pin labels; the long one goes below them.
        lines = ["WIRING", "FUEL TRIM OIL TEMP: S terminal", "IGN: any gauge's I terminal",
                 "Taps: 10k at the gauge end", "BAT+: +12V always on, 1A fuse",
                 "GND: gauge G / battery -", "TACH: gray wire, coil TACH",
                 "NMEA: drop cable, shield unused", "LIGHTS: J6 to the LED board", "",
                 "PROBES: red 3V3, yellow DATA, black GND",
                 "", f"{design.TITLE} r{design.REVISION}", "github.com/erod998/boatmfd"]
        below_j6 = pcbnew.ToMM(self.fps["J6"].GetCourtyard(pcbnew.F_CrtYd).BBox().GetBottom()) - OY + 1.0
        y = 35.0
        for body in lines:
            if body.startswith("PROBES"):   # the one line too long to pass J6's labels
                y = max(y, below_j6)
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
        self.titles()
        self.outline()
        self.place()
        # J6's ground pins sit between signal pins 1.25 mm apart: their vias go in first, before
        # the signals leaving the pins beside them close off the way out.
        self.stitch_gnd(refs={"J6"})
        for net in ORDER:
            self.route_net(net)
            print(f"  routed {net}", flush=True)
        self.stitch_gnd()
        self.pours()
        self.silk()
        return self.failed

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
    tpl = Path(r"C:\Users\erod9\AppData\Local\Programs\KiCad\10.0\share\kicad\template\RaspberryPi-HAT\RaspberryPi-HAT.kicad_pro")
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
    # PCBWay's standard 2-layer limits (pcbway.com/capabilities.html), with margin where it is free:
    # tracks/gaps 0.1 mm, annular ring 0.15 mm, hole-to-hole 16 mil, copper to a routed edge 0.25 mm,
    # silkscreen 0.8 mm tall with a 0.15 mm stroke.
    rules.update({"min_clearance": 0.15, "min_track_width": 0.15, "min_via_diameter": 0.6, "min_via_annular_width": 0.15,
                  "min_through_hole_diameter": 0.3, "min_copper_edge_clearance": 0.3, "min_hole_to_hole": 0.41,
                  "min_hole_clearance": 0.25, "min_text_height": 0.8, "min_text_thickness": 0.15})
    pro["sheets"] = [[schematic.ROOT, "Root"]]
    Path(path).write_text(json.dumps(pro, indent=2), encoding="utf-8")


if __name__ == "__main__":
    b = Board()
    failed = b.build()
    out = HERE / "sensor-board.kicad_pcb"
    b.save(out)
    write_project(HERE / "sensor-board.kicad_pro")
    write_rules(HERE / "sensor-board.kicad_dru")
    print("wrote", out.name)
    if failed:
        print("UNROUTED:", failed)
        sys.exit(1)
