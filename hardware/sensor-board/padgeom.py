"""Pad geometry straight from KiCad's footprint files, without KiCad.

board.py routes from pcbnew's own pad geometry when it runs under KiCad's Python. This module
gives the same answer from the .kicad_mod text, so the placement and routing can also be tried
(`python board.py --dry-run`) with only numpy -- and board.py, under KiCad, checks that the two
agree pad for pad before it trusts a route.

Conventions, as KiCad applies them:
  * a footprint turned by `rot` degrees moves a local point (x, y) to
    (x cos r + y sin r, -x sin r + y cos r): counter-clockwise on screen, y pointing down;
  * a footprint flipped to the bottom has its local y mirrored first;
  * a pad's own angle adds to the footprint's.
"""
import math
from dataclasses import dataclass
from pathlib import Path

from sexpr import find, find_all, parse


@dataclass
class Pad:
    number: str
    x: float              # board mm, centre
    y: float
    hw: float             # half width and height of the axis-aligned outline
    hh: float
    shape: str            # "rect" (rect, roundrect, oval, custom, trapezoid) or "circle"
    corner: float         # corner radius of a rounded rectangle / oval
    top: bool             # copper on F.Cu / B.Cu
    bottom: bool
    drill: float = 0.0    # plated hole, 0 for SMD
    npth: bool = False


def _num(v):
    return float(v)


def _unq(a):
    return a[1:-1] if isinstance(a, str) and a.startswith('"') else a


def rotate(x, y, deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return x * c + y * s, -x * s + y * c


class Library:
    def __init__(self, kicad_footprints, project_libs):
        """kicad_footprints: KiCad's footprints folder; project_libs: {nickname: folder}."""
        self.kicad = Path(kicad_footprints)
        self.project = {k: Path(v) for k, v in project_libs.items()}
        self._cache = {}

    def load(self, fpid):
        if fpid not in self._cache:
            lib, name = fpid.split(":")
            folder = self.project.get(lib, self.kicad / f"{lib}.pretty")
            self._cache[fpid] = parse((folder / f"{name}.kicad_mod").read_text(encoding="utf-8"))
        return self._cache[fpid]

    def courtyard(self, fpid):
        """The F.CrtYd outline's bounding box, in footprint coordinates."""
        xs, ys = [], []
        for kind in ("fp_line", "fp_rect", "fp_arc", "fp_poly", "fp_circle"):
            for g in find_all(self.load(fpid), kind):
                layer = find(g, "layer")
                if layer is None or _unq(layer[1]) != "F.CrtYd":
                    continue
                pts = [c for c in g if isinstance(c, list) and c and c[0] in ("start", "end", "mid", "center")]
                pl = find(g, "pts")
                if pl is not None:
                    pts += find_all(pl, "xy")
                if kind == "fp_circle":
                    c, e = find(g, "center"), find(g, "end")
                    r = math.hypot(_num(e[1]) - _num(c[1]), _num(e[2]) - _num(c[2]))
                    xs += [_num(c[1]) - r, _num(c[1]) + r]
                    ys += [_num(c[2]) - r, _num(c[2]) + r]
                for p in pts:
                    xs.append(_num(p[1]))
                    ys.append(_num(p[2]))
        return (min(xs), min(ys), max(xs), max(ys)) if xs else None

    def pads(self, fpid, x, y, rot, flip=False):
        """Every copper pad of the footprint placed at (x, y), turned `rot` degrees, as Pads."""
        out = []
        for pad in find_all(self.load(fpid), "pad"):
            number, kind, shape = _unq(pad[1]), pad[2], pad[3]
            at = find(pad, "at")
            lx, ly = _num(at[1]), _num(at[2])
            pang = _num(at[3]) if len(at) > 3 else 0.0
            size = find(pad, "size")
            w, h = _num(size[1]), _num(size[2])
            layers = [_unq(l) for l in find(pad, "layers")[1:]]
            top = any(l in ("F.Cu", "*.Cu") for l in layers)
            bottom = any(l in ("B.Cu", "*.Cu") for l in layers)
            if not (top or bottom) and kind != "np_thru_hole":
                continue      # paste-only apertures
            if flip:
                ly, pang = -ly, -pang
                top, bottom = bottom, top
            bx, by = rotate(lx, ly, rot)
            ang = (pang + rot) % 360
            drill = 0.0
            d = find(pad, "drill")
            if d is not None:
                vals = [a for a in d[1:] if not isinstance(a, list) and a != "oval"]
                drill = _num(vals[0]) if vals else 0.0
            if shape == "custom":
                # The whole pad: its anchor and every primitive, as one bounding box (the router
                # must see all of it, not just the anchor).
                pts = [(-w / 2, -h / 2), (w / 2, h / 2)]
                prims = find(pad, "primitives")
                for g in (prims[1:] if prims else []):
                    if not isinstance(g, list):
                        continue
                    for c in g:
                        if isinstance(c, list) and c and c[0] in ("start", "end", "mid", "center"):
                            pts.append((_num(c[1]), _num(c[2])))
                    pl = find(g, "pts")
                    if pl is not None:
                        pts += [(_num(p[1]), _num(p[2])) for p in find_all(pl, "xy")]
                    wd = find(g, "width")
                    if g[0] == "gr_circle":
                        c, e = find(g, "center"), find(g, "end")
                        r = math.hypot(_num(e[1]) - _num(c[1]), _num(e[2]) - _num(c[2]))
                        pts += [(_num(c[1]) - r, _num(c[2]) - r), (_num(c[1]) + r, _num(c[2]) + r)]
                    if wd is not None and _num(wd[1]) > 0:
                        hwid = _num(wd[1]) / 2
                        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                        pts += [(min(xs) - hwid, min(ys) - hwid), (max(xs) + hwid, max(ys) + hwid)]
                if flip:
                    pts = [(px, -py) for px, py in pts]
                # primitives are in the pad's own frame: turn them by the pad's absolute angle
                corners = [rotate(px, py, ang) for px, py in pts]
                xs, ys = [c[0] for c in corners], [c[1] for c in corners]
                cx, cy = x + bx + (min(xs) + max(xs)) / 2, y + by + (min(ys) + max(ys)) / 2
                out.append(Pad(number, cx, cy, (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2, "rect", 0.0,
                               top, bottom, drill))
                continue
            if round(ang) % 180 == 90:
                w, h = h, w
            elif round(ang) % 90 != 0:
                raise ValueError(f"{fpid} pad {number}: only right angles are supported ({ang})")
            corner = 0.0
            if shape == "oval":
                corner = min(w, h) / 2
            elif shape == "roundrect":
                rr = find(pad, "roundrect_rratio")
                corner = min(w, h) * (_num(rr[1]) if rr else 0.25)
            kind_shape = "circle" if shape == "circle" else "rect"
            out.append(Pad(number, x + bx, y + by, w / 2, h / 2, kind_shape, corner, top, bottom, drill,
                           npth=kind == "np_thru_hole"))
        return out
