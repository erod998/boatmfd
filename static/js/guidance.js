// ---------- Auto Guidance: a Go To that follows the channel ----------
// A Go To used to be a straight line from the boat to the destination -- across points, islands
// and whatever else was in the way. A chartplotter's Auto Guidance plans over the water instead,
// and on a river lake like this one that means down the charted channel. This does the same:
//
//  1. The chart's water is rasterized once into a 20 m grid (an offscreen canvas does the filling):
//     depth areas, creeks, the lake, marina basins -- minus land, the dam, piers and docks.
//  2. Every water cell gets a cost: cheapest on the recommended track (the Corps' charted sailing
//     line down the channel), dearer in open water, dearest hugging a bank. A* finds the cheapest
//     way through, so the route runs down the channel wherever the channel goes your way, and
//     leaves it only to reach a destination that is off it -- up a creek, into a marina.
//  3. The cell path is thinned to its turns, never cutting a corner across land, and handed to
//     the server as the Go To's path; the server steers along it leg by leg (app/guidance.py).
//
// If there is no chart, no fix, or no way through the water (a destination behind the dam), the
// Go To falls back to the straight line it always was, and says so.

const GUIDE_CELL_M = 20;
// Layers by name (app/chart_data.py): what floats, and what does not.
const GUIDE_WATER = ["depth_area", "lake", "rivers", "lock_basin", "harbour", "berths", "small_craft_facility_area"];
const GUIDE_OBSTACLES = ["land", "dam", "shoreline_construction", "pylons_area", "pontoon", "floating_dock",
  "mooring_area", "lock_gate", "landmark_area"];
const GUIDE_OBSTACLE_LINES = ["dam_line", "lock_gate_line", "shoreline_construction_line", "mooring_line"];
// Cost per cell, in tenths (stored as bytes): the channel, open water, and water within ~40 m of a
// bank or structure. 0 is not water.
const COST_TRACK = 10, COST_WATER = 22, COST_NEAR_SHORE = 50;

let guideGrid = null;       // built lazily, on the first Go To
let guideGridSource = null; // the chart bundle it was built from

function buildGuideGrid() {
  const [west, south, east, north] = chartBBox;
  const midLat = ((south + north) / 2) * Math.PI / 180;
  const dlat = GUIDE_CELL_M / 111132, dlon = GUIDE_CELL_M / (111320 * Math.cos(midLat));
  const w = Math.ceil((east - west) / dlon), h = Math.ceil((north - south) / dlat);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const px = (lon) => (lon - west) / dlon, py = (lat) => (north - lat) / dlat;

  const trace = (geom) => {
    const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : null;
    const lines = geom.type === "LineString" ? [geom.coordinates] : geom.type === "MultiLineString" ? geom.coordinates : null;
    for (const rings of polys || []) for (const ring of rings) {
      ring.forEach(([lon, lat], i) => (i ? ctx.lineTo(px(lon), py(lat)) : ctx.moveTo(px(lon), py(lat))));
      ctx.closePath();
    }
    for (const line of lines || []) line.forEach(([lon, lat], i) => (i ? ctx.lineTo(px(lon), py(lat)) : ctx.moveTo(px(lon), py(lat))));
  };
  const each = (names, fn) => names.forEach((n) => (chartFeatures[n] ? chartFeatures[n].features : []).forEach((f) => f.geometry && fn(f.geometry)));
  const read = () => {
    const rgba = ctx.getImageData(0, 0, w, h).data;
    const out = new Uint8Array(w * h);
    for (let i = 0; i < out.length; i++) out[i] = rgba[i * 4] > 127 ? 1 : 0;
    return out;
  };
  const clear = () => { ctx.fillStyle = "#000"; ctx.fillRect(0, 0, w, h); };

  // Pass 1: water, with every obstacle cut back out of it.
  clear();
  ctx.fillStyle = "#fff";
  each(GUIDE_WATER, (g) => { ctx.beginPath(); trace(g); ctx.fill("evenodd"); });
  ctx.fillStyle = "#000";
  ctx.strokeStyle = "#000";
  ctx.lineWidth = 1.5;
  each(GUIDE_OBSTACLES, (g) => { ctx.beginPath(); trace(g); ctx.fill("evenodd"); });
  each(GUIDE_OBSTACLE_LINES, (g) => { ctx.beginPath(); trace(g); ctx.stroke(); });
  const water = read();
  // Pass 2: near a bank -- the shoreline and every obstacle's edge, drawn ~40 m wide each side.
  clear();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 4;
  each(["coastline", ...GUIDE_OBSTACLE_LINES], (g) => { ctx.beginPath(); trace(g); ctx.stroke(); });
  each(GUIDE_OBSTACLES, (g) => { ctx.beginPath(); trace(g); ctx.stroke(); });
  const nearShore = read();
  // Pass 3: the channel's sailing line, about 60 m wide.
  clear();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 3;
  each(["recommended_track"], (g) => { ctx.beginPath(); trace(g); ctx.stroke(); });
  const track = read();

  const cost = new Uint8Array(w * h);
  for (let i = 0; i < cost.length; i++) {
    cost[i] = !water[i] ? 0 : track[i] ? COST_TRACK : nearShore[i] ? COST_NEAR_SHORE : COST_WATER;
  }
  canvas.width = canvas.height = 0;   // hand the canvas memory back now
  return { w, h, west, north, dlon, dlat, cost, water };
}

