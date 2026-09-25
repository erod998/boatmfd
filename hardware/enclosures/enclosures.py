"""Printable enclosures for the boat MFD's electronics, in PETG. There are two boxes:

  * the Pi box: the Raspberry Pi 5, the M.2 SSD board over its Active Cooler, and the sensor
    board on top;
  * the LED box: the LED board.

They sit behind the dash, but a boat is humid. A sealed box traps damp air, which then
condenses on the boards as the temperature swings. So these boxes are vented but drip-proof:
  * the lid has no openings, and it overhangs the walls so drips fall clear of the seam;
  * the vents are louvres: slots in the side walls under a hood that slopes down and out;
  * there's a hood over every connector window too, and a drain hole in each floor corner.
Conformal-coat the boards as well. The coating is what actually keeps condensation off them.

Each box has two parts:
  * a base: the walls, with a window for each connector's plug, standoffs for the boards, and
    ears to screw it down;
  * a flat lid, screwed to lugs on the outside of the walls. The screws go outside because the
    boards fill the boxes.

Run it with FreeCAD 1.0's command-line Python, from this directory:
    "%LOCALAPPDATA%\\Programs\\FreeCAD 1.0\\bin\\freecadcmd.exe" enclosures.py
It writes to out/:
  * each part as STEP (opens in Fusion 360) and as 3MF (for Bambu Studio), already turned the
    way up it prints;
  * each whole box with its boards, as a STEP for checking the fit;
  * a list of anything on the boards that a box runs into.
The boards come from the KiCad files (this script runs kicad-cli). The Pi 5 comes from Raspberry
Pi's own model, which goes in models/ (see README.md); without it, the Pi is left out of the check.

Coordinates, in mm:
  * Pi box: the Pi 5's own frame. The origin is the board's corner at the USB-C end away from the
    GPIO header. x runs along the USB-C/HDMI edge towards the USB ports, y towards the GPIO
    header, and z = 0 is the underside of the Pi's board.
  * LED box: the LED board's frame, with the origin at its corner by the pixel outputs and USB-C.
    x runs along the pixel-output edge towards the 12 V input, and z = 0 is the board's
    underside.
"""
import math
import os
import subprocess
import sys

import FreeCAD as App
import Part

V = App.Vector
HERE = os.environ.get("ENCLOSURE_DIR") or os.getcwd()
OUT = os.path.join(HERE, "out")
MODELS = os.path.join(HERE, "models")
HW = os.path.dirname(HERE)
KICAD_CLI = os.environ.get("KICAD_CLI") or os.path.expandvars(
    r"%LOCALAPPDATA%\Programs\KiCad\10.0\bin\kicad-cli.exe")

# ---- Measure these on the real parts, then run this again ----
# These are estimates until the boards are stacked in front of you.
PI_STANDOFF = 5.0     # the Pi's board, above the floor
SSD_HAT_Z = 13.8      # the M.2 board's underside, above the Pi's: Waveshare's standoffs over the Active Cooler
SENSOR_Z = 26.0       # the sensor board's underside, above the Pi's: on the M.2 board's stacking header
LED_STANDOFF = 6.0    # the LED board, above the floor (its through-hole pins stick out ~3 mm below)
FUSE_TOP = 22.0       # an ATO fuse's top in its holder, above the LED board's underside (holder 17.5 mm tall)

# ---- The boxes ----
WALL = 2.5            # 5 perimeters of a 0.4 mm nozzle; PETG
FLOOR = 2.5
LID = 2.5
CORNER_R = 4.0        # outside vertical edges
RIM_CLEAR = 4.5       # from the tallest part to the rim; the lid's lip hangs 3 mm below the rim
LID_OVERHANG = 1.5    # the drip edge: the lid runs past the walls all round
LIP_H, LIP_T, LIP_CLEAR = 3.0, 1.6, 0.3   # the lid's locating lip, inside the walls
HOOD = 2.5            # how far a hood stands out from the wall; its underside at 45 degrees prints unsupported
LOUVRE_H, LOUVRE_PITCH = 2.5, 8.0
LUG_W, LUG_OUT, LUG_H = 10.0, 9.0, 8.0    # the lid's screw lugs, on the outside of the walls at the rim
LUG_PILOT, LID_HOLE = 2.7, 3.4            # M3 self-tapping screws (or a 4.0 mm hole for M3 heat-set inserts)
EAR_OUT, EAR_W, EAR_T, EAR_HOLE = 14.0, 16.0, 4.0, 4.5   # screw-down ears at the floor: #8 or M4 screws
DRAIN = 3.0
BRIDGE_MAX = 60.0     # a window longer than this gets thin ribs across it, so its top prints as short bridges:
RIB_W = 1.2           # snip them out after printing


