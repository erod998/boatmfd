"""Lays out and routes sensor-board.kicad_pcb from design.py. Run with KiCad's bundled Python.

Placement is a table below; routing is router.py under the design rules:
  * 0.2 mm tracks and clearance (the ADS1115's 0.5 mm pin pitch needs no finer);
  * 3 mm between the tach's ignition side and everything else, 0.6 mm inside its resistor chain;
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
W, H = 65.0, 56.0                    # the Raspberry Pi HAT outline (KiCad's own HAT template)
TRACK, HV_TRACK, VIA_D, VIA_DRILL = 0.2, 0.25, 0.6, 0.3
HV = set(design.TACH_HV_NETS)
SERIES = {"TACH_IN", "TACH_A", "TACH_B"}   # the nets the ignition spike is divided across

# ---------------------------------------------------------------- placement (board mm, degrees CCW)
LANE_Y = [11.5, 16.5, 21.5, 26.5, 31.5, 36.5]    # one per channel, top to bottom
PLACE = {
    "J4": (8.37, 4.77, -90, "B"),                 # the Pi header, underneath (as KiCad's HAT template)
    "H1": (3.5, 3.5, 0, "F"), "H2": (61.5, 3.5, 0, "F"), "H3": (3.5, 52.5, 0, "F"), "H4": (61.5, 52.5, 0, "F"),
    # Helm plug on the left edge (over the Pi's SD-card end): plug faces out, pin 1 at the top.
    "J1": (8.7, 14.665, -90, "F"),
    # Converters and their decoupling.
    "U1": (38.8, 18.0, 0, "F"), "U2": (38.8, 29.0, 0, "F"),
    "C7": (43.6, 17.6, -90, "F"), "C8": (45.7, 17.6, -90, "F"),
    "C9": (43.6, 28.6, -90, "F"), "C10": (45.7, 28.6, -90, "F"),
    # Tach, bottom right. The ignition side (left of U3) is kept 3 mm from everything else.
    "J2": (42.0, 47.3, 0, "F"),
    "R19": (36.0, 41.5, 90, "F"), "R20": (38.8, 41.5, -90, "F"), "R21": (41.6, 41.5, 90, "F"),
    "D7": (44.4, 41.5, -90, "F"), "C11": (47.3, 41.5, -90, "F"),
    "U3": (55.5, 41.5, 0, "F"),
    "R23": (56.5, 35.8, 0, "F"), "R22": (60.0, 35.8, 0, "F"),
    # Probes on the right edge, and their protection.
    "J3": (56.3, 26.0, 90, "F"),
    "D8": (49.8, 22.2, 180, "F"), "R25": (51.4, 18.8, 180, "F"), "R24": (51.4, 15.6, 180, "F"),
}
for i, y in enumerate(LANE_Y):
    PLACE[f"R{1 + i}"] = (14.8, y, 0, "F")            # top resistor: IN left, DIV right
    PLACE[f"R{7 + i}"] = (18.6, y + 1.7, -90, "F")    # divider bottom: DIV up, GND down
    PLACE[f"D{1 + i}"] = (22.0, y - 0.94, -90, "F")   # clamp: COM down onto the lane, GND/3V3 up
    PLACE[f"R{13 + i}"] = (25.8, y, 0, "F")           # series: DIV left, ADC right
    PLACE[f"C{1 + i}"] = (29.2, y + 1.7, -90, "F")    # filter: ADC up, GND down

# Routing order: the constrained nets first.
ORDER = (["TACH_IN", "TACH_A", "TACH_B", "TACH_LED", "TACH_GND"] +
         [f"{n}_{s}" for n, *_ in design.CHANNELS for s in ("IN", "DIV", "ADC")] +
         ["TACH_OUT", "TACH_GPIO", "OW_EXT", "OW", "SDA", "SCL", "+3V3"])


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
        for hx, hy in [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]:
            self.router.holes.append((hx, hy, 2.75 / 2 + 0.5))
        # Nothing but the ignition side inside the tach's isolation zone (set after placement,
        # from the parts that are actually there).
        self.router.keepouts.append((*self.hv_zone(), lambda n: n in HV))

    def hv_zone(self, grow=3.0):
        xs, ys = [], []
        for (ref, num), it in self.pad_items.items():
            if it.net in HV:
                g = it.geo
                hw, hh = (g["hw"], g["hh"]) if it.kind == "rect" else (g["r"], g["r"])
                xs += [g["x"] - hw, g["x"] + hw]
                ys += [g["y"] - hh, g["y"] + hh]
        return (max(0.0, min(xs) - grow), max(0.0, min(ys) - grow), min(W, max(xs) + grow), H)

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
        width = HV_TRACK if net in HV else TRACK
        self.router.track = width
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

    def stitch_gnd(self):
        """A via beside every surface-mount GND pad, into the bottom plane."""
        self.router.track = TRACK
        for (ref, num), item in self.pad_items.items():
            if item.net != "GND" or item.layers != {TOP}:
                continue
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
        pts = [(inset, inset), (W - inset, inset), (W - inset, H - inset), (inset, H - inset)]
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
        pcbnew.ZONE_FILLER(self.board).Fill(self.board.Zones())

    def silk(self):
        def text(s, x, y, size=1.0, layer=pcbnew.F_SilkS, rot=0, just=None):
            t = pcbnew.PCB_TEXT(self.board)
            t.SetText(s)
            t.SetPosition(V(x, y))
            t.SetLayer(layer)
            t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
            t.SetTextThickness(mm(size * 0.15))
            t.SetTextAngleDegrees(rot)
            if just == "left":
                t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
            elif just == "right":
                t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_RIGHT)
            self.board.Add(t)
        # Every connector's pinout in one block in the free bottom-left corner; there is no room
        # beside the connectors without landing on pads.
        lines = [("J1 HELM", 41.6), ("1 FUEL S    5 OIL S", 42.8), ("2 TRIM S    6 SPARE", 44.0),
                 ("3 BATT+     7 GAUGE G", 45.2), ("4 GAUGE I   8 BATT-", 46.4),
                 ("J2 DELCO EST TACH", 47.8), ("1 GRAY WIRE  2 GND", 49.0),
                 ("J3 1 3V3  2 DATA  3 GND", 50.4),
                 (f"{design.TITLE} r{design.REVISION}", 51.8), ("github.com/erod998/boatmfd", 53.0)]
        for body, y in lines:
            text(body, 12.4, y, 0.8, just="left")

    # ---------------------------------------------------------------- all of it
    def titles(self):
        """The title block, for the PDFs; and the place/drill origin at the board's bottom-left
        corner, so the Gerbers, drill file and pick-and-place file all share it."""
        tb = pcbnew.TITLE_BLOCK()
        tb.SetTitle(design.TITLE)
        tb.SetRevision(design.REVISION)
        tb.SetDate(design.DATE)
        tb.SetComment(0, "Passive taps on the existing gauges; Delco EST tach")
        tb.SetComment(1, "Generated from design.py by board.py; edit those, not this file")
        self.board.SetTitleBlock(tb)
        self.board.GetDesignSettings().SetAuxOrigin(V(0, H))

    def build(self):
        self.titles()
        self.outline()
        self.place()
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
""", encoding="utf-8")


def write_project(path):
    tpl = Path(r"C:\Users\erod9\AppData\Local\Programs\KiCad\10.0\share\kicad\template\RaspberryPi-HAT\RaspberryPi-HAT.kicad_pro")
    pro = json.loads(tpl.read_text(encoding="utf-8"))
    pro["meta"]["filename"] = Path(path).name
    default = dict(pro["net_settings"]["classes"][0])
    default.update({"name": "Default", "clearance": 0.2, "track_width": TRACK, "via_diameter": VIA_D, "via_drill": VIA_DRILL})
    hv = dict(default, name="TACH_HV", track_width=HV_TRACK, priority=0)
    pro["net_settings"]["classes"] = [default, hv]
    pro["net_settings"]["netclass_patterns"] = [{"netclass": "TACH_HV", "pattern": pcb_net_name(n)}
                                                for n in design.TACH_HV_NETS]
    rules = pro["board"]["design_settings"]["rules"]
    rules.update({"min_clearance": 0.15, "min_track_width": 0.15, "min_via_diameter": 0.5, "min_via_annular_width": 0.1,
                  "min_through_hole_diameter": 0.3, "min_copper_edge_clearance": 0.3, "min_hole_to_hole": 0.25,
                  "min_hole_clearance": 0.25})
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