function guideCell(g, lat, lon) {
  const i = Math.floor((lon - g.west) / g.dlon), j = Math.floor((g.north - lat) / g.dlat);
  return i >= 0 && j >= 0 && i < g.w && j < g.h ? j * g.w + i : -1;
}

// The nearest water cell to one that isn't (a dock, a GPS fix a few metres onto the bank, a
// destination tapped on land), within about a kilometre.
function nearestWater(g, start) {
  if (start < 0) return -1;
  if (g.cost[start] > 0) return start;
  const sx = start % g.w, sy = Math.floor(start / g.w);
  for (let r = 1; r <= 50; r++) {
    let best = -1, bestD = Infinity;
    for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
      const x = sx + dx, y = sy + dy;
      if (x < 0 || y < 0 || x >= g.w || y >= g.h) continue;
      const k = y * g.w + x;
      if (g.cost[k] > 0 && dx * dx + dy * dy < bestD) { best = k; bestD = dx * dx + dy * dy; }
    }
    if (best >= 0) return best;
  }
  return -1;
}

// A* over the grid, 8-connected. The heuristic is the straight-line distance at the cheapest cost,
// weighted a little (1.2) for speed: the route can come out a few percent longer than the very
// cheapest, never through land.
function guideSearch(g, from, to) {
  const n = g.w * g.h;
  const gScore = new Float32Array(n).fill(Infinity);
  const came = new Int32Array(n).fill(-1);
  const closed = new Uint8Array(n);
  const tx = to % g.w, ty = Math.floor(to / g.w);
  const h = (k) => {
    const dx = Math.abs(k % g.w - tx), dy = Math.abs(Math.floor(k / g.w) - ty);
    return 1.2 * COST_TRACK * (Math.max(dx, dy) + 0.41421356 * Math.min(dx, dy));
  };
  // A binary heap of cell indexes keyed by f.
  let heap = new Int32Array(1024), keys = new Float32Array(1024), size = 0;
  const push = (k, f) => {
    if (size === heap.length) {
      const h2 = new Int32Array(size * 2); h2.set(heap); heap = h2;
      const k2 = new Float32Array(size * 2); k2.set(keys); keys = k2;
    }
    let i = size++;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (keys[p] <= f) break;
      heap[i] = heap[p]; keys[i] = keys[p]; i = p;
    }
    heap[i] = k; keys[i] = f;
  };
  const pop = () => {
    const top = heap[0];
    const lastK = heap[--size], lastF = keys[size];
    let i = 0;
    for (;;) {
      let c = 2 * i + 1;
      if (c >= size) break;
      if (c + 1 < size && keys[c + 1] < keys[c]) c++;
      if (keys[c] >= lastF) break;
      heap[i] = heap[c]; keys[i] = keys[c]; i = c;
    }
    heap[i] = lastK; keys[i] = lastF;
    return top;
  };
  const DX = [1, -1, 0, 0, 1, 1, -1, -1], DY = [0, 0, 1, -1, 1, -1, 1, -1];
  gScore[from] = 0;
  push(from, h(from));
  let expanded = 0;
  while (size) {
    const k = pop();
    if (closed[k]) continue;
    if (k === to) break;
    closed[k] = 1;
    if (++expanded > 4000000) return null;   // runaway guard: nothing on this lake needs a fraction of that
    const x = k % g.w, y = Math.floor(k / g.w);
    for (let d = 0; d < 8; d++) {
      const nx = x + DX[d], ny = y + DY[d];
      if (nx < 0 || ny < 0 || nx >= g.w || ny >= g.h) continue;
      const nk = ny * g.w + nx;
      const c = g.cost[nk];
      if (!c || closed[nk]) continue;
      // No squeezing diagonally between two land cells.
      if (d >= 4 && (!g.cost[y * g.w + nx] || !g.cost[ny * g.w + x])) continue;
      const tentative = gScore[k] + c * (d >= 4 ? 1.41421356 : 1);   // in tenths, like the costs
      if (tentative < gScore[nk]) {
        gScore[nk] = tentative;
        came[nk] = k;
        push(nk, tentative + h(nk));
      }
    }
  }
  if (came[to] < 0 && from !== to) return null;
  const cells = [];
  for (let k = to; k >= 0; k = came[k]) { cells.push(k); if (k === from) break; }
  return cells.reverse();
}