def box(x0, y0, z0, x1, y1, z1):
    return Part.makeBox(x1 - x0, y1 - y0, z1 - z0, V(x0, y0, z0))


def rounded_box(x0, y0, z0, x1, y1, z1, r):
    b = box(x0, y0, z0, x1, y1, z1)
    if r <= 0:
        return b
    edges = [e for e in b.Edges if abs(e.Vertexes[0].Point.z - e.Vertexes[1].Point.z) > 1e-6]
    return b.makeFillet(r, edges)


def cyl(x, y, z0, z1, d):
    return Part.makeCylinder(d / 2, z1 - z0, V(x, y, z0))


class Box:
    """A base (floor, walls, the lugs the lid screws to) and its lid, around an interior
    rectangle x0..x1, y0..y1 from the floor's top at z_floor to the rim at z_rim.

    Walls are named for the interior edge they stand on: x0, x1, y0, y1. Along a wall, 'a' is the
    other plan coordinate (y for the x walls, x for the y walls); 'u' is the distance out from the
    wall's outer face."""

    def __init__(self, name, x0, y0, x1, y1, z_floor, z_rim):
        self.name = name
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.z_floor, self.z_rim = z_floor, z_rim
        self.adds, self.cuts = [], []
        self.lugs = []                 # (wall, a): screw centres for the lid
        self.windows = []              # for the check that the lid's lip clears them
        self.keep_clear = []           # (label, shape): parts of the base the boards must not touch
        self.ribs = []                 # break-away bridge supports across the long windows

    # -- mapping from a wall's local (u, a, z) to the box's (x, y, z)
    def at(self, wall, u, a, z):
        if wall == "x0":
            return V(self.x0 - WALL - u, a, z)
        if wall == "x1":
            return V(self.x1 + WALL + u, a, z)
        if wall == "y0":
            return V(a, self.y0 - WALL - u, z)
        return V(a, self.y1 + WALL + u, z)

    def local_box(self, wall, u0, u1, a0, a1, z0, z1):
        p, q = self.at(wall, u0, a0, z0), self.at(wall, u1, a1, z1)
        return box(min(p.x, q.x), min(p.y, q.y), z0, max(p.x, q.x), max(p.y, q.y), z1)

    def local_prism(self, wall, profile, a0, a1):
        """A prism: a (u, z) profile, run along the wall from a0 to a1."""
        pts = [self.at(wall, u, a0, z) for u, z in profile] + [self.at(wall, profile[0][0], a0, profile[0][1])]
        face = Part.Face(Part.makePolygon(pts))
        d = self.at(wall, 0, a1, 0) - self.at(wall, 0, a0, 0)
        return face.extrude(d)

    # -- features
    def window(self, wall, a0, a1, z0, z1, hood=True):
        """A hole through the wall for a plug, with a hood over it outside."""
        self.cuts.append(self.local_box(wall, -WALL - 0.6, 0.6, a0, a1, z0, z1))
        self.windows.append((wall, a0, a1, z0, z1))
        spans = math.ceil((a1 - a0) / BRIDGE_MAX)
        for k in range(1, spans):
            a = a0 + (a1 - a0) * k / spans
            self.ribs.append(self.local_box(wall, -WALL, 0.0, a - RIB_W / 2, a + RIB_W / 2, z0 - 0.01, z1 + 0.01))
        if hood and z1 + 2 * HOOD <= self.z_rim - 0.5:     # higher up, the lid's overhang covers it
            self.hood(wall, a0 - 1.0, a1 + 1.0, z1)

    def hood(self, wall, a0, a1, z):
        """A ridge on the outside of the wall above z: 45 degrees underneath, sloping down and
        out on top, so drips run off it rather than into what's below."""
        self.adds.append(self.local_prism(wall, [(-0.01, z), (HOOD, z + HOOD), (-0.01, z + 2 * HOOD)], a0, a1))

    def louvres(self, wall, a0, a1, z0, z1):
        """Vent slots, each under a hood, filling the wall between z0 and z1."""
        z = z0
        while z + LOUVRE_H + 2 * HOOD <= z1 + 1e-6:
            self.window(wall, a0, a1, z, z + LOUVRE_H)
            z += LOUVRE_PITCH

    def lug(self, wall, a):
        """A block on the outside of the wall at the rim, 45 degrees underneath, with a pilot
        hole for the lid's screw."""
        top = self.z_rim
        self.adds.append(self.local_box(wall, -0.01, LUG_OUT, a - LUG_W / 2, a + LUG_W / 2, top - LUG_H, top))
        self.adds.append(self.local_prism(wall, [(-0.01, top - LUG_H), (LUG_OUT, top - LUG_H), (-0.01, top - LUG_H - LUG_OUT)],
                                          a - LUG_W / 2, a + LUG_W / 2))
        c = self.at(wall, LUG_OUT / 2 + 0.5, a, 0)
        self.cuts.append(cyl(c.x, c.y, top - LUG_H - 2, top + 1, LUG_PILOT))
        self.lugs.append((wall, a))

    def ear(self, wall, a):
        """A tab at the floor to screw the box down through."""
        z0 = self.z_floor - FLOOR
        self.adds.append(self.local_box(wall, -0.01, EAR_OUT, a - EAR_W / 2, a + EAR_W / 2, z0, z0 + EAR_T))
        c = self.at(wall, EAR_OUT - 6.5, a, 0)
        self.cuts.append(cyl(c.x, c.y, z0 - 1, z0 + EAR_T + 1, EAR_HOLE))

    def post(self, x, y, top, d=6.0, pilot=None, through=None, pocket=None, label="post"):
        """A standoff from the floor up to `top`. pilot: a blind hole from the top (self-tapping
        screw); through: a hole all the way through the floor, and a round pocket `pocket` wide
        from underneath for a screw head or nut."""
        shape = cyl(x, y, self.z_floor - 0.01, top, d)
        self.adds.append(shape)
        self.keep_clear.append((label, cyl(x, y, self.z_floor, top - 0.05, d)))
        if pilot:
            self.cuts.append(cyl(x, y, top - min(8.0, top - self.z_floor + FLOOR - 1.0), top + 1, pilot))
        if through:
            self.cuts.append(cyl(x, y, self.z_floor - FLOOR - 1, top + 1, through))
            if pocket:
                self.cuts.append(cyl(x, y, self.z_floor - FLOOR - 1, self.z_floor - FLOOR + 2.2, pocket))

    def drain(self, x, y):
        self.cuts.append(cyl(x, y, self.z_floor - FLOOR - 1, self.z_floor + 1, DRAIN))

    # -- the parts
    def base(self):
        W = WALL
        outer = rounded_box(self.x0 - W, self.y0 - W, self.z_floor - FLOOR, self.x1 + W, self.y1 + W, self.z_rim, CORNER_R)
        inner = rounded_box(self.x0, self.y0, self.z_floor, self.x1, self.y1, self.z_rim + 1, max(CORNER_R - W, 0.5))
        shape = outer.cut(inner)
        for a in self.adds:
            shape = shape.fuse(a)
        for c in self.cuts:
            shape = shape.cut(c)
        for r in self.ribs:
            shape = shape.fuse(r)
        return shape.removeSplitter()

    def walls_only(self):
        W = WALL
        outer = rounded_box(self.x0 - W, self.y0 - W, self.z_floor - FLOOR, self.x1 + W, self.y1 + W, self.z_rim, CORNER_R)
        inner = rounded_box(self.x0, self.y0, self.z_floor, self.x1, self.y1, self.z_rim + 1, max(CORNER_R - W, 0.5))
        shape = outer.cut(inner)
        for c in self.cuts:
            shape = shape.cut(c)
        return shape

    def lid(self):
        W, O = WALL, LID_OVERHANG
        z = self.z_rim
        plate = rounded_box(self.x0 - W - O, self.y0 - W - O, z, self.x1 + W + O, self.y1 + W + O, z + LID, CORNER_R + O)
        for wall, a in self.lugs:
            plate = plate.fuse(self.local_box(wall, -0.5, LUG_OUT + O, a - LUG_W / 2 - O, a + LUG_W / 2 + O, z, z + LID))
        c = LIP_CLEAR
        lip_out = rounded_box(self.x0 + c, self.y0 + c, z - LIP_H, self.x1 - c, self.y1 - c, z + 0.01, max(CORNER_R - W - c, 0.5))
        lip_in = rounded_box(self.x0 + c + LIP_T, self.y0 + c + LIP_T, z - LIP_H - 1, self.x1 - c - LIP_T, self.y1 - c - LIP_T, z + 0.02, 0.5)
        shape = plate.fuse(lip_out.cut(lip_in))
        for wall, a in self.lugs:
            h = self.at(wall, LUG_OUT / 2 + 0.5, a, 0)
            shape = shape.cut(cyl(h.x, h.y, z - 1, z + LID + 1, LID_HOLE))
        self.keep_clear.append(("lid lip", lip_out.cut(lip_in)))
        self.keep_clear.append(("lid", box(self.x0 - W, self.y0 - W, z, self.x1 + W, self.y1 + W, z + LID)))
        return shape.removeSplitter()

    def check_lip(self):
        """The lid's lip hangs inside the walls: no window may reach up into it."""
        bad = [w for w in self.windows if w[4] > self.z_rim - LIP_H - 0.3]
        for w in bad:
            print(f"  WARNING {self.name}: the window on {w[0]} at {w[1]:.1f}..{w[2]:.1f} reaches {w[4]:.1f}, into the lid's lip")
        return not bad


