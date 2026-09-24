"""A small grid router for a two-layer board.

Enough for this board: a few dozen slow nets on a Pi HAT. For each net it rasterises
every other net's copper -- inflated by the clearance that pair of nets needs, which is 3 mm
between the ignition side of the tach and everything else -- then finds a path with A* on a
0.1 mm grid across both layers, preferring the top so the bottom stays a ground plane. Multi-pin
nets are built as a tree, each pad joining whatever is already routed.

KiCad's DRC is the judge of the result, not this code: board.py runs it after every build.
"""
import heapq
import math

import numpy as np

TOP, BOTTOM = 0, 1
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


class Item:
    """Copper on the board: a pad (rectangle, possibly rounded), a track segment or a via."""

    def __init__(self, net, layers, kind, **geo):
        self.net, self.layers, self.kind, self.geo = net, set(layers), kind, geo


class Router:
    def __init__(self, width, height, clearance_fn, track=0.25, via_d=0.6, res=0.1, edge_clear=0.4,
                 margin=0.06, layer_cost=(1.0, 3.0), via_cost=12.0, turn_cost=0.6, origin=(0.0, 0.0),
                 arc_margin=False):
        """The grid covers origin .. origin + (width, height), in board mm. arc_margin: a step
        between two clear cells can cut closer to curved copper (a pad's corner, a track's end, a
        via) than either cell is -- by up to a diagonal step's (2 res^2) / 8, over the curve's
        radius. With arc_margin, that much more clearance round curves for a diagonal step, and
        half of it round a circle off the grid (a through-hole pad) for a straight one. A straight
        step can't cut closer to a pad's or a track's straight side, or to a circle centred on the
        grid (a via): none there, since at 0.4 mm pitch there's nothing to spare."""
        self.res = res
        self.x0, self.y0 = origin
        self.nx, self.ny = int(math.ceil(width / res)) + 1, int(math.ceil(height / res)) + 1
        self.w, self.h = width, height
        self.clearance = clearance_fn          # (net_a, net_b) -> mm
        self.track, self.via_r = track, via_d / 2
        self.edge_clear, self.margin = edge_clear, margin
        self.arc_dip = res * res / 4 if arc_margin else 0.0
        self._exact = False
        self.layer_cost, self.via_cost, self.turn_cost = layer_cost, via_cost, turn_cost
        self.items = []
        self.keepouts = []                      # (x0, y0, x1, y1, allow_fn(net)[, layers]) regions
        self.holes = []                         # (x, y, r): nothing within r
        self.outside = []                       # (x0, y0, x1, y1): notches in the outline, off the board
        xs = self.x0 + np.arange(self.nx) * res
        ys = self.y0 + np.arange(self.ny) * res
        self.X, self.Y = np.meshgrid(xs, ys, indexing="ij")

    # ---------------------------------------------------------------- geometry
    def cell(self, x, y):
        return int(round((x - self.x0) / self.res)), int(round((y - self.y0) / self.res))

    def xy(self, i, j):
        return self.x0 + i * self.res, self.y0 + j * self.res

    def _window(self, x0, y0, x1, y1):
        x0, x1, y0, y1 = x0 - self.x0, x1 - self.x0, y0 - self.y0, y1 - self.y0
        i0, j0 = max(0, int(math.floor(x0 / self.res))), max(0, int(math.floor(y0 / self.res)))
        i1, j1 = min(self.nx, int(math.ceil(x1 / self.res)) + 1), min(self.ny, int(math.ceil(y1 / self.res)) + 1)
        return i0, j0, i1, j1

    def _arc(self, radius, diagonal=True):
        """Extra clearance round a curve of this radius (see arc_margin)."""
        if not self.arc_dip or self._exact:
            return 0.0
        return (self.arc_dip if diagonal else self.arc_dip / 2) / max(radius, 0.05) + 0.0002

    def _paint(self, grid, item, r, grid_d=None):
        """Mark every cell within r of the item's copper; and in grid_d, the map for diagonal steps,
        the same with the arc margin round its curves."""
        g = item.geo
        if item.kind == "rect":
            cx, cy, hw, hh, cr = g["x"], g["y"], g["hw"], g["hh"], g.get("corner", 0.0)
            m = self._arc(r + cr)
            i0, j0, i1, j1 = self._window(cx - hw - r - m, cy - hh - r - m, cx + hw + r + m, cy + hh + r + m)
            X, Y = self.X[i0:i1, j0:j1], self.Y[i0:i1, j0:j1]
            # A rounded rectangle: an inner rectangle grown by its corner radius.
            dx = np.maximum(np.abs(X - cx) - (hw - cr), 0.0)
            dy = np.maximum(np.abs(Y - cy) - (hh - cr), 0.0)
            d2 = dx * dx + dy * dy
            grid[i0:i1, j0:j1] |= d2 <= (r + cr) ** 2
            if grid_d is not None:                  # the corners
                grid_d[i0:i1, j0:j1] |= d2 <= (r + cr + np.where((dx > 0) & (dy > 0), m, 0.0)) ** 2
        elif item.kind == "seg":
            x1, y1, x2, y2, hw = g["x1"], g["y1"], g["x2"], g["y2"], g["w"] / 2
            m = self._arc(r + hw)
            rr = r + hw + m
            i0, j0, i1, j1 = self._window(min(x1, x2) - rr, min(y1, y2) - rr, max(x1, x2) + rr, max(y1, y2) + rr)
            X, Y = self.X[i0:i1, j0:j1], self.Y[i0:i1, j0:j1]
            vx, vy = x2 - x1, y2 - y1
            L2 = vx * vx + vy * vy
            t0 = ((X - x1) * vx + (Y - y1) * vy) / L2 if L2 > 0 else np.full(X.shape, -1.0)
            t = np.clip(t0, 0, 1)
            px, py = x1 + t * vx, y1 + t * vy
            d2 = (X - px) ** 2 + (Y - py) ** 2
            grid[i0:i1, j0:j1] |= d2 <= (r + hw) ** 2
            if grid_d is not None:                  # the round ends
                grid_d[i0:i1, j0:j1] |= d2 <= (r + hw + np.where((t0 < 0) | (t0 > 1), m, 0.0)) ** 2
        elif item.kind == "circle":
            cx, cy, rad = g["x"], g["y"], g["r"]
            rr = r + rad + self._arc(r + rad)
            i0, j0, i1, j1 = self._window(cx - rr, cy - rr, cx + rr, cy + rr)
            X, Y = self.X[i0:i1, j0:j1], self.Y[i0:i1, j0:j1]
            d2 = (X - cx) ** 2 + (Y - cy) ** 2
            on_grid = all(abs(v / self.res - round(v / self.res)) < 1e-6 for v in (cx - self.x0, cy - self.y0))
            grid[i0:i1, j0:j1] |= d2 <= (r + rad + (0.0 if on_grid else self._arc(r + rad, diagonal=False))) ** 2
            if grid_d is not None:
                grid_d[i0:i1, j0:j1] |= d2 <= rr * rr

    def inside(self, item, layer):
        """Cells whose centre is on this item's copper, on one layer."""
        grid = np.zeros((self.nx, self.ny), bool)
        if layer in item.layers:
            self._exact = True                 # the copper itself, no margin
            self._paint(grid, item, 0.0)
            self._exact = False
        return grid

    # ---------------------------------------------------------------- obstacle maps
    # A via's centre stays this far outside its own net's surface-mount pads: its drill hole (and
    # the solder mask's opening round it) off the pad, so the joint's solder can't wick down it.
    # Its copper ring may touch the pad; that's the same net.
    VIA_OFF_PAD = 0.25

    def blocked(self, net, radius, own_smd=False, diagonal=False):
        """Per-layer maps of cells where copper of `net`, of half-width `radius`, cannot go. With
        own_smd (placing a via), the net's own surface-mount pads keep it VIA_OFF_PAD away too.
        With diagonal, a second set for diagonal steps (see arc_margin): returns (maps, maps_d)."""
        maps = [np.zeros((self.nx, self.ny), bool), np.zeros((self.nx, self.ny), bool)]
        maps_d = [np.zeros((self.nx, self.ny), bool), np.zeros((self.nx, self.ny), bool)] if diagonal else None
        for item in self.items:
            if item.net == net:
                if own_smd and item.kind == "rect" and len(item.layers) == 1:
                    for layer in (TOP, BOTTOM):
                        self._paint(maps[layer], item, self.VIA_OFF_PAD)
                continue
            r = self.clearance(net, item.net) + radius + self.margin
            for layer in item.layers:
                self._paint(maps[layer], item, r, maps_d[layer] if diagonal else None)
        # The board edge and holes block both layers; a keepout, the layers it names (both if none).
        e = self.edge_clear + radius + self.margin
        edge = ((self.X < self.x0 + e) | (self.Y < self.y0 + e) |
                (self.X > self.x0 + self.w - e) | (self.Y > self.y0 + self.h - e))
        for x0, y0, x1, y1 in self.outside:
            edge |= (self.X > x0 - e) & (self.X < x1 + e) & (self.Y > y0 - e) & (self.Y < y1 + e)
        for k, m in enumerate(maps):
            m |= edge
            if diagonal:
                maps_d[k] |= edge
            for x, y, r in self.holes:
                self._paint(m, Item(None, (0,), "circle", x=x, y=y, r=r), radius + self.margin,
                            maps_d[k] if diagonal else None)
        for x0, y0, x1, y1, allow, *layers in self.keepouts:
            if allow(net):
                continue
            i0, j0, i1, j1 = self._window(x0 - radius, y0 - radius, x1 + radius, y1 + radius)
            for layer in (layers[0] if layers else (TOP, BOTTOM)):
                maps[layer][i0:i1, j0:j1] = True
                if diagonal:
                    maps_d[layer][i0:i1, j0:j1] = True
        return (maps, maps_d) if diagonal else maps

    # ---------------------------------------------------------------- search
    MAX_EXPANDED = 6_000_000

    def search(self, net, sources, goal, heur_xy, via_ok=True, allow_layers=(TOP, BOTTOM)):
        """A* from any source cell to any goal cell. sources: list of (i, j, layer).
        goal: (i, j, layer) -> bool. Returns the cell path or None."""
        track, track_d = self.blocked(net, self.track / 2, diagonal=True)
        via = self.blocked(net, self.via_r, own_smd=True) if via_ok else None
        hx, hy = heur_xy
        hi, hj = (hx - self.x0) / self.res, (hy - self.y0) / self.res
        cheap = min(self.layer_cost[l] for l in allow_layers)

        def h(i, j):
            dx, dy = abs(i - hi), abs(j - hj)
            return cheap * (max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy))

        openq, g, came = [], {}, {}
        sources = [(i, j, l) for i, j, l in sources if 0 <= i < self.nx and 0 <= j < self.ny and l in allow_layers]
        # Not from a cell of a pad too close to its neighbour (off a fine-pitch pin's centre line),
        # unless there's nowhere else to start.
        sources = [c for c in sources if not track[c[2]][c[0], c[1]]] or sources
        for (i, j, l) in sources:
            s = (i, j, l, -1)
            g[s] = 0.0
            heapq.heappush(openq, (h(i, j), 0.0, s))
        expanded = 0
        while openq:
            f, gc, s = heapq.heappop(openq)
            if gc > g.get(s, math.inf):
                continue
            i, j, l, d = s
            if goal(i, j, l):
                path = [(i, j, l)]
                while s in came:
                    s = came[s]
                    path.append(s[:3])
                return path[::-1]
            expanded += 1
            if expanded > self.MAX_EXPANDED:
                return None
            for nd, (di, dj) in enumerate(DIRS):
                ni, nj = i + di, j + dj
                if not (0 <= ni < self.nx and 0 <= nj < self.ny) or (track_d if di and dj else track)[l][ni, nj]:
                    continue
                if di and dj and track_d[l][i, j]:     # a diagonal step: from a clear cell too
                    continue
                step = (math.sqrt(2) if di and dj else 1.0) * self.layer_cost[l]
                if d >= 0 and d != nd:
                    turn = min((nd - d) % 8, (d - nd) % 8)
                    step += self.turn_cost * turn
                ns = (ni, nj, l, nd)
                ng = gc + step
                if ng < g.get(ns, math.inf):
                    g[ns] = ng
                    came[ns] = s
                    heapq.heappush(openq, (ng + h(ni, nj), ng, ns))
            if via_ok:
                ol = 1 - l
                if ol in allow_layers and not via[TOP][i, j] and not via[BOTTOM][i, j]:
                    ns = (i, j, ol, -1)
                    ng = gc + self.via_cost
                    if ng < g.get(ns, math.inf):
                        g[ns] = ng
                        came[ns] = s
                        heapq.heappush(openq, (ng + h(i, j), ng, ns))
        return None

    # ---------------------------------------------------------------- results
    def to_geometry(self, path):
        """A cell path as straight segments per layer and via positions, collinear runs merged."""
        segs, vias = [], []
        run = [path[0]]
        for c in path[1:]:
            if c[2] != run[-1][2]:
                vias.append(self.xy(c[0], c[1]))
                segs += self._runs(run)
                run = [c]
            else:
                run.append(c)
        segs += self._runs(run)
        return segs, vias

    def _runs(self, run):
        if len(run) < 2:
            return []
        out, start = [], run[0]
        prev_dir = None
        for a, b in zip(run, run[1:]):
            d = (b[0] - a[0], b[1] - a[1])
            if prev_dir is not None and d != prev_dir:
                out.append((self.xy(start[0], start[1]), self.xy(a[0], a[1]), a[2]))
                start = a
            prev_dir = d
        out.append((self.xy(start[0], start[1]), self.xy(run[-1][0], run[-1][1]), run[-1][2]))
        return out