// Every cell on the straight line between two cells is water (Bresenham).
function lineOfWater(g, a, b) {
  let x0 = a % g.w, y0 = Math.floor(a / g.w);
  const x1 = b % g.w, y1 = Math.floor(b / g.w);
  const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  for (;;) {
    if (!g.water[y0 * g.w + x0]) return false;
    if (x0 === x1 && y0 === y1) return true;
    const e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
}

// Douglas-Peucker down to the turns, but a shortcut is only taken if it stays on water.
function thinPath(g, cells, tolCells) {
  const keep = new Uint8Array(cells.length);
  keep[0] = keep[cells.length - 1] = 1;
  const stack = [[0, cells.length - 1]];
  while (stack.length) {
    const [i, j] = stack.pop();
    if (j <= i + 1) continue;
    const ax = cells[i] % g.w, ay = Math.floor(cells[i] / g.w), bx = cells[j] % g.w, by = Math.floor(cells[j] / g.w);
    const len = Math.hypot(bx - ax, by - ay) || 1;
    let worst = -1, worstD = -1;
    for (let k = i + 1; k < j; k++) {
      const px = cells[k] % g.w, py = Math.floor(cells[k] / g.w);
      const d = Math.abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / len;
      if (d > worstD) { worstD = d; worst = k; }
    }
    if (worstD > tolCells || !lineOfWater(g, cells[i], cells[j])) {
      const split = worstD > tolCells ? worst : (i + j) >> 1;
      keep[split] = 1;
      stack.push([i, split], [split, j]);
    }
  }
  return cells.filter((_, k) => keep[k]);
}

// The Go To path from the boat to a destination, as [[lat, lon], ...] -- the boat first, the
// destination last -- or null to go straight.
function planGuidedPath(fromLat, fromLon, toLat, toLon) {
  if (typeof chartBBox === "undefined" || !chartBBox || !chartFeatures.recommended_track) return null;
  if (!guideGrid || guideGridSource !== chartFeatures) {
    guideGrid = buildGuideGrid();
    guideGridSource = chartFeatures;
  }
  const g = guideGrid;
  const from = nearestWater(g, guideCell(g, fromLat, fromLon));
  const to = nearestWater(g, guideCell(g, toLat, toLon));
  if (from < 0 || to < 0) return null;
  const cells = guideSearch(g, from, to);
  if (!cells) return null;
  const thin = thinPath(g, cells, 1.5);
  const centre = (k) => [g.north - (Math.floor(k / g.w) + 0.5) * g.dlat, g.west + ((k % g.w) + 0.5) * g.dlon];
  const path = [[fromLat, fromLon], ...thin.slice(1, -1).map(centre), [toLat, toLon]];
  // Anything under a few metres apart is the same point to a boat.
  return path.filter((p, i) => i === 0 || i === path.length - 1 ||
    Math.hypot(p[0] - path[i - 1][0], (p[1] - path[i - 1][1]) * 0.8) > 0.00003);
}