# ============================================================ the Pi box
PI_W, PI_H = 85.0, 56.0
PI_HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]
# The sensor board, in the Pi's frame (KiCad x - 100, 106 - KiCad y): 65 x 93.6 mm. It overhangs
# the Pi by 20 mm past the USB-C/HDMI edge (J5 NMEA 2000, J2 tach), and by 17.6 mm past the GPIO edge
# (the tabs: J7 5 V in, J6 RJ45 to the LED board). J1 (the gauge taps) runs along the x = 0 edge.
SENSOR = dict(x0=0.0, x1=65.0, y0=-20.0, y1=73.6)
SENSOR_H5 = (61.5, -16.5)          # its fifth mounting hole, over nothing of the Pi's
SENS_J1 = (7.01, 49.09)            # along y, facing -x
SENS_J5_J2 = (1.25, 53.46)         # along x, facing -y (J5 1.25..31.88, J2 34.25..53.46)
SENS_J6 = (38.35, 54.75)           # the RJ45, along x, facing +y
RJ45_H = 12.7                      # Amphenol 54602-908LF, above the board
MC_H = 7.55                        # Phoenix MC 1,5 GF headers, above the board


def pi_box():
    st = SENSOR_Z + 1.6            # the sensor board's top
    top_parts = st + RJ45_H
    # Interior. x0 leaves 6 mm past the Pi's x = 0 edge for the M.2 board's ribbon cable, which
    # loops out from the Pi's PCIe connector there. x1 clears the USB and Ethernet jacks, which
    # stand 3 mm proud of the board; y0 and y1 clear the sensor board.
    b = Box("pi-box", -6.0, SENSOR["y0"] - 0.6, 88.6, SENSOR["y1"] + 0.6, -PI_STANDOFF, top_parts + RIM_CLEAR)

    # The Pi: posts under its four holes. An M2.5 screw from underneath goes into the standoffs
    # above; the pocket takes its head, or a nut on a standoff's male thread.
    for x, y in PI_HOLES:
        b.post(x, y, 0.0, d=6.0, through=2.8, pocket=5.6, label="Pi post")
    # The sensor board's overhang: a post under its fifth hole (M2.5 self-tapping screw from
    # above), and a rest under the J5 end. Plugging in J5 and J2 pushes on that edge.
    b.post(*SENSOR_H5, SENSOR_Z, d=6.0, pilot=2.2, label="sensor post")
    b.post(6.0, -15.5, SENSOR_Z, d=5.0, label="sensor rest")

    # USB-C and both micro-HDMI: one opening. Raspberry Pi's model leaves out the HDMI sockets,
    # and the plugs' overmoulds need room anyway. The plugs reach 20 mm in, under the sensor board.
    b.window("y0", 3.5, 46.5, -1.5, 9.5)
    # J5 (NMEA 2000) and J2 (tach), side by side along the same edge.
    b.window("y0", SENS_J5_J2[0] - 1.5, SENS_J5_J2[1] + 1.5, st - 2.5, st + MC_H + 4.0)
    # J1, the gauge taps; the plug reaches the 6 mm to its header through this.
    b.window("x0", SENS_J1[0] - 3.0, SENS_J1[1] + 3.0, st - 3.0, st + MC_H + 5.0)
    # The Pi's USB and Ethernet jacks.
    b.window("x1", 1.3, 55.2, -1.5, 18.5)
    # J6, the RJ45 to the LED board. (J7, the sensor board's own 5 V input, isn't used: the Pi 5
    # takes its power on its USB-C. Drill here if that changes.)
    b.window("y1", SENS_J6[0] - 1.0, SENS_J6[1] + 1.0, st - 1.0, st + RJ45_H + 1.0)

    # Vents around the Active Cooler and the SSD, below the sensor board.
    b.louvres("x0", 8.0, 47.0, -2.5, st - 4.0)
    b.louvres("y1", 3.0, 34.0, -2.5, st - 4.0)
    b.louvres("y1", 68.0, 85.0, -2.5, b.z_rim - LIP_H - 1.0)
    b.louvres("x1", 5.0, 51.0, 21.0, b.z_rim - LIP_H - 1.0)

    # The lid's lugs, clear of the windows; the ears to screw it down.
    for y in (b.y0 + 8.0, b.y1 - 8.0):
        b.lug("x0", y)
        b.lug("x1", y)
    for y in (12.0, 42.0):
        b.ear("x0", y)
        b.ear("x1", y)
    for x, y in ((b.x0 + 2.5, b.y0 + 2.5), (b.x1 - 2.5, b.y0 + 2.5), (b.x0 + 2.5, b.y1 - 2.5), (b.x1 - 2.5, b.y1 - 2.5)):
        b.drain(x, y)
    return b


# ============================================================ the LED box
LED_W, LED_H = 178.0, 94.0
LED_HOLES = [(3.5, 3.5), (174.5, 3.5), (174.5, 90.5), (3.5, 90.5), (51.8, 42.8), (120.0, 42.8)]
# In this frame (KiCad x - 50, 144 - KiCad y). The zone outputs J3-J6 run along the y = 94 edge
# (plugs from +y), and the addressable outputs J7-J10 along y = 0 (plugs from -y). The 12 V input J2
# is on the x = 178 edge (wires from +x). The RJ45 J1 and the USB-C J11 are on x = 0 (from -x).
LED_ZONES = (7.02, 153.47)
LED_PIXELS = (28.53, 154.60)
LED_J2 = (32.69, 52.83)
LED_J1 = (48.36, 64.75)
LED_J11 = (16.64, 27.36)
J2_H = 20.0                        # METZ CONNECT RT10N02HGLU, above the board (estimate: 10 AWG rising clamp)
MSTB_H = 8.6                       # the 5.08 mm headers, above the board


def led_box():
    top = 1.6
    b = Box("led-box", -0.8, -0.8, LED_W + 0.8, LED_H + 0.8, -LED_STANDOFF, max(FUSE_TOP, top + J2_H) + RIM_CLEAR)
    for i, (x, y) in enumerate(LED_HOLES):
        # M3 self-tapping screws from above. Use nylon ones in the middle two, which are in the
        # 12 V bus.
        b.post(x, y, 0.0, d=7.0, pilot=2.7, label="LED post")
    b.window("y1", LED_ZONES[0] - 1.5, LED_ZONES[1] + 1.5, top - 2.0, top + MSTB_H + 5.0)
    b.window("y0", LED_PIXELS[0] - 1.5, LED_PIXELS[1] + 1.5, top - 2.0, top + MSTB_H + 5.0)
    b.window("x1", LED_J2[0] - 1.5, LED_J2[1] + 1.5, top - 2.0, top + J2_H - 2.0)
    b.window("x0", LED_J1[0] - 1.0, LED_J1[1] + 1.0, top - 1.0, top + RJ45_H + 1.5)
    b.window("x0", LED_J11[0] - 3.0, LED_J11[1] + 3.0, top - 1.5, top + 7.0)   # USB-C, for loading firmware
    rim_vent = b.z_rim - LIP_H - 1.0
    b.louvres("x0", 31.5, 45.5, -4.0, rim_vent)
    b.louvres("x0", 67.5, 80.0, -4.0, rim_vent)
    b.louvres("x1", 14.0, 29.0, -4.0, rim_vent)
    b.louvres("x1", 57.0, 80.0, -4.0, rim_vent)
    # The lid's lugs: at the corners of the end walls, and two more along the pixel-output side
    # where its window leaves room. (Along the connector edges they'd hang over the plugs.)
    for y in (b.y0 + 6.0, b.y1 - 6.0):
        b.lug("x0", y)
        b.lug("x1", y)
    for x in (12.0, 167.0):
        b.lug("y0", x)
    for y in (18.0, 76.0):
        b.ear("x0", y)
        b.ear("x1", y)
    for x, y in ((b.x0 + 2.5, b.y0 + 9.0), (b.x1 - 2.5, b.y0 + 9.0), (b.x0 + 2.5, b.y1 - 9.0), (b.x1 - 2.5, b.y1 - 9.0)):
        b.drain(x, y)
    return b


# ============================================================ the boards, to check the fit
def kicad_step(board):
    out = os.path.join(OUT, f"{board}.step")
    pcb = os.path.join(HW, board, f"{board}.kicad_pcb")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(pcb):
        subprocess.run([KICAD_CLI, "pcb", "export", "step", "--subst-models", "--force", "-o", out, pcb],
                       check=True, capture_output=True)
    return Part.read(out)


def pi_stack():
    """The Pi, the M.2 board and the sensor board, as solids, with a label each."""
    solids = []
    model = os.path.join(MODELS, "rpi-5b_no_graphics.step")
    if os.path.exists(model):
        solids += [("Pi 5", s) for s in Part.read(model).Solids]
    else:
        print("  (no models/rpi-5b_no_graphics.step: the Pi itself is left out of the check; see README.md)")
        solids.append(("Pi 5 board", box(0, 0, 0, PI_W, PI_H, 1.3)))
    # The M.2 board, its SSD and the Active Cooler under it, as boxes: no models of them.
    solids.append(("M.2 board", box(0, 0, SSD_HAT_Z, 65, 56, SSD_HAT_Z + 1.6)))
    solids.append(("SSD", box(12, 8, SSD_HAT_Z + 1.6, 54, 30, SSD_HAT_Z + 5.5)))
    solids.append(("Active Cooler", box(20, 10, 1.3, 66, 50, SSD_HAT_Z - 0.3)))
    sensor = kicad_step("sensor-board").copy()
    sensor.translate(V(-100, 106, SENSOR_Z))
    solids += [("sensor board", s) for s in sensor.Solids]
    solids.append(("sensor J6 RJ45", box(SENS_J6[0], 54.7, SENSOR_Z + 1.6, SENS_J6[1], 73.5, SENSOR_Z + 1.6 + RJ45_H)))
    return solids


def led_stack():
    led = kicad_step("led-board").copy()
    led.translate(V(-50, 144, 0))
    solids = [("LED board", s) for s in led.Solids]
    # No 3D models in KiCad's library for these: boxes at their datasheets' sizes.
    fuse_x = [(32.7, 39.8), (69.3, 76.4), (105.9, 113.0), (142.5, 149.6), (32.4, 39.5), (63.9, 71.0), (95.4, 102.5), (126.9, 134.0)]
    for i, (x0, x1) in enumerate(fuse_x):
        y0, y1 = (42.75, 63.84) if i < 4 else (21.75, 42.84)
        solids.append((f"F{i + 1} + fuse", box(x0, y0, 1.6, x1, y1, FUSE_TOP)))
    solids.append(("J2 12 V terminal", box(163.62, LED_J2[0], 1.6, 177.22, LED_J2[1], 1.6 + J2_H)))
    solids.append(("J1 RJ45", box(0.08, LED_J1[0], 1.6, 18.91, LED_J1[1], 1.6 + RJ45_H)))
    return solids


def check(b, solids, base, lid):
    """Anything of the boards inside the base's walls, floor, posts or the lid."""
    parts = [("walls", b.walls_only())] + b.keep_clear
    problems = []
    for label, s in solids:
        sb = s.BoundBox
        for plabel, p in parts:
            if not sb.intersect(p.BoundBox):
                continue
            try:
                v = s.common(p).Volume
            except Exception:
                continue
            if v > 0.05:
                problems.append(f"{label} x {sb.XMin:.1f}..{sb.XMax:.1f} y {sb.YMin:.1f}..{sb.YMax:.1f} "
                                f"z {sb.ZMin:.1f}..{sb.ZMax:.1f} runs into the {plabel} ({v:.2f} mm3)")
    return problems


# ============================================================ output
def export(shape, name, flip=False):
    """STEP where it sits in the box (Fusion 360); STL and 3MF turned the way it prints (Bambu
    Studio), standing on z = 0."""
    shape.exportStep(os.path.join(OUT, f"{name}.step"))
    p = shape.copy()
    if flip:
        p.rotate(V(0, 0, 0), V(1, 0, 0), 180)
    bb = p.BoundBox
    p.translate(V(-bb.XMin, -bb.YMin, -bb.ZMin))
    import MeshPart
    mesh = MeshPart.meshFromShape(Shape=p, LinearDeflection=0.02, AngularDeflection=0.15, Relative=False)
    mesh.write(os.path.join(OUT, f"{name}.stl"))
    try:
        mesh.write(os.path.join(OUT, f"{name}.3mf"))
    except Exception as exc:
        print(f"  (no 3MF: {exc}; the STL prints the same)")
    return p


def main():
    os.makedirs(OUT, exist_ok=True)
    ok = True
    for make, stack in ((pi_box, pi_stack), (led_box, led_stack)):
        b = make()
        print(f"{b.name}: interior {b.x1 - b.x0:.1f} x {b.y1 - b.y0:.1f} x {b.z_rim - b.z_floor:.1f} mm, "
              f"outside {b.x1 - b.x0 + 2 * WALL:.1f} x {b.y1 - b.y0 + 2 * WALL:.1f} x "
              f"{b.z_rim - b.z_floor + FLOOR + LID:.1f} mm (without ears and lugs)")
        base, lid = b.base(), b.lid()
        ok &= b.check_lip()
        print(f"  base {base.Volume / 1000:.1f} cm3, lid {lid.Volume / 1000:.1f} cm3, solids {len(base.Solids)}+{len(lid.Solids)}")
        export(base, f"{b.name}-base")
        export(lid, f"{b.name}-lid", flip=True)
        solids = stack()
        problems = check(b, solids, base, lid)
        for p in problems:
            print("  CLASH:", p)
        ok &= not problems
        if not problems:
            print("  fit: nothing of the boards runs into the box")
        Part.makeCompound([base, lid] + [s for _, s in solids]).exportStep(os.path.join(OUT, f"{b.name}-assembly.step"))
    print("ok" if ok else "PROBLEMS: see above")
    return 0 if ok else 1


main()
