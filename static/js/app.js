"use strict";
const $ = (id) => document.getElementById(id);
const postJson = (url, body) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const bind = (name, text) => document.querySelectorAll(`[data-bind="${name}"]`).forEach((e) => (e.textContent = text));
const fmtNum = (v, d = 0) => (v == null ? "--" : Number(v).toFixed(d));

// Numbers that glide to each new reading (see makeAnimatedText in dials.js) instead of jumping.
const animBinds = {};
function animBind(name, value, decimals = 0) {
  const a = animBinds[name] || (animBinds[name] = makeAnimatedText(() => document.querySelectorAll(`[data-bind="${name}"]`), { decimals, omega: 6 }));
  a.update(value);
}

let lastData = null;

// ---------- Hold a gauge/box to open a menu (the alarm menu, gauge-display menu, or an overlay picker) ----------
// Lives here (not alarms.js, which loads after this file) because the data-overlay boxes further
// down need it at script-load time, before alarms.js has even been fetched.
/** Pure gesture: hold `el` for `ms` and call onHold(); a hold is never also a tap. Idempotent (safe to call twice). */
function attachHold(el, onHold, ms = 600) {
  if (!el || el._holdAttached) return;
  el._holdAttached = true;
  el.classList.add("holdable");

  let timer = null;
  let origin = null;
  let fired = false;
  const cancel = () => { clearTimeout(timer); timer = null; el.classList.remove("holding"); };
  el.addEventListener("pointerdown", (e) => {
    if (e.button) return;
    fired = false;
    origin = [e.clientX, e.clientY];
    el.classList.add("holding");
    timer = setTimeout(() => { fired = true; cancel(); onHold(); }, ms);
  });
  el.addEventListener("pointermove", (e) => { if (timer && Math.hypot(e.clientX - origin[0], e.clientY - origin[1]) > 14) cancel(); });
  ["pointerup", "pointercancel", "pointerleave"].forEach((ev) => el.addEventListener(ev, cancel));
  // a long press is not also a tap (a speed dial, for one, switches units when tapped)
  el.addEventListener("click", (e) => { if (fired) { e.stopImmediatePropagation(); e.preventDefault(); fired = false; } }, true);
  el.addEventListener("contextmenu", (e) => e.preventDefault());  // a touch long-press must not open the browser's own menu
}

// ---------- Units (tap a speed dial, or Options) ----------
// The backend works in knots and nautical miles; the display defaults to mph and statute miles for lake use.
const UNITS = {
  mph: { speed: "mph", gauge: "MPH", dist: "mi", factor: 1.15078, max: 40, step: 10 },
  kn: { speed: "kn", gauge: "KN", dist: "nm", factor: 1, max: 35, step: 5 },
};
let unit = "mph";
try { if (localStorage.getItem("unit") in UNITS) unit = localStorage.getItem("unit"); } catch (e) { /* storage unavailable */ }
const toSpeed = (kn) => kn * UNITS[unit].factor;
const toDist = (nm) => nm * UNITS[unit].factor;

// ---------- Dial zones (green range is simply the blue fill) ----------
const ZONES = {
  cool: [{ from: 195, to: 210, level: "amber" }, { from: 210, to: 260, level: "red" }],
  oil: [{ from: 0, to: 10, level: "red" }, { from: 10, to: 20, level: "amber" }],
  batt: [{ from: 10, to: 11.8, level: "red" }, { from: 11.8, to: 12.3, level: "amber" }, { from: 14.8, to: 16, level: "red" }],
  fuel: [{ from: 0, to: 10, level: "red" }, { from: 10, to: 25, level: "amber" }],
};

const dials = {};

// ---------- Map ----------
// Old Hickory Lake (Cumberland River) at Hendersonville, TN 37075.
// rotate: true (from the leaflet-rotate plugin) lets the chart turn to Heading Up; rotateControl/shiftKeyRotate are
// its own touch/mouse rotate gestures, turned off since bearing is driven only from GPS heading (see setChartOrient).
// fadeAnimation off: the chart is map tiles (see "The chart as map tiles"), and a chart that fades
// in, tile by tile, after every zoom is not what a chartplotter does -- and the fade costs frames.
const map = L.map("map", { zoomControl: false, attributionControl: false, minZoom: 5, maxZoom: 18, rotate: true, rotateControl: false, shiftKeyRotate: false, fadeAnimation: false }).setView([36.306, -86.563], 14);
L.control.scale({ position: "bottomleft", metric: false, imperial: true, maxWidth: 110 }).addTo(map);

// ---------- The chart itself: USACE Inland ENC vectors, drawn here, from local disk ----------
// This used to be a raster tile layer pulling rendered pictures from the Corps of Engineers' map
// server -- thousands of throttled requests and ~88 MB to cover one lake at five fixed zooms, and
// nothing at all without internet. It now draws the actual S-57 feature data (fetched once by
// `python -m app.fetch_charts`, see app/chart_data.py): 822 features and 2.1 MB for the whole lake,
// which is why it's fast, works entirely offline, stays crisp at any zoom, restyles for night by
// changing colours rather than re-rendering, and can tell you what a buoy is when you tap it.
//
// Canvas rather than SVG: thousands of chart features would cost a DOM element each in SVG.
//
// Leaflet's canvas renderer clears and redraws everything on every "moveend", and a chart that
// follows the boat -- or turns with it in Heading Up -- moves the map a fraction of a pixel every
// animation frame. For the whole lake that is several thousand polygons, sixty times a second.
// But what the canvas holds does not change when the map merely slides or turns: it is drawn in
// layer coordinates, 30% larger than the screen on every side, and the pane transform does the
// sliding and turning. So these renderers only redraw once the visible corners get near the edge
// of what was last drawn, or the zoom or pixel origin changed -- about once every few seconds
// when following the boat, instead of every frame.
const LazyCanvas = L.Canvas.extend({
  _update() {
    const map = this._map;
    if (this._bounds && !map._animatingZoom && this._drawnZoom === map.getZoom() &&
        this._drawnOrigin && this._drawnOrigin.equals(map.getPixelOrigin())) {
      const size = map.getSize();
      const m = Math.max(size.x, size.y) * 0.1;
      const inner = L.bounds(this._bounds.min.add([m, m]), this._bounds.max.subtract([m, m]));
      const corners = [[0, 0], [size.x, 0], [0, size.y], [size.x, size.y]];
      if (corners.every((c) => inner.contains(map.containerPointToLayerPoint(c)))) return;
    }
    L.Canvas.prototype._update.call(this);
    this._drawnZoom = map.getZoom();
    this._drawnOrigin = map.getPixelOrigin();
  },
});

// Four layers of chart, bottom to top: the base (land, water, depth areas, roads) as map tiles; the
// surveyed-depth shading over it; the chart's lines (shoreline, channel, cables) as tiles again; and
// the aids to navigation and hazards as markers on top, where they can be tapped -- so a buoy at the
// edge of the surveyed channel is never painted over by the depth shading.
//
// All three are created inside leaflet-rotate's rotating pane. A pane made with plain
// createPane(name) lands in the map pane instead, which the plugin never turns: from the switch
// to vector charts until this was found, Heading Up turned the boat, the track and the labels
// while the chart underneath stayed north-up and drew in the wrong place.
const rotatingPane = map.getPane("rotatePane");   // undefined only if rotation is off, and then the default is right
const chartPane = map.createPane("chart", rotatingPane);
chartPane.style.zIndex = 200;   // under every marker pane, over the map background
map.createPane("survey", rotatingPane).style.zIndex = 205;
map.createPane("chartTop", rotatingPane).style.zIndex = 210;
map.createPane("chartPoints", rotatingPane).style.zIndex = 212;
const chartRendererPoints = new LazyCanvas({ padding: 0.3, pane: "chartPoints" });
// The chart's lines, drawn over the surveyed-depth shading; every other area and line kind is drawn
// under it. Point kinds are markers (POINT_KINDS, below).
const TOP_LINE_KINDS = new Set(["coastline", "contour", "structure_line", "dock_line", "hazard_line", "track"]);
const rendererFor = () => ({ renderer: chartRendererPoints, pane: "chartPoints" });   // for the point markers

// Depth shading bands, in metres, shallowest first. IENC gives each depth area a range
// (Depth_Area_Value_1..2); the band is chosen from the deepest edge, so a 0-2.74 m polygon shades
// as the shallow water it is. The whole point of a chart at a glance is "can I go there".
const DEPTH_BANDS = [2.0, 5.0, 10.0];

// survey: the surveyed-depth ramp, shallowest first, for SURVEY_BANDS_FT below -- darker blue is
// less water, as on a paper chart, and the deep river bed goes nearly white.
const CHART_PALETTES = {
  day: {
    land: "#e9dcc3", landEdge: "#b9ab8d", builtUp: "#ded2bc", water: ["#8ec9e8", "#b3dcf0", "#d3ebf8", "#e8f4fb"],
    coast: "#4a4636", contour: "#6f98ad", caution: "#e8d24a", danger: "#d1382f", track: "#b02a8f",
    hazard: "#e07b20", text: "#1c1c1c", textHalo: "#ffffff", chartBg: "#efe8da",
    facility: "#8e2aa6", dock: "#8a7f6c", structure: "#c9bda4", road: "#c7b28c", rail: "#6b6254", restricted: "#c0268f",
    survey: ["#4d98cf", "#6aaddb", "#89c0e4", "#a8d2ec", "#c4e1f2", "#dcedf8", "#f1f8fc"],
  },
  dusk: {
    land: "#8d8369", landEdge: "#6e664f", builtUp: "#847b63", water: ["#3d6c86", "#4e7f99", "#5e91ab", "#6ea0b9"],
    coast: "#3a372c", contour: "#7fa5b8", caution: "#c2ae3e", danger: "#c2352c", track: "#a3287f",
    hazard: "#c46c1c", text: "#f0f0f0", textHalo: "#1a1a1a", chartBg: "#958c76",
    facility: "#b85ad0", dock: "#6b6250", structure: "#7a7059", road: "#73664b", rail: "#4a4336", restricted: "#b13d8f",
    survey: ["#2f5c77", "#3a6a86", "#467995", "#5388a3", "#6297b1", "#72a6be", "#83b4ca"],
  },
  night: {
    land: "#241f16", landEdge: "#4a412e", builtUp: "#2c261b", water: ["#0d2a3a", "#0a2231", "#071a26", "#05131c"],
    coast: "#6e6449", contour: "#3f6b82", caution: "#8a7a24", danger: "#a12b22", track: "#7d1f63",
    hazard: "#8a5416", text: "#d6dade", textHalo: "#000000", chartBg: "#1b1812",
    facility: "#8a3f9e", dock: "#4b4335", structure: "#342d21", road: "#3d3424", rail: "#2d271d", restricted: "#7a2461",
    survey: ["#123a52", "#0f3247", "#0c2a3c", "#0a2332", "#081c29", "#061620", "#041017"],
  },
};

const palette = () => CHART_PALETTES[chartColorMode] || CHART_PALETTES.day;

function depthBandIndex(props) {
  const deepest = parseFloat(props.Depth_Area_Value_2);
  if (!isFinite(deepest)) return DEPTH_BANDS.length;
  for (let i = 0; i < DEPTH_BANDS.length; i++) if (deepest <= DEPTH_BANDS[i]) return i;
  return DEPTH_BANDS.length;
}

// Colour for a buoy/beacon from its own charted colour, so a red starboard-hand mark draws red.
function aidColor(props, p) {
  const c = String(props.Color || "").toLowerCase();
  if (c.includes("red")) return "#d1382f";
  if (c.includes("green")) return "#2f9e44";
  if (c.includes("yellow")) return "#e8c33a";
  if (c.includes("white")) return "#f2f2f2";
  if (c.includes("black")) return "#1a1a1a";
  return p.hazard;
}

function chartStyle(kind, feature) {
  const p = palette();
  const props = feature.properties || {};
  const r = rendererFor(kind);
  switch (kind) {
    case "depth":
      return { ...r, stroke: false, fillColor: p.water[depthBandIndex(props)], fillOpacity: 1 };
    case "area":
      return { ...r, color: p.landEdge, weight: 1, fillColor: p.land, fillOpacity: 1 };
    case "water_area":   // creeks, the lock basin: water of uncharted depth
      return { ...r, stroke: false, fillColor: p.water[1], fillOpacity: 1 };
    case "facility_area":   // marina basins
      return { ...r, color: p.facility, weight: 1.4, dashArray: "5 4", fillColor: p.water[2], fillOpacity: 1 };
    case "dock":
      return { ...r, color: p.dock, weight: 1, fillColor: p.dock, fillOpacity: 0.9 };
    case "structure":
      return { ...r, color: p.landEdge, weight: 1, fillColor: p.structure, fillOpacity: 1 };
    case "road":
      return { ...r, color: p.road, weight: 1.3, fill: false };
    case "railroad":
      return { ...r, color: p.rail, weight: 1.5, dashArray: "7 3", fill: false };
    case "caution":
      return { ...r, color: p.caution, weight: 2, dashArray: "6 4", fillColor: p.caution, fillOpacity: 0.16 };
    case "restricted":   // restricted areas and anchorages: magenta dashes, as charts print them
      return { ...r, color: p.restricted, weight: 1.6, dashArray: "9 5", fillColor: p.restricted, fillOpacity: 0.05 };
    case "danger":
      return { ...r, color: p.danger, weight: 2, fillColor: p.danger, fillOpacity: 0.25 };
    case "coastline":
      return { ...r, color: p.coast, weight: 1.6, fill: false };
    case "contour":
      return { ...r, color: p.contour, weight: 1, dashArray: "5 4", fill: false };
    case "structure_line":
      return { ...r, color: p.coast, weight: 2.6, fill: false };
    case "dock_line":
      return { ...r, color: p.dock, weight: 2, fill: false };
    case "track":
      return { ...r, color: p.track, weight: 2, dashArray: "10 6", fill: false, opacity: 0.85 };
    case "hazard_line":
      return { ...r, color: p.hazard, weight: 2, dashArray: "3 4", fill: false };
    default:
      return { ...r, color: p.coast, weight: 1, fill: false };
  }
}

// Point features become small chart symbols. Kept as canvas circleMarkers rather than DOM icons so
// hundreds of them cost nothing to redraw while the chart rotates.
function chartPoint(kind, feature, latlng) {
  const p = palette();
  const props = feature.properties || {};
  const base = rendererFor(kind);
  if (kind === "facility") {
    return L.circleMarker(latlng, { ...base, radius: 5.5, color: p.textHalo, weight: 1.5,
      fillColor: p.facility, fillOpacity: 1 });
  }
  if (kind === "notice") {
    return L.circleMarker(latlng, { ...base, radius: 4, color: p.text, weight: 1,
      fillColor: "#f2c318", fillOpacity: 1 });
  }
  if (kind === "buoy" || kind === "beacon") {
    return L.circleMarker(latlng, { ...base, radius: kind === "buoy" ? 5 : 4.5,
      color: p.textHalo, weight: 1.2, fillColor: aidColor(props, p), fillOpacity: 1 });
  }
  if (kind === "light") {
    return L.circleMarker(latlng, { ...base, radius: 5.5, color: aidColor(props, p), weight: 2,
      fillColor: aidColor(props, p), fillOpacity: 0.45 });
  }
  if (kind === "danger_point") {
    return L.circleMarker(latlng, { ...base, radius: 4, color: p.danger, weight: 2,
      fillColor: p.danger, fillOpacity: 0.5 });
  }
  if (kind === "distance_mark") {
    return L.circleMarker(latlng, { ...base, radius: 2.5, color: p.text, weight: 1,
      fillColor: p.textHalo, fillOpacity: 0.9 });
  }
  return L.circleMarker(latlng, { ...base, radius: 2.5, color: p.coast, weight: 1,
    fillColor: p.coast, fillOpacity: 0.6 });
}

// Which chart layers each Options toggle controls, by the layer names app/chart_data.py writes.
const CHART_LAYER_GROUPS = {
  base: { label: "Land & shoreline", hint: "Land fill, the shoreline, creeks, bridges, the dam and lock, and built-up areas",
          layers: ["land", "lake", "rivers", "lock_basin", "built_up", "bridge", "dam", "dam_line", "lock_gate", "lock_gate_line",
                   "shoreline_construction", "pylons_area", "landmark_area", "coastline"] },
  depths: { label: "Charted depths & contours", hint: "The Corps chart's depth areas (0-9 ft outside the channel, 9 ft+ in it) and contour lines",
            layers: ["depth_area", "depth_contour"] },
  aids: { label: "Aids to navigation", hint: "Buoys, beacons, lights, daymarks and river mile markers",
          layers: ["lateral_buoy", "isolated_danger_buoy", "special_purpose_buoy", "lateral_beacon", "daymark", "light", "distance_mark", "recommended_track"] },
  facilities: { label: "Marinas & docks", hint: "Marinas, boat docks, piers and mooring facilities",
                layers: ["harbour", "berths", "small_craft_facility_area", "pontoon", "floating_dock", "mooring_area", "mooring_line",
                         "shoreline_construction_line", "harbour_facility", "small_craft_facility", "mooring_point", "berth_point"] },
  hazards: { label: "Hazards & restricted areas", hint: "Wrecks, rocks, obstructions, cables, pipelines, caution and restricted areas, notice signs",
             layers: ["caution", "cable_area", "restricted_area", "anchorage_area", "rock_area", "wreck_area", "obstruction_area", "obstruction",
                      "underwater_rock", "wreck", "pile", "pylons", "caution_point", "overhead_cable", "overhead_pipeline", "submarine_pipeline",
                      "submarine_cable", "obstruction_line", "notice_mark"] },
  roads: { label: "Roads & railroads", hint: "Roads and railroads on land, for finding your way to a ramp or a marina",
           layers: ["roads", "railroads"] },
  landmarks: { label: "Landmarks", hint: "Charted landmarks, shoreline structures and river gauges",
               layers: ["landmark", "shoreline_construction_point", "waterway_gauge"] },
  // Not chart layers: drawn by the surveyed-depth layer and the label placer below.
  survey: { label: "Surveyed depths", hint: "Real depths from the Corps of Engineers' channel surveys, shaded, with soundings when zoomed in",
            layers: [] },
  names: { label: "Names & labels", hint: "Creek, island, town and marina names, and river miles", layers: [] },
};

let chartLayerState = {};
try { chartLayerState = JSON.parse(localStorage.getItem("chartLayers")) || {}; } catch (e) { /* storage unavailable */ }
function layerGroupOn(key) {
  return chartLayerState[key] !== false;   // everything is shown by default
}
const groupForLayer = (name) =>
  Object.keys(CHART_LAYER_GROUPS).find((k) => CHART_LAYER_GROUPS[k].layers.includes(name));

// ---------- Loading and drawing the chart ----------
// Draw order matters on a chart: land and depth shading underneath, then lines, then the aids to
// navigation on top where they can always be seen and tapped.
const DRAW_ORDER = ["area", "water_area", "depth", "facility_area", "structure", "road", "railroad", "dock",
                     "caution", "restricted", "danger", "coastline", "contour", "structure_line", "dock_line",
                     "hazard_line", "track", "landmark", "facility", "notice", "distance_mark", "danger_point",
                     "beacon", "buoy", "light"];
// Kinds that are only names -- creek and island names, towns -- and are drawn as labels, not shapes.
const LABEL_ONLY = new Set(["place", "place_area", "water_name", "water_name_area"]);

const chartGroup = L.layerGroup().addTo(map);
const chartLayers = {};        // layer name -> { kind, leafletLayer }, for the point markers
const chartFeatures = {};      // layer name -> { kind, features }: the raw chart, for identify and the cursor
let chartArea = null;          // the area currently loaded
let chartBBox = null;          // its [west, south, east, north], for the Go To route planner (guidance.js)
let chartLoading = false;

// ---------- The chart as map tiles ----------
// The chart used to be thousands of Leaflet vector layers, and Leaflet re-projects and re-clips
// every vertex of every one of them on every zoom -- 170,000 vertices for the whole lake, about
// 80 ms of solid work per zoom step on a desktop and a visible stall on the iPad, wherever on the
// lake you were looking. Now the chart is cut into map tiles in the browser (geojson-vt, the
// technique vector map engines use): each tile holds only its own piece of the chart, simplified
// for its zoom, is drawn once, and is reused while it stays on screen. A zoom draws a screenful of
// small tiles instead of redoing the whole lake.
const TILE_EXTENT = 4096;
let baseIndex = null, topIndex = null;
const layerGroupOf = {};   // layer name -> its Map Settings group, cached (asked for every feature of every tile)
const groupOf = (name) => (name in layerGroupOf ? layerGroupOf[name] : (layerGroupOf[name] = groupForLayer(name)));

// Canvas drawing of one tile's features, styled exactly as the vector layers were (chartStyle).
function drawChartTile(ctx, tile, z, k) {
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  for (const f of tile.features) {
    const kind = f.tags._k;
    if (f.type === 1 || !kindShownAt(kind, z) || !layerGroupOn(groupOf(f.tags._n))) continue;
    const st = chartStyle(kind, { properties: f.tags });
    ctx.beginPath();
    for (const part of f.geometry) {
      for (let i = 0; i < part.length; i++) {
        const x = part[i][0] * k, y = part[i][1] * k;
        if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      }
      if (f.type === 3) ctx.closePath();
    }
    if (f.type === 3 && st.fill !== false) {
      ctx.globalAlpha = st.fillOpacity ?? 0.2;
      ctx.fillStyle = st.fillColor || st.color;
      ctx.fill("evenodd");
    }
    if (st.stroke !== false && (st.weight ?? 3) > 0) {
      ctx.globalAlpha = st.opacity ?? 1;
      ctx.strokeStyle = st.color;
      ctx.lineWidth = st.weight ?? 3;
      ctx.setLineDash(st.dashArray ? String(st.dashArray).split(/[ ,]+/).map(Number) : []);
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
  ctx.setLineDash([]);
}

const ChartTiles = L.GridLayer.extend({
  initialize(which, options) {
    this._which = which;
    L.GridLayer.prototype.initialize.call(this, options);
  },
  createTile(coords) {
    const tile = document.createElement("canvas");
    const size = this.getTileSize();
    const ratio = window.devicePixelRatio > 1 ? 2 : 1;
    tile.width = size.x * ratio;
    tile.height = size.y * ratio;
    const index = this._which === "top" ? topIndex : baseIndex;
    const t = index && index.getTile(coords.z, coords.x, coords.y);
    if (t) {
      const ctx = tile.getContext("2d");
      ctx.scale(ratio, ratio);
      drawChartTile(ctx, t, coords.z, size.x / TILE_EXTENT);
    }
    return tile;
  },
});
const tileOptions = { updateWhenIdle: false, updateWhenZooming: false, keepBuffer: 3, maxZoom: 18 };
const baseTiles = new ChartTiles("base", { ...tileOptions, pane: "chart" });
const topTiles = new ChartTiles("top", { ...tileOptions, pane: "chartTop" });
function redrawChartTiles() {
  if (map.hasLayer(baseTiles)) baseTiles.redraw();
  if (map.hasLayer(topTiles)) topTiles.redraw();
}

// Splits a chart bundle into the two tile indexes, the point markers and the raw features.
function buildChart(bundle) {
  const base = [], top = [];
  const entries = Object.entries(bundle.layers || {});
  entries.sort((a, b) => DRAW_ORDER.indexOf(a[1].kind) - DRAW_ORDER.indexOf(b[1].kind));
  Object.keys(chartFeatures).forEach((k) => delete chartFeatures[k]);
  for (const [name, { kind, geojson }] of entries) {
    const features = (geojson && geojson.features) || [];
    chartFeatures[name] = { kind, features };
    if (LABEL_ONLY.has(kind) || POINT_KINDS.has(kind)) continue;
    const dest = TOP_LINE_KINDS.has(kind) ? top : base;
    for (const f of features) {
      if (!f.geometry || /Point$/.test(f.geometry.type)) continue;
      dest.push({ type: "Feature", geometry: f.geometry, properties: { ...f.properties, _k: kind, _n: name } });
    }
  }
  // tolerance is in 1/4096ths of a tile: 6 is under half a screen pixel, at every zoom.
  const opts = { maxZoom: 18, indexMaxZoom: 5, indexMaxPoints: 100000, tolerance: 6, extent: TILE_EXTENT, buffer: 64 };
  baseIndex = geojsonvt({ type: "FeatureCollection", features: base }, opts);
  topIndex = geojsonvt({ type: "FeatureCollection", features: top }, opts);
  if (!map.hasLayer(baseTiles)) { baseTiles.addTo(map); topTiles.addTo(map); } else redrawChartTiles();
}

// Which chart layers take a tap. Only the point features -- aids to navigation, mile markers,
// landmarks, hazard points -- the things you would actually want to tap to identify.
//
// Every layer used to be tappable, and land, shoreline and depth-area polygons between them cover
// the entire chart, so every tap anywhere opened "Land" or "Depth area" and never reached the map:
// tap-to-navigate and the ruler were both dead wherever there was chart data, which on Old Hickory
// is everywhere. Areas are still identifiable -- the chart cursor names what it is sitting in.
// Non-interactive layers are also skipped by the canvas renderer's hit-testing, which otherwise
// ran a containment check against every polygon on every mouse move.
const POINT_KINDS = new Set(["buoy", "beacon", "light", "distance_mark", "landmark", "danger_point", "facility", "notice"]);

// The zoom a kind of feature starts to show at. The whole lake at once is hundreds of pylons,
// mooring posts and landmarks piled on the river line; a chartplotter brings detail in as you
// zoom, and so does this. Kinds not listed show at every zoom.
const KIND_MIN_ZOOM = {
  buoy: 12, beacon: 12, light: 12, distance_mark: 12, facility: 12, road: 12, railroad: 12,
  landmark: 14, danger_point: 13, notice: 14, dock: 13, dock_line: 13, structure_line: 12,
};
const kindShownAt = (kind, z) => z >= (KIND_MIN_ZOOM[kind] || 0);
// Adds or removes each point-marker layer for the current zoom and the Map Settings toggles. (The
// tiles apply both themselves, as they draw.)
function applyChartVisibility() {
  const z = map.getZoom();
  Object.entries(chartLayers).forEach(([name, { kind, layer }]) => {
    const on = layerGroupOn(groupForLayer(name)) && kindShownAt(kind, z);
    if (on && !chartGroup.hasLayer(layer)) layer.addTo(chartGroup);
    else if (!on && chartGroup.hasLayer(layer)) chartGroup.removeLayer(layer);
  });
}
const isPointGeom = (f) => !!f.geometry && /Point$/.test(f.geometry.type);

function renderChartBundle(bundle) {
  chartBBox = bundle.bbox || null;
  chartGroup.clearLayers();
  Object.keys(chartLayers).forEach((k) => delete chartLayers[k]);
  buildChart(bundle);
  buildChartLabels(bundle.layers || {});
  const entries = Object.entries(bundle.layers || {});
  entries.sort((a, b) => DRAW_ORDER.indexOf(a[1].kind) - DRAW_ORDER.indexOf(b[1].kind));
  entries.forEach(([name, { kind, geojson }]) => {
    if (!POINT_KINDS.has(kind)) return;   // everything else is in the tiles
    const tappable = true;
    const layer = L.geoJSON(geojson, {
      renderer: chartRendererPoints,
      pane: "chartPoints",
      interactive: tappable,
      style: (f) => chartStyle(kind, f),
      pointToLayer: (f, latlng) => chartPoint(kind, f, latlng),
      // Checked per feature as well as per layer, so a future chart area that encodes one of these
      // kinds as a polygon still cannot start swallowing taps across whatever it covers.
      onEachFeature: (f, lyr) => {
        if (!tappable || !isPointGeom(f)) return;
        lyr.on("click", (e) => {
          L.DomEvent.stopPropagation(e);
          clearCursor();
          showChartFeature(name, kind, f.properties || {}, lyr.getLatLng ? lyr.getLatLng() : e.latlng);
        });
      },
    });
    chartLayers[name] = { kind, layer };
  });
  applyChartVisibility();
}

// The full-detail chart, once: the tiles simplify it for each zoom themselves, so there is no
// longer a zoomed-out copy to swap to (and no reload each time the zoom crossed over).
async function loadChart(area) {
  if (chartLoading) return;
  chartLoading = true;
  try {
    const bundle = await (await fetch(`/api/chart/${area}/detail`)).json();
    if (!bundle || !bundle.layers) return;
    chartArea = area;
    renderChartBundle(bundle);
    setChartNotice(null);
  } catch (e) {
    // No charts on disk yet, or the file is unreadable: the boat, track and everything else still
    // work, there's just no chart under them. `python -m app.fetch_charts <area>` fixes it.
  } finally {
    chartLoading = false;
  }
}

async function initCharts() {
  try {
    const { areas } = await (await fetch("/api/chart/areas")).json();
    if (!areas || !areas.length) {
      setChartNotice("No chart data on this device -- run:  python -m app.fetch_charts old-hickory");
      return;
    }
    await loadChart(areas[0].name);
    if (areas[0].survey) loadSurvey(areas[0].name);
  } catch (e) {
    setChartNotice("Chart data could not be read -- check the boat-dashboard service log");
  }
}

// ---------- Surveyed depths: the Corps' channel surveys, shaded (app/survey_depths.py) ----------
// The Corps chart only knows "0-9 ft" and "9 ft or more" on this lake. Its hydrographic surveys
// know the actual bottom, to a tenth of a foot, across the old river bed -- river miles 216-225
// and 297-313 on Old Hickory. The file stores bottom *elevations*; depth is the lake level minus
// the bottom, so the shading and every sounding follow the Lake level setting (Map Settings), the
// way a Garmin's lake charts do. Everything shallower is darker, as on a paper chart.
const SURVEY_BANDS_FT = [3, 6, 10, 15, 20, 30];   // band edges; each palette has one more colour than edges
const SURVEY_BAND_LABELS = ["< 3", "3-6", "6-10", "10-15", "15-20", "20-30", "30+"];
let survey = null;            // the loaded grid, or null if this area has none
let lakeLevelSetting = null;  // feet, shared by every screen (/api/chart-settings); null = normal pool
const lakeLevel = () => (lakeLevelSetting != null ? lakeLevelSetting : survey ? survey.normal_pool_ft : null);
function surveyBand(depthFt) {
  let i = 0;
  while (i < SURVEY_BANDS_FT.length && depthFt >= SURVEY_BANDS_FT[i]) i++;
  return i;
}

// Web Mercator, as Leaflet lays out its tiles: global pixel position at a zoom's scale.
const mercX = (lon, scale) => ((lon + 180) / 360) * scale;
const mercY = (lat, scale) => {
  const s = Math.sin((lat * Math.PI) / 180);
  return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * scale;
};
const mercLon = (x, scale) => (x / scale) * 360 - 180;
const mercLat = (y, scale) => (180 / Math.PI) * Math.atan(Math.sinh(Math.PI - (2 * Math.PI * y) / scale));

function drawSurveyTile(ctx, coords, size) {
  const g = survey, p = palette();
  const scale = size.x * Math.pow(2, coords.z);
  const x0 = coords.x * size.x, y0 = coords.y * size.y;
  const west = mercLon(x0, scale), east = mercLon(x0 + size.x, scale);
  const north = mercLat(y0, scale), south = mercLat(y0 + size.y, scale);
  const level10 = lakeLevel() * 10;
  const iMin = Math.floor((west - g.west) / g.dlon), iMax = Math.floor((east - g.west) / g.dlon);
  const jMin = Math.floor((south - g.south) / g.dlat), jMax = Math.floor((north - g.south) / g.dlat);
  for (let j = jMin; j <= jMax; j++) {
    const runs = g.rowMap.get(j);
    if (!runs) continue;
    const yTop = mercY(g.south + (j + 1) * g.dlat, scale) - y0;
    const h = mercY(g.south + j * g.dlat, scale) - y0 - yTop;
    for (const [start, values] of runs) {
      const end = start + values.length - 1;
      if (end < iMin || start > iMax) continue;
      for (let i = Math.max(start, iMin); i <= Math.min(end, iMax); i++) {
        const depth = (level10 - values[i - start]) / 10;
        if (depth <= 0) continue;   // a bottom above today's lake level is dry, not water
        const xl = mercX(g.west + i * g.dlon, scale) - x0;
        const xr = mercX(g.west + (i + 1) * g.dlon, scale) - x0;
        ctx.fillStyle = p.survey[surveyBand(depth)];
        ctx.fillRect(xl, yTop, xr - xl + 0.6, h + 0.6);   // the overlap hides hairline seams between cells
      }
    }
  }
  // Where the surveys end: a faint dashed outline round each surveyed stretch, so it is plain where
  // the chart has real depths and where it only has the Corps' 0-9 ft / 9 ft+.
  ctx.save();
  ctx.strokeStyle = p.contour;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  for (const s of g.surveys) {
    for (const ring of s.outline || []) {
      ctx.beginPath();
      ring.forEach(([lon, lat], i) => {
        const x = mercX(lon, scale) - x0, y = mercY(lat, scale) - y0;
        if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      });
      ctx.stroke();
    }
  }
  ctx.restore();
  // Soundings -- the surveyor's own chosen spot depths -- once zoomed in far enough to read them.
  // Thinned to the shallowest in each ~34 px square, the square taken in global pixels so that a
  // label straddling two tiles is chosen, and drawn, identically by both.
  if (coords.z < 15 || !g.soundings.length) return;
  const BIN = 34, MARGIN = 40;
  const mlon = (MARGIN / scale) * 360;
  const best = new Map();
  for (const [lat, lon, e10] of g.soundings) {
    if (lon < west - mlon || lon > east + mlon || lat < south - mlon || lat > north + mlon) continue;
    const gx = mercX(lon, scale), gy = mercY(lat, scale);
    const key = `${Math.floor(gx / BIN)},${Math.floor(gy / BIN)}`;
    const cur = best.get(key);
    if (!cur || e10 > cur[2]) best.set(key, [gx, gy, e10]);
  }
  ctx.font = "italic 600 11px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 3;
  ctx.strokeStyle = p.textHalo;
  ctx.fillStyle = p.text;
  for (const [gx, gy, e10] of best.values()) {
    const depth = (level10 - e10) / 10;
    if (depth <= 0) continue;
    const text = String(Math.floor(depth));   // charts round soundings down: never promise water that isn't there
    ctx.strokeText(text, gx - x0, gy - y0);
    ctx.fillText(text, gx - x0, gy - y0);
  }
}

const SurveyLayer = L.GridLayer.extend({
  createTile(coords) {
    const tile = document.createElement("canvas");
    const size = this.getTileSize();
    const ratio = window.devicePixelRatio > 1 ? 2 : 1;
    tile.width = size.x * ratio;
    tile.height = size.y * ratio;
    if (survey && lakeLevel() != null && layerGroupOn("survey")) {
      const ctx = tile.getContext("2d");
      ctx.scale(ratio, ratio);
      drawSurveyTile(ctx, coords, size);
    }
    return tile;
  },
});
const surveyLayer = new SurveyLayer({ pane: "survey", minZoom: 10, updateWhenIdle: false, keepBuffer: 2 });

function surveyCell(i, j) {
  const runs = survey.rowMap.get(j);
  if (!runs) return null;
  for (const [start, values] of runs) if (i >= start && i < start + values.length) return values[i - start];
  return null;
}
// The surveyed depth at a point, and which survey it comes from, or null outside the surveys.
function surveyDepthAt(latlng) {
  if (!survey || lakeLevel() == null) return null;
  const cell = surveyCell(Math.floor((latlng.lng - survey.west) / survey.dlon), Math.floor((latlng.lat - survey.south) / survey.dlat));
  if (cell == null) return null;
  const newestFirst = survey.surveys.slice().reverse();
  const from = newestFirst.find((s) => s.outline.some((ring) => inRing(latlng.lng, latlng.lat, ring))) || null;
  return { depth: (lakeLevel() * 10 - cell) / 10, bottom: cell / 10, survey: from };
}

async function loadSurvey(area) {
  try {
    const res = await fetch(`/api/chart/${area}/survey`);
    if (!res.ok) return;
    const g = await res.json();
    g.rowMap = new Map(Object.entries(g.rows).map(([j, runs]) => [Number(j), runs]));
    delete g.rows;
    survey = g;
    await refreshChartSettings();
    if (!map.hasLayer(surveyLayer)) surveyLayer.addTo(map);
    else surveyLayer.redraw();
  } catch (e) {
    // No survey for this area, or unreadable: the chart works without it.
  }
}

async function refreshChartSettings() {
  try {
    const s = await (await fetch("/api/chart-settings")).json();
    if (s.lake_level_ft !== lakeLevelSetting) {
      lakeLevelSetting = s.lake_level_ft;
      if (survey) surveyLayer.redraw();
    }
  } catch (e) { /* keep what we have */ }
}
// Another screen may change the lake level; pick it up without a reload.
setInterval(() => { if (survey) refreshChartSettings(); }, 30000);

async function setLakeLevel(ft) {
  lakeLevelSetting = ft == null ? null : Math.round(ft * 10) / 10;
  await postJson("/api/chart-settings", { lake_level_ft: lakeLevelSetting });
  surveyLayer.redraw();
  if (typeof cursorLatLng !== "undefined" && cursorLatLng) renderCursor();
}

// ---------- Chart labels: the names a chart prints ----------
// Creek and bay names, islands and bends, towns, marinas, bridges, river miles. They are DOM
// labels in the marker pane, which leaflet-rotate keeps upright while the chart turns, so they
// always read the right way up in Heading Up. Only what fits is shown: the candidates on screen
// are placed in priority order, and one that would overlap a label already placed is left out.
const LABEL_STYLES = {
  town: { cls: "town", priority: 1, minZoom: 10 },
  water: { cls: "water", priority: 2, minZoom: 12 },
  place: { cls: "place", priority: 3, minZoom: 13 },
  facility: { cls: "facility", priority: 4, minZoom: 13 },
  bridge: { cls: "place", priority: 5, minZoom: 14 },
  mile: { cls: "mile", priority: 6, minZoom: 14 },
};
// Which chart layers carry names worth printing, and how.
const LABEL_LAYERS = {
  town: "town", land_region_point: "place", land_region: "place", water_name_point: "water",
  water_name_area: "water", rivers: "water", harbour: "facility", berths: "facility",
  small_craft_facility: "facility", small_craft_facility_area: "facility", harbour_facility: "facility",
  bridge: "bridge", distance_mark: "mile",
};
const MAX_LABELS = 70;
const labelLayer = L.layerGroup().addTo(map);
const labelMarkers = new Map();   // key -> marker currently shown
let chartLabels = [];

// A point inside a polygon to hang its name on: the centroid of its largest ring when that falls
// inside (a creek arm's centroid often lands on the bank), else the inside sample nearest to it.
function labelPoint(geom) {
  if (geom.type === "Point") return L.latLng(geom.coordinates[1], geom.coordinates[0]);
  const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
  let best = null, bestArea = 0;
  polys.forEach((rings) => {
    const ring = rings[0] || [];
    let a = 0, cx = 0, cy = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const f = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
      a += f; cx += (ring[j][0] + ring[i][0]) * f; cy += (ring[j][1] + ring[i][1]) * f;
    }
    if (Math.abs(a) > bestArea) { bestArea = Math.abs(a); best = { rings, cx: cx / (3 * a), cy: cy / (3 * a) }; }
  });
  if (!best) return null;
  const inside = (x, y) => inRing(x, y, best.rings[0]) && !best.rings.slice(1).some((h) => inRing(x, y, h));
  if (inside(best.cx, best.cy)) return L.latLng(best.cy, best.cx);
  const xs = best.rings[0].map((c) => c[0]), ys = best.rings[0].map((c) => c[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  let pick = null, pickD = Infinity;
  for (let a = 1; a < 12; a++) for (let b = 1; b < 12; b++) {
    const x = x0 + ((x1 - x0) * a) / 12, y = y0 + ((y1 - y0) * b) / 12;
    const d = (x - best.cx) ** 2 + (y - best.cy) ** 2;
    if (d < pickD && inside(x, y)) { pick = [x, y]; pickD = d; }
  }
  return pick ? L.latLng(pick[1], pick[0]) : null;
}

function buildChartLabels(layers) {
  const seen = new Set();
  chartLabels = [];
  Object.entries(layers).forEach(([name, { geojson }]) => {
    const style = LABEL_STYLES[LABEL_LAYERS[name]];
    if (!style || !geojson) return;
    (geojson.features || []).forEach((f) => {
      const props = f.properties || {};
      let text = String(props.Object_Name || "").trim();
      if (name === "town") text = text.replace(/,\s*[A-Z]{2}$/, "");
      if (name === "distance_mark") {
        const mile = parseFloat(props.Waterway_Distance);
        text = isFinite(mile) ? `Mile ${mile % 1 ? mile.toFixed(1) : mile}` : "";
      }
      if (!text || !f.geometry) return;
      const at = labelPoint(f.geometry);
      if (!at) return;
      // The same name twice close together (a creek drawn in two pieces) is printed once.
      const key = `${text}@${at.lat.toFixed(2)},${at.lng.toFixed(2)}`;
      if (seen.has(key)) return;
      seen.add(key);
      chartLabels.push({ key, text, at, ...style, w: text.length * (style.cls === "town" ? 8 : 6.6) + 8, h: 16 });
    });
  });
  chartLabels.sort((a, b) => a.priority - b.priority);
  placeLabels();
}

function labelIcon(label) {
  const span = document.createElement("span");
  span.textContent = label.text;   // chart data goes in as text, never as markup
  return L.divIcon({ className: `chart-label ${label.cls}`, html: span, iconSize: [0, 0] });
}

function placeLabels() {
  const show = new Set();
  if (layerGroupOn("names") && map.getSize().x > 10) {
    const z = map.getZoom(), size = map.getSize(), placed = [];
    for (const l of chartLabels) {
      if (z < l.minZoom) continue;
      const pt = map.latLngToContainerPoint(l.at);
      if (pt.x < -l.w || pt.y < -l.h || pt.x > size.x + l.w || pt.y > size.y + l.h) continue;
      const box = [pt.x - l.w / 2, pt.y - l.h / 2, pt.x + l.w / 2, pt.y + l.h / 2];
      if (placed.some((b) => b[0] < box[2] && box[0] < b[2] && b[1] < box[3] && box[1] < b[3])) continue;
      placed.push(box);
      show.add(l.key);
      if (placed.length >= MAX_LABELS) break;
    }
  }
  for (const [key, m] of labelMarkers) {
    if (!show.has(key)) { labelLayer.removeLayer(m); labelMarkers.delete(key); }
  }
  for (const l of chartLabels) {
    if (!show.has(l.key) || labelMarkers.has(l.key)) continue;
    const m = L.marker(l.at, { icon: labelIcon(l), interactive: false, keyboard: false, zIndexOffset: -1000 });
    labelLayer.addLayer(m);
    labelMarkers.set(l.key, m);
  }
}
// Re-placed when the view changes, but at most a few times a second: a chart following the boat
// or turning with it changes the view every frame.
let labelTimer = 0;
function scheduleLabels() {
  if (labelTimer) return;
  labelTimer = setTimeout(() => { labelTimer = 0; placeLabels(); }, 300);
}
map.on("moveend zoomend rotate resize", scheduleLabels);

// Night mode is now a real restyle rather than a CSS filter over a picture: same geometry, new colours.
function restyleChart() {
  redrawChartTiles();
  Object.entries(chartLayers).forEach(([name, { kind, layer }]) => {
    layer.eachLayer((lyr) => {
      if (lyr.feature && lyr.feature.geometry && lyr.feature.geometry.type.includes("Point")) {
        const p = palette();
        const props = lyr.feature.properties || {};
        if (kind === "buoy" || kind === "beacon") lyr.setStyle({ fillColor: aidColor(props, p), color: p.textHalo });
        else if (kind === "light") lyr.setStyle({ color: aidColor(props, p), fillColor: aidColor(props, p) });
        else if (kind === "danger_point") lyr.setStyle({ color: p.danger, fillColor: p.danger });
        else if (kind === "facility") lyr.setStyle({ color: p.textHalo, fillColor: p.facility });
        else if (kind === "notice") lyr.setStyle({ color: p.text });
        else lyr.setStyle({ color: p.coast, fillColor: p.coast });
      } else if (lyr.setStyle && lyr.feature) {
        lyr.setStyle(chartStyle(kind, lyr.feature));
      }
    });
  });
  mapEl.style.background = palette().chartBg;
  mapEl.dataset.chartMode = chartColorMode;   // the chart labels take their colours from this
  if (surveyLayer) surveyLayer.redraw();
}

function setChartLayerGroup(key, on) {
  chartLayerState[key] = on;
  try { localStorage.setItem("chartLayers", JSON.stringify(chartLayerState)); } catch (e) { /* storage unavailable */ }
  if (key === "survey") surveyLayer.redraw();
  if (key === "names") placeLabels();
  applyChartVisibility();
  redrawChartTiles();
}

// ---------- Identify: tap a charted feature and find out what it is ----------
// The thing a raster chart fundamentally cannot do. IENC carries the full S-57 attribution, so a
// beacon can report itself properly: name, what kind of mark it is, and its light characteristic
// in the notation actually printed on charts ("Fl(2)R 5s").
const CHART_KIND_LABELS = {
  buoy: "Buoy", beacon: "Beacon", light: "Light", distance_mark: "River mile marker",
  danger_point: "Hazard", landmark: "Landmark", depth: "Charted depth area", contour: "Depth contour",
  coastline: "Shoreline", track: "Recommended track", caution: "Caution area",
  danger: "Hazard area", area: "Land", hazard_line: "Hazard", facility: "Marina / boating facility",
  facility_area: "Marina", notice: "Notice mark", restricted: "Restricted area", water_area: "Water",
  dock: "Dock", structure: "Structure", survey_depth: "Surveyed depth",
};

// "Flashing" + "(2)" + "Red" + "5 Seconds" is how a chart writes Fl(2)R 5s.
function lightSignature(props) {
  const abbrev = { Flashing: "Fl", "Quick-Flashing": "Q", Occulting: "Oc", Isophase: "Iso",
                    "Fixed": "F", "Long-Flashing": "LFl", "Very Quick-Flashing": "VQ" };
  const ch = props.Light_Characteristics;
  if (!ch) return null;
  const colorLetter = { Red: "R", Green: "G", White: "W", Yellow: "Y" }[props.Color] || "";
  const group = (props.Signal_Group || "").replace(/\s/g, "");
  const period = (props.Signal_Period || "").match(/([\d.]+)/);
  return `${abbrev[ch] || ch}${group}${colorLetter}${period ? " " + period[1] + "s" : ""}`;
}

function chartFeatureSummary(kind, props) {
  const rows = [];
  const name = props.Object_Name || props.Information;
  const sig = lightSignature(props);
  if (sig) rows.push(["Light", sig]);
  if (props.Category_of_Lateral_Mark) rows.push(["Mark", props.Category_of_Lateral_Mark]);
  if (props.Category_of_Light && props.Category_of_Light !== "Unknown") rows.push(["Category", props.Category_of_Light]);
  if (props.Color && !sig) rows.push(["Colour", props.Color]);
  if (props.Beacon_Shape) rows.push(["Shape", props.Beacon_Shape.replace(/_/g, " ")]);
  if (props.Waterway_Distance != null && props.Waterway_Distance !== "") {
    rows.push(["Mile", `${props.Waterway_Distance} ${props.Horizontal_Units === "Statute Miles" ? "mi" : ""}`.trim()]);
  }
  // S-57 "Category of ..." attributes say what a marina, notice mark or restricted area is.
  Object.entries(props).forEach(([k, v]) => {
    if (!/^Category_of_/.test(k) || ["Category_of_Lateral_Mark", "Category_of_Light", "Category_of_Recommended_Track"].includes(k)) return;
    if (v && v !== "Unknown") rows.push([k.replace(/^Category_of_/, "").replace(/_/g, " "), String(v)]);
  });
  if (props.Restriction && props.Restriction !== "Unknown") rows.push(["Restriction", props.Restriction]);
  if (props.Information) rows.push(["Note", props.Information]);
  // The Corps chart gives this lake two depth areas: 0 to 2.74 m outside the channel, and "2.74 m
  // to unknown" inside it. Said as what they mean rather than as "0.0 ft to 9.0 ft" and nothing.
  if (kind === "depth") {
    const lo = parseFloat(props.Depth_Area_Value_1), hi = parseFloat(props.Depth_Area_Value_2);
    const f = (m) => `${Math.round(m * 3.28084)} ft`;
    if (isFinite(lo) && isFinite(hi)) rows.push(["Depth", `${f(lo)} to ${f(hi)}`]);
    else if (isFinite(lo)) rows.push(["Depth", `${f(lo)} or more`]);
  }
  if (kind === "survey_depth") {
    rows.push(["Depth", `${props.depth.toFixed(1)} ft`]);
    rows.push(["Lake level", `${props.level.toFixed(1)} ft`]);
    rows.push(["Bottom", `${props.bottom.toFixed(1)} ft (NAVD88)`]);
    if (props.survey) rows.push(["Surveyed", props.survey.date || "--"], ["Survey", props.survey.name]);
    rows.push(["Source", "US Army Corps of Engineers"]);
  }
  if (kind === "contour" && props.Value_of_Depth_Contour != null) {
    rows.push(["Contour", `${(parseFloat(props.Value_of_Depth_Contour) * 3.28084).toFixed(0)} ft`]);
  }
  if (props.Category_of_Recommended_Track) rows.push(["Track", props.Category_of_Recommended_Track]);
  if (props.Traffic_Flow) rows.push(["Traffic", props.Traffic_Flow]);
  // The USACE service's field is Vertical_Clearance (this used to read Vertical_Clearance_Value,
  // which does not exist, so no clearance was ever shown). Neither the records nor the service's
  // layer metadata say what unit it is in, and 16.5 ft and 16.5 m are very different answers under
  // a power line, so the number is shown with that said plainly rather than with a guessed unit.
  [["Clearance", props.Vertical_Clearance ?? props.Vertical_Clearance_Value],
   ["Clearance (closed)", props.Vertical_Clearance_Closed], ["Clearance (open)", props.Vertical_Clearance_Open]]
    .forEach(([label, v]) => {
      const n = parseFloat(v);
      if (isFinite(n)) rows.push([label, `${n} (unit not charted)`]);
    });
  if (props.Source_Dataset) rows.push(["Chart", props.Source_Dataset.replace(/\.000$/, "")]);
  return { title: name || CHART_KIND_LABELS[kind] || "Charted feature",
           subtitle: name ? CHART_KIND_LABELS[kind] || "" : "", rows };
}

// Panels that live *inside* the Leaflet container, so they travel with the chart between the Helm
// and the full Nav. Chart screen (attachMap moves #map between them) instead of covering the
// widgets around it: the feature readout, the no-chart notice, the measure readout.
//
// Being inside the container has a cost: Leaflet treats a tap on any child as a tap on the chart.
// Before this, closing the feature panel with its X also dropped a Go To waypoint underneath the
// button -- and since a Go To and a route are mutually exclusive, identifying a buoy mid-route and
// then closing the panel silently cancelled the route. disableClickPropagation stops the tap (and
// the mousedown/touchstart that would start a drag) at the panel's edge.
function mapPanel(id) {
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement("div");
    el.id = id;
    el.hidden = true;
  }
  if (el.parentElement !== mapEl) mapEl.appendChild(el);
  if (!el.dataset.mapPanel) {
    L.DomEvent.disableClickPropagation(el);
    L.DomEvent.disableScrollPropagation(el);
    // disableClickPropagation alone is not enough for a click. Leaflet decides whether a click
    // belongs to the map by walking up from the clicked element looking for this panel's flag --
    // and a button whose handler rebuilds the panel (Set Ref does) has been detached by then, so
    // the walk finds nothing and the chart takes the tap too. Stopping the click here works
    // regardless: an event's route is fixed when it is dispatched, so it still passes through
    // this element even after the button has gone.
    L.DomEvent.on(el, "click", L.DomEvent.stopPropagation);
    el.dataset.mapPanel = "1";
  }
  return el;
}

// A chart-less install used to render as a flat empty panel -- the #map background colour and
// nothing else -- which looks identical to a crash and tells you nothing. Says what is wrong and
// how to fix it instead.
function setChartNotice(msg) {
  if (!msg) { const el = document.getElementById("chartNotice"); if (el) el.hidden = true; return; }
  const el = mapPanel("chartNotice");
  el.textContent = msg;
  el.hidden = false;
}

function showChartFeature(layerName, kind, props, latlng) {
  const el = mapPanel("chartFeature");
  const { title, subtitle, rows } = chartFeatureSummary(kind, props);
  const body = rows.map(([k, v]) => `<div class="cf-row"><span>${k}</span><b>${v}</b></div>`).join("");
  el.innerHTML = `<div class="cf-head"><div><div class="cf-title">${title}</div>` +
    `${subtitle ? `<div class="cf-sub">${subtitle}</div>` : ""}</div>` +
    `<button class="cf-close" aria-label="Close">&times;</button></div>${body}` +
    (latlng ? '<div class="cf-actions"><button class="btn primary cf-goto">Go To</button></div>' : "");
  el.hidden = false;
  el.querySelector(".cf-close").addEventListener("click", () => (el.hidden = true));
  const go = el.querySelector(".cf-goto");
  if (go) go.addEventListener("click", async () => {
    el.hidden = true;
    await setWaypoint(latlng.lat, latlng.lng, title.slice(0, 60));
  });
}

// ---------- My Vessel: heading line and compass rose (Options -> Map layers & colors -> My Vessel) ----------
// Mirrors Garmin's own "Layers > My Vessel" menu: a heading line projects ahead of the boat, either a fixed
// distance or how far it will travel in a set time at its current speed, and a compass rose is a fixed-size
// ring around the boat marked with the compass points. Both are per-browser display preferences, like the
// chart layer toggles above, not shared server-side.
let vesselSettings = { headingLineOn: true, headingLineMode: "distance", headingLineNm: 0.3, headingLineMinutes: 10,
  compassRoseOn: false, rangeRingsOn: false, rangeRingSpacingNm: 0.25 };
try { Object.assign(vesselSettings, JSON.parse(localStorage.getItem("vesselSettings")) || {}); } catch (e) { /* storage unavailable */ }

function saveVesselSettings() {
  try { localStorage.setItem("vesselSettings", JSON.stringify(vesselSettings)); } catch (e) { /* storage unavailable */ }
}

function setVesselSetting(key, value) {
  vesselSettings[key] = value;
  saveVesselSettings();
  applyVesselSettings();
}

// A boat travelling at `bearingDeg` for `distNm` nautical miles: same flat-earth approximation the
// simulated GPS route uses (app/gps.py), fine at the short distances a heading line or a route leg covers.
function projectLatLng(lat, lon, bearingDeg, distNm) {
  const rad = Math.PI / 180;
  const dlat = (distNm / 60) * Math.cos(bearingDeg * rad);
  const dlon = (distNm / 60) * Math.sin(bearingDeg * rad) / Math.cos(lat * rad);
  return [lat + dlat, lon + dlon];
}

const compassRoseIcon = L.divIcon({
  className: "",
  html: `<svg class="compass-rose" viewBox="-50 -50 100 100">
    <circle cx="0" cy="0" r="44" fill="none" stroke="rgba(255,255,255,0.55)" stroke-width="1.5" stroke-dasharray="2 5"/>
    <g fill="rgba(255,255,255,0.9)" font-size="12" font-weight="700" text-anchor="middle">
      <text x="0" y="-32">N</text><text x="33" y="4.5">E</text><text x="0" y="41">S</text><text x="-33" y="4.5">W</text>
    </g>
    <line x1="0" y1="-44" x2="0" y2="-37" stroke="#ff4d4d" stroke-width="2.5"/>
  </svg>`,
  iconSize: [90, 90],
  iconAnchor: [45, 45],
});
// Marker icons do NOT turn with the chart -- leaflet-rotate puts the marker pane in its non-rotating
// pane (see screenUpAngle below) -- so a rose left alone would read N at the top of the screen in
// Heading and Course Up whichever way north actually was. It is turned to match the chart instead.
// (An earlier comment here claimed the opposite, and the rose was wrong in both rotated modes.)
const compassRoseMarker = L.marker([0, 0], { icon: compassRoseIcon, interactive: false, zIndexOffset: -1000 });

function orientCompassRose() {
  const svg = compassRoseMarker.getElement() && compassRoseMarker.getElement().querySelector("svg");
  if (svg) svg.style.transform = `rotate(${onScreen(0)}deg)`;
}

// Range rings: concentric circles at a fixed real-world spacing around the boat, so distance to
// anything on screen can be eyeballed without measuring it. Drawn in metres (L.circle, not
// L.circleMarker) so they scale with zoom the way real distance does -- the opposite of the
// compass rose above, which is deliberately a fixed size on screen.
const RANGE_RING_COUNT = 4;
const NM_TO_M = 1852;
const rangeRingGroup = L.layerGroup();
const rangeRings = Array.from({ length: RANGE_RING_COUNT }, () =>
  L.circle([0, 0], { radius: 0, fill: false, color: "rgba(255,255,255,0.4)", weight: 1, dashArray: "3 6", interactive: false }).addTo(rangeRingGroup));
const rangeRingLabel = L.marker([0, 0], {
  icon: L.divIcon({ className: "", html: '<span class="range-ring-label"></span>', iconSize: [70, 16], iconAnchor: [35, 8] }),
  interactive: false,
}).addTo(rangeRingGroup);

function updateRangeRings() {
  if (!vesselSettings.rangeRingsOn || shownLat == null) return;
  const spacing = vesselSettings.rangeRingSpacingNm;
  rangeRings.forEach((ring, i) => {
    ring.setLatLng([shownLat, shownLon]);
    ring.setRadius(spacing * (i + 1) * NM_TO_M);
  });
  // Label the outermost ring, due north of the boat, with the distance it actually represents.
  const outer = spacing * RANGE_RING_COUNT;
  rangeRingLabel.setLatLng(projectLatLng(shownLat, shownLon, 0, outer));
  const el = rangeRingLabel.getElement() && rangeRingLabel.getElement().querySelector(".range-ring-label");
  if (el) el.textContent = `${toDist(outer).toFixed(2)} ${UNITS[unit].dist}`;
}

const headingLine = L.polyline([], { color: "#ffcf40", weight: 2, opacity: 0.85, dashArray: "1 7" }).addTo(map);

function updateHeadingLine() {
  if (!vesselSettings.headingLineOn || shownLat == null) { headingLine.setLatLngs([]); return; }
  const sogKn = lastData && lastData.gps.has_fix ? lastData.gps.sog_kn || 0 : 0;
  const distNm = vesselSettings.headingLineMode === "time" ? sogKn * (vesselSettings.headingLineMinutes / 60) : vesselSettings.headingLineNm;
  if (!(distNm > 0)) { headingLine.setLatLngs([]); return; }
  headingLine.setLatLngs([[shownLat, shownLon], projectLatLng(shownLat, shownLon, shownHeading, distNm)]);
}

function applyVesselSettings() {
  if (vesselSettings.compassRoseOn) { compassRoseMarker.addTo(map); orientCompassRose(); }
  else map.removeLayer(compassRoseMarker);
  if (vesselSettings.rangeRingsOn) { rangeRingGroup.addTo(map); updateRangeRings(); }
  else map.removeLayer(rangeRingGroup);
  updateHeadingLine();   // no-ops safely until there's a first fix to draw from (see below)
}
// Not called yet here: updateHeadingLine() reads the eased boat position declared further down this file,
// which doesn't exist until that `let` runs. Applied once it does, alongside the other startup preferences.

// ---------- User Data: the recorded breadcrumb trail (Options -> Map layers & colors -> User Data) ----------
let trackVisible = true;
try { trackVisible = localStorage.getItem("trackVisible") !== "off"; } catch (e) { /* storage unavailable */ }

function setTrackVisible(on) {
  trackVisible = on;
  trackLine.setStyle({ opacity: on ? 0.85 : 0 });
  try { localStorage.setItem("trackVisible", on ? "on" : "off"); } catch (e) { /* storage unavailable */ }
}

async function clearTrack() {
  trackLine.setLatLngs([]);         // answer at once; the server confirms behind the scenes
  await fetch("/api/track", { method: "DELETE" });
}

// One map, shared by the Helm and Chart screens: it moves into whichever screen is showing.
const mapEl = $("map");
const mapObserver = new ResizeObserver(() => map.invalidateSize());
function attachMap(slot) {
  if (!slot) return;
  if (mapEl.parentElement !== slot) slot.appendChild(mapEl);
  mapObserver.disconnect();
  mapObserver.observe(slot);
  requestAnimationFrame(() => map.invalidateSize());
}

document.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => (b.dataset.zoom === "in" ? map.zoomIn() : map.zoomOut())));
// While Leaflet's own zoom animation is running (a CSS transform on the tile pane), latLngToContainerPoint()
// briefly disagrees with where things actually render -- calling panBy() from that window (the per-frame
// lockBoatFrame() below does, in Heading Up) fights the in-progress transform and the boat icon visibly
// snaps/glitches. isZooming skips those calls until the animation settles, then one corrective call on zoomend.
let isZooming = false;
map.on("zoomstart", () => { isZooming = true; });
map.on("zoomend", () => { isZooming = false; lockBoatFrame(); applyChartVisibility(); });

let nightMode = false;   // true only for the full night palette; the quick Night button and its label stay binary
let chartColorMode = "day";
const trackLine = L.polyline([], { weight: 3, opacity: 0.85 }).addTo(map);

// ---------- My Depth Map (a simplified Quickdraw): colored dots at recorded depth samples ----------
let quickdrawRecording = false;
const quickdrawLayer = L.layerGroup().addTo(map);
let quickdrawPointCount = 0;
function quickdrawColor(depthFt) {
  if (depthFt < 5) return "#ff3b30";
  if (depthFt < 15) return "#ff9500";
  if (depthFt < 30) return "#ffe135";
  if (depthFt < 50) return "#34c759";
  return "#2fa8e0";
}
function clearQuickdrawDots() {
  quickdrawLayer.clearLayers();
  quickdrawPointCount = 0;
}
async function loadQuickdrawDots() {
  const res = await (await fetch("/api/quickdraw")).json();
  quickdrawRecording = res.enabled;
  if (res.points.length === quickdrawPointCount) return;  // nothing new to draw
  clearQuickdrawDots();
  res.points.forEach((p) => {
    L.circleMarker([p.lat, p.lon], { radius: 4, weight: 0, fillOpacity: 0.85, fillColor: quickdrawColor(p.depth_ft) }).addTo(quickdrawLayer);
  });
  quickdrawPointCount = res.points.length;
}
loadQuickdrawDots();
setInterval(() => { if (quickdrawRecording) loadQuickdrawDots(); }, 5000);  // only poll while actually recording
function setChartMode(mode) {
  chartColorMode = mode === "dusk" ? "dusk" : mode === "night" ? "night" : "day";
  nightMode = chartColorMode === "night";
  // Night used to be a CSS filter smeared over rendered tiles, which dimmed the boat and the track
  // along with the chart. With vectors it's what it should be: the same geometry, drawn in the
  // night palette, while everything on top keeps its own colours.
  restyleChart();
  trackLine.setStyle({ color: chartColorMode === "day" ? "#7a2fbf" : "#4db3ff" });
  try { localStorage.setItem("chartMode", chartColorMode); } catch (e) { /* storage unavailable */ }
  document.dispatchEvent(new CustomEvent("chartmode"));
}

// ---------- Chart orientation: North Up (default) or Heading Up ----------
// Heading Up turns the whole chart so the boat's current heading points to the top of the screen (the boat
// icon then always points straight up, locked about a third of the way up from the bottom of the map, so
// there's more chart ahead of the boat than behind it); North Up leaves the chart alone and rotates only
// the boat icon. map.setBearing() is from leaflet-rotate: it takes the compass direction that should point
// up, which is the *opposite* sense from a heading (confirmed against the plugin's own source, not guessed),
// hence 360-heading.
const ORIENTS = ["north", "heading", "course"];
const ORIENT_LABELS = { north: "North Up", heading: "Heading Up", course: "Course Up" };
let chartOrient = "north";
try { if (ORIENTS.includes(localStorage.getItem("chartOrient"))) chartOrient = localStorage.getItem("chartOrient"); } catch (e) { /* storage unavailable */ }
// Both rotating modes share the same machinery -- spin the chart, lock the boat into its frame,
// stop counter-rotating the boat icon -- and differ only in which angle goes to the top.
const rotatedUp = () => chartOrient !== "north";
let rotateChoice = chartOrient !== "north" ? chartOrient : "heading";   // what the chart button turns to from North Up
try { if (["heading", "course"].includes(localStorage.getItem("chartRotateChoice"))) rotateChoice = localStorage.getItem("chartRotateChoice"); } catch (e) { /* storage unavailable */ }

// The compass direction currently pointing up the screen: 0 in North Up, the (eased) heading in
// Heading Up, the (eased) leg course in Course Up.
//
// Every icon that stands for a real-world direction -- the boat, the AIS targets, the compass
// rose -- has to be rotated by (its true direction - this). leaflet-rotate keeps the marker pane
// in its *non-rotating* pane (see _initPanes in static/vendor/leaflet-rotate.js: markerPane is
// created under norotatePane, and rotateWithView defaults to false), so marker icons stay upright
// on screen while the chart turns underneath them. Rotating an icon by its bare compass angle is
// therefore only right in North Up; in the other two modes it is off by exactly the chart's
// rotation. The compass rose showed N at the top of the screen in Heading Up, and AIS targets
// pointed the wrong way, for that reason.
function screenUpAngle() {
  if (chartOrient === "heading") return shownHeading;
  if (chartOrient === "course") return shownCourse;
  return 0;
}
const onScreen = (trueDeg) => (((trueDeg - screenUpAngle()) % 360) + 360) % 360;

// Course Up points the *intended leg* at the top of the screen, not the bow. That is the whole
// difference: Heading Up re-aims the chart with every wiggle of the boat, which on a lake at
// idle is a slow constant swim, while Course Up holds the leg still and lets the boat icon swing
// against it. With nothing to follow there is no course, so it falls back to the direction of
// travel and finally to the heading -- never leaving the chart stuck pointing somewhere stale.
function courseUpAngle() {
  const nav = activeNav(lastData);
  if (nav && nav.course_deg != null) return nav.course_deg;
  if (nav && nav.bearing_deg != null) return nav.bearing_deg;
  const fix = lastData && lastData.gps && lastData.gps.has_fix ? lastData.gps : null;
  if (fix && fix.sog_kn > 0.5 && fix.cog_deg != null) return fix.cog_deg;
  return shownHeading;
}

// ---------- Smoothly animated boat: heading and position ----------
// GPS fixes arrive once a second; heading is a simple wrapped-angle ease toward the latest one (a
// first-order lag, the same idea as the gauge springs in dials.js), fine since it settles well
// within that second either way. Position used to be eased the same way -- chasing a target point
// that jumps once a second -- which looked fine at a lake-idle 6-9 kn (a small jump, eased away
// quickly) but turned into a visible lurch-then-stall at a 40 mph cruise: the exponential ease is
// front-loaded (fast at first, asymptotically slower), so it sprints to cover the whole second's
// travel early and then visibly creeps/sits for the remainder of that second waiting on the next
// fix -- "ticks forward, stops, goes again." Position is now dead reckoning instead: project
// forward every frame from the last fix's own speed and course (projectLatLng, same flat-earth
// math the simulated GPS route and the heading line already use), which is constant-velocity by
// construction -- no per-second speedup/slowdown to see. Each new fix only has to correct for
// however far reality actually diverged from that projection (normally small), not replay the
// whole second's travel, so that correction is eased out fast (CORRECTION_OMEGA) rather than
// slowly chased. Once a fix has arrived the loop just keeps running rather than stopping when
// "settled": a real boat is essentially always moving or turning a little.
let shownHeading = 0;
let targetHeading = 0;
let shownCourse = 0;   // eased Course Up angle, see courseUpAngle()
let headingEverSet = false;   // the very first heading is applied at once, not spun up to from zero
let shownLat = null, shownLon = null;
let drLat = null, drLon = null, drCogDeg = 0, drSogKn = 0, drAnchorT = 0;  // dead-reckoning anchor
let correctionLat = 0, correctionLon = 0;  // the small, fast-decaying gap left over from the last fix
let boatAnimId = 0;
let boatLastT = 0;
const HEADING_OMEGA = 3.5;     // a reading that arrives once a second, eased to settle well within that second
const CORRECTION_OMEGA = 8;    // absorb a fix's small correction within a few hundred ms, not chase it for a full second

function angleDiff(target, current) {
  // shortest signed distance from current to target, in (-180, 180]
  const d = (((target - current + 180) % 360) + 360) % 360 - 180;
  return d === -180 ? 180 : d;
}

function boatTick(now) {
  const dt = Math.min(0.05, (now - boatLastT) / 1000 || 0);
  boatLastT = now;

  const diff = angleDiff(targetHeading, shownHeading);
  shownHeading = Math.abs(diff) < 0.02 ? targetHeading : (((shownHeading + diff * Math.min(1, HEADING_OMEGA * dt)) % 360) + 360) % 360;

  const elapsedH = (now - drAnchorT) / 3600000;
  const [drNowLat, drNowLon] = projectLatLng(drLat, drLon, drCogDeg, drSogKn * elapsedH);
  const decay = Math.max(0, 1 - CORRECTION_OMEGA * dt);
  correctionLat *= decay;
  correctionLon *= decay;
  shownLat = drNowLat + correctionLat;
  shownLon = drNowLon + correctionLon;

  boatMarker.setLatLng([shownLat, shownLon]);
  compassRoseMarker.setLatLng([shownLat, shownLon]);
  updateHeadingLine();
  // Course Up eases toward its target the same way heading does, so advancing to the next leg
  // of a route turns the chart rather than snapping it round.
  const courseDiff = angleDiff(courseUpAngle(), shownCourse);
  shownCourse = Math.abs(courseDiff) < 0.02 ? courseUpAngle()
    : (((shownCourse + courseDiff * Math.min(1, HEADING_OMEGA * dt)) % 360) + 360) % 360;

  if (rotatedUp()) {
    // Only when it has actually moved: every setBearing fires a "rotate" that repositions every
    // marker and label, and on a steady course the eased heading stops changing.
    const bearing = (360 - screenUpAngle()) % 360;
    if (Math.abs(angleDiff(bearing, map.getBearing())) > 0.01) map.setBearing(bearing);
  } else if (drSogKn >= 2) {
    // Look-ahead follows the course over the ground, eased so a turn slides the chart across
    // rather than jumping it; below 2 kn the course is mostly noise, so the last one is kept.
    const c = (drCogDeg * Math.PI) / 180, k = Math.min(1, 1.5 * dt);
    lookAheadX += (Math.sin(c) - lookAheadX) * k;
    lookAheadY += (Math.cos(c) - lookAheadY) * k;
  }
  lockBoatFrame();
  // Heading Up: always straight up. Course Up: the boat's angle to the leg, which is exactly what
  // shows you crabbing off track. North Up: the plain heading. All three are this one expression.
  const svg = boatMarker.getElement() && boatMarker.getElement().querySelector("svg");
  if (svg) svg.style.transform = `rotate(${onScreen(shownHeading)}deg)`;
  // The chart turns continuously in the rotated modes, so the other direction-bearing icons have
  // to follow every frame, not only when a new AIS frame or setting arrives.
  if (rotatedUp()) { orientCompassRose(); orientAisIcons(); }

  boatAnimId = requestAnimationFrame(boatTick);
}

function setBoatTarget(lat, lon, heading, cogDeg, sogKn) {
  const now = performance.now();
  if (shownLat == null) { shownLat = lat; shownLon = lon; correctionLat = 0; correctionLon = 0; }  // first fix: appear in place
  else { correctionLat = shownLat - lat; correctionLon = shownLon - lon; }  // continue from wherever it's currently shown
  drLat = lat;
  drLon = lon;
  drCogDeg = cogDeg || 0;
  drSogKn = sogKn || 0;
  drAnchorT = now;
  targetHeading = ((heading % 360) + 360) % 360;
  if (!headingEverSet) { headingEverSet = true; shownHeading = targetHeading; }
  if (!boatAnimId) {
    boatLastT = now;
    boatAnimId = requestAnimationFrame(boatTick);
  }
}

// Keeps the boat horizontally centered and one-third of the way up from the bottom of the map, using the
// eased position above rather than the raw last fix, so a noisy GPS reading can't make it jump either. This
// used to be two map.setView() calls (recenter on the boat, then recenter again on the point 1/3 up) run once
// a second — cheap enough per call, but leaflet-rotate spins the whole chart around the CENTER of the
// container, not around the boat's locked position, so between those once-a-second corrections the boat
// visibly swung through an arc as the bearing eased toward a new heading. A single map.panBy() is cheap enough
// to call every animation frame instead, right alongside the heading update, so the boat never leaves its
// locked screen position at all. panBy(offset) moves map CONTENT the opposite way from `offset` (verified
// empirically against a live map: a panBy of (0, +50) moved a fixed point 50px UP the screen), so to slide the
// boat from where it currently renders to where it belongs, the offset passed in is current-minus-desired.
//
// It also does North Up now, where the boat sits at the centre: the chart follows the boat, the
// way a chartplotter's does, until the chart is dragged -- then it stays put and the Center
// button appears. (North Up used to let the boat drift to the edge of the screen and then jump
// the whole chart to catch up.)
//
// In North Up the boat sits a third of the way in from the edge it is coming from ("look-ahead"),
// so two thirds of the screen is always the water ahead: the bottom third heading north, the left
// third heading east. It used to sit dead centre, with as much chart behind as in front.
let lookAheadX = 0, lookAheadY = 0;   // eased direction of travel, screen axes; (0, 0) = centred
function lockBoatFrame() {
  if (!following || shownLat == null || isZooming) return;
  const size = map.getSize();
  if (size.x < 10 || size.y < 10) return;  // not laid out yet (e.g. its screen isn't showing)
  const boatPt = map.latLngToContainerPoint(L.latLng(shownLat, shownLon));
  const desiredPt = rotatedUp() ? L.point(size.x / 2, (size.y * 2) / 3)
    : L.point(size.x / 2 - (lookAheadX * size.x) / 6, size.y / 2 + (lookAheadY * size.y) / 6);
  const delta = boatPt.subtract(desiredPt);
  // panBy rounds to whole pixels, so anything under half a pixel would be a no-op move event.
  if (Math.abs(delta.x) >= 0.5 || Math.abs(delta.y) >= 0.5) map.panBy(delta, { animate: false });
}

// ---------- Following the boat, and the Center button ----------
let following = true;
function setFollowing(on) {
  following = on;
  document.querySelectorAll('[data-act="recenter"]').forEach((b) => { b.hidden = on; });
}
map.on("dragstart", () => setFollowing(false));   // only a finger (or mouse) dragging the chart
document.querySelectorAll('[data-act="recenter"]').forEach((b) => b.addEventListener("click", () => centerOnBoat()));

// The chart button says which way is up, the way a chartplotter labels its orientation, rather
// than showing a compass-and-arrow icon that looked like a "center on the boat" button.
const ORIENT_SHORT = { north: "N\u2191", heading: "H\u2191", course: "C\u2191" };
function syncOrientUI() {
  document.querySelectorAll(".zoom button.orient").forEach((b) => {
    b.classList.toggle("active", rotatedUp());
    b.textContent = ORIENT_SHORT[chartOrient];
    b.title = `${ORIENT_LABELS[chartOrient]} (tap for ${rotatedUp() ? "North Up" : ORIENT_LABELS[rotateChoice]})`;
    b.setAttribute("aria-label", `Chart orientation: ${ORIENT_LABELS[chartOrient]}`);
  });
  const opt = document.getElementById("optOrient");
  if (opt) opt.textContent = ORIENT_LABELS[chartOrient];
}

function setChartOrient(mode) {
  chartOrient = ORIENTS.includes(mode) ? mode : "north";
  if (rotatedUp()) {
    rotateChoice = chartOrient;
    try { localStorage.setItem("chartRotateChoice", rotateChoice); } catch (e) { /* storage unavailable */ }
    shownCourse = courseUpAngle();   // enter Course Up already aimed, rather than swinging round from 0
    map.setBearing((360 - (chartOrient === "course" ? shownCourse : shownHeading)) % 360);
    lockBoatFrame();
  } else {
    map.setBearing(0);
    if (shownLat != null && following) map.panTo([shownLat, shownLon]);  // north-up expects the boat back at plain center
  }
  // North Up gets no per-frame orienting (nothing is turning), so set these once on the way in.
  orientCompassRose();
  orientAisIcons();
  syncOrientUI();
  try { localStorage.setItem("chartOrient", chartOrient); } catch (e) { /* storage unavailable */ }
}
// Options cycles through all three. The on-chart button is a one-press toggle between North Up and
// whichever rotating mode was last chosen: it used to cycle all three, and since Heading Up and
// Course Up look the same on a straight run, the middle press seemed to do nothing -- getting back
// to North Up took two.
const nextOrient = () => ORIENTS[(ORIENTS.indexOf(chartOrient) + 1) % ORIENTS.length];
document.querySelectorAll('[data-act="orient"]').forEach((b) => b.addEventListener("click", () => setChartOrient(rotatedUp() ? "north" : rotateChoice)));
syncOrientUI();  // the saved preference (read into chartOrient above) needs applying to the button/Options too, not just the map

// a top-down boat, rotated to the heading
const boatIcon = L.divIcon({
  className: "",
  html: `<svg class="boat-marker" viewBox="0 0 30 30"><path d="M15 2C20 8 22.5 14 22.5 22V27H7.5V22C7.5 14 10 8 15 2Z" fill="#fff" stroke="#0a0a0b" stroke-width="1.6"/><path d="M15 9V24" stroke="#0b4f97" stroke-width="2.2"/></svg>`,
  iconSize: [30, 30],
  iconAnchor: [15, 15],
});
const boatMarker = L.marker([36.306, -86.563], { icon: boatIcon }).addTo(map);
let waypointMarker = null;
let wpLine = null;
let hasFitted = false;

let savedMode = "day";
try { savedMode = localStorage.getItem("chartMode") || "day"; } catch (e) { /* storage unavailable */ }
setChartMode(savedMode);
setTrackVisible(trackVisible);
applyVesselSettings();
initCharts();   // load whatever charts are on disk; the rest of the dashboard works without them

// Back to the boat, and following it again. Not animated: an animated pan that is still running
// when an Options panel closes and the chart resizes ends up off-centre.
function centerOnBoat() {
  setFollowing(true);
  if (shownLat == null) return;
  if (rotatedUp()) lockBoatFrame();
  else map.setView([shownLat, shownLon], map.getZoom(), { animate: false });
}

// A Go To follows the channel: the route is planned here, where the chart is (guidance.js), and
// the server steers along it leg by leg. With no fix, no chart, or no way through the water, it is
// the straight line it always was -- and the chart says so.
async function setWaypoint(lat, lon, name = "WP") {
  const fix = lastData && lastData.gps && lastData.gps.has_fix ? lastData.gps : null;
  let path = null;
  if (fix && typeof planGuidedPath === "function") {
    setChartNotice("Planning a route along the channel\u2026");
    await new Promise((r) => setTimeout(r, 30));   // let the notice paint before the search runs
    try { path = planGuidedPath(fix.lat, fix.lon, lat, lon); } catch (e) { path = null; }
    setChartNotice(path ? null : "No route through the water found: going in a straight line");
    if (!path) setTimeout(() => setChartNotice(null), 5000);
  }
  await postJson("/api/waypoint", { lat, lon, name, path });
}
const clearWaypoint = () => fetch("/api/waypoint", { method: "DELETE" });
map.on("click", (e) => {
  if (measureClick(e.latlng)) return;   // the ruler takes the tap while it is out
  const fe = document.getElementById("chartFeature");
  if (fe) fe.hidden = true;   // a tap elsewhere dismisses a stale feature readout
  setCursor(e.latlng);
});

// ---------- The chart cursor ----------
// A GPSMAP does not navigate the moment the chart is touched: a tap drops a cursor, the info bar
// shows how far and in which direction it is and what is charted there, and nothing changes until
// "Go To" is pressed. This used to go straight to a Go To -- and since a Go To and a route are
// mutually exclusive, one stray tap in a chop replaced the destination and cancelled the route.
let cursorLatLng = null;
const cursorMarker = L.marker([0, 0], {
  icon: L.divIcon({ className: "", iconSize: [34, 34], iconAnchor: [17, 17],
    html: '<svg class="chart-cursor" viewBox="-17 -17 34 34"><circle r="9" fill="none" stroke="#fff" stroke-width="2.5"/>' +
          '<circle r="9" fill="none" stroke="#e0202b" stroke-width="1.2"/><path d="M0 -16v8M0 8v8M-16 0h8M8 0h8" stroke="#fff" stroke-width="2"/></svg>' }),
  interactive: false, zIndexOffset: 1000,
});
const cursorBar = mapPanel("cursorBar");

// Ray-cast point-in-polygon on GeoJSON [lng, lat] rings; holes after the outer ring.
function inRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function inPolygonGeom(geom, x, y) {
  const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
  return polys.some((rings) => rings.length && inRing(x, y, rings[0]) && !rings.slice(1).some((h) => inRing(x, y, h)));
}
// The charted areas under a point, from the layers worth naming, in this order.
const CURSOR_AREAS = ["bridge", "dam", "caution", "depth_area"];
// A feature's bounding box, worked out once: the quick test before the ray cast.
function featureBBox(f) {
  if (f._bbox) return f._bbox;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const walk = (c) => {
    if (typeof c[0] === "number") { x0 = Math.min(x0, c[0]); x1 = Math.max(x1, c[0]); y0 = Math.min(y0, c[1]); y1 = Math.max(y1, c[1]); }
    else c.forEach(walk);
  };
  if (f.geometry) walk(f.geometry.coordinates);
  return (f._bbox = [x0, y0, x1, y1]);
}
function areasAt(latlng) {
  const found = [];
  const x = latlng.lng, y = latlng.lat;
  CURSOR_AREAS.forEach((name) => {
    const entry = chartFeatures[name];
    if (!entry) return;
    entry.features.forEach((f) => {
      const b = featureBBox(f);
      if (x < b[0] || x > b[2] || y < b[1] || y > b[3]) return;
      if (f.geometry && inPolygonGeom(f.geometry, x, y)) found.push({ name, kind: entry.kind, props: f.properties || {} });
    });
  });
  return found;
}

function setCursor(latlng) {
  cursorLatLng = latlng;
  cursorMarker.setLatLng(latlng).addTo(map);
  renderCursor();
}

function clearCursor() {
  cursorLatLng = null;
  map.removeLayer(cursorMarker);
  cursorBar.hidden = true;
}

function cursorRangeHtml() {
  const fix = lastData && lastData.gps && lastData.gps.has_fix ? lastData.gps : null;
  if (!fix) return '<span class="cb-brg">No GPS fix</span>';
  const from = L.latLng(fix.lat, fix.lon);
  return `<b>${toDist(map.distance(from, cursorLatLng) / 1852).toFixed(2)}</b> ${UNITS[unit].dist}` +
    `<span class="cb-brg">${Math.round(bearingBetween(from, cursorLatLng)) % 360}°</span>`;
}

// Built once per cursor position. Only the range text is refreshed after that (below): rebuilding
// the buttons every second would swap a button out from under a finger that is mid-press, and a
// Go To that silently does nothing is exactly the kind of failure nobody notices until it matters.
function renderCursor() {
  if (!cursorLatLng) return;
  const areas = areasAt(cursorLatLng);
  // A surveyed depth leads, when the cursor is on one: it is the real number.
  const sd = surveyDepthAt(cursorLatLng);
  if (sd && sd.depth > 0) {
    areas.unshift({ name: "survey", kind: "survey_depth",
                    props: { depth: sd.depth, bottom: sd.bottom, level: lakeLevel(), survey: sd.survey } });
  }
  const lines = areas.map((a, i) => {
    const { title, rows } = chartFeatureSummary(a.kind, a.props);
    const depth = rows.find(([k]) => k === "Depth");
    const text = !depth ? title
      : a.kind === "survey_depth" ? `Depth <b>${depth[1]}</b> <small>surveyed${a.props.survey && a.props.survey.date ? " " + a.props.survey.date.slice(0, 4) : ""}</small>`
        : `Charted depth ${depth[1]}`;
    return `<button class="cb-area" data-i="${i}">${text}<span>›</span></button>`;
  }).join("");
  cursorBar.innerHTML =
    `<div class="cb-top"><div class="cb-range">${cursorRangeHtml()}</div>` +
    '<button class="cf-close cb-close" aria-label="Close">&times;</button></div>' +
    `<div class="cb-pos">${fmtCoord(cursorLatLng.lat, true)}&nbsp;&nbsp;${fmtCoord(cursorLatLng.lng, false)}</div>` +
    lines +
    '<div class="cf-actions"><button class="btn primary cb-goto">Go To</button><button class="btn cb-save">Save Waypoint</button></div>';
  cursorBar.hidden = false;
  cursorBar.querySelector(".cb-close").addEventListener("click", clearCursor);
  cursorBar.querySelectorAll(".cb-area").forEach((b) => b.addEventListener("click", () => {
    const a = areas[Number(b.dataset.i)];
    clearCursor();   // the detail panel takes the cursor bar's place rather than stacking on it
    showChartFeature(a.name, a.kind, a.props);
  }));
  cursorBar.querySelector(".cb-goto").addEventListener("click", async () => {
    const at = cursorLatLng;
    clearCursor();
    await setWaypoint(at.lat, at.lng);
    if (typeof openPanel === "function") openPanel("waypoint");
  });
  cursorBar.querySelector(".cb-save").addEventListener("click", async () => {
    const at = cursorLatLng;
    clearCursor();
    await postJson("/api/waypoints", { lat: at.lat, lon: at.lng });
  });
}
// Range and bearing from the boat keep up as the boat moves; once a second is plenty for reading.
setInterval(() => {
  const el = cursorLatLng && cursorBar.querySelector(".cb-range");
  if (el) el.innerHTML = cursorRangeHtml();
}, 1000);

// ---------- Measure distance (the chart's ruler button) ----------
// A GPSMAP's "Measure Distance". The reference starts at the boat and follows it; each tap on the
// chart moves the far end, and the range and bearing from reference to end read out at the top.
// "Set Ref" pins the reference to the current end instead, for measuring between two arbitrary
// points, and "From Boat" puts it back.
//
// Built for a finger. An earlier version re-anchored on every tap and relied on mouse hover to
// show the live reading, so on a touchscreen -- which has no hover -- every tap set both ends to
// the same point and it read 0.00 forever. A desktop mouse hid that completely.
let measureOn = false;
let measureRef = null;   // null = the reference is the boat itself, following it
let measureEnd = null;   // null = nothing tapped yet
const measureLine = L.polyline([], { color: "#39d0d8", weight: 2, dashArray: "6 4", interactive: false });
const measureEnds = L.layerGroup();
const measureReadout = mapPanel("measureReadout");

function bearingBetween(a, b) {
  const rad = Math.PI / 180;
  const dLon = (b.lng - a.lng) * rad;
  const y = Math.sin(dLon) * Math.cos(b.lat * rad);
  const x = Math.cos(a.lat * rad) * Math.sin(b.lat * rad) - Math.sin(a.lat * rad) * Math.cos(b.lat * rad) * Math.cos(dLon);
  return (Math.atan2(y, x) / rad + 360) % 360;
}

const measureActive = () => measureOn;
const measureFrom = () => measureRef || (shownLat == null ? null : L.latLng(shownLat, shownLon));

function setMeasure(on) {
  measureOn = on;
  measureRef = null;
  measureEnd = null;
  if (on) { measureLine.addTo(map); measureEnds.addTo(map); }
  else { map.removeLayer(measureLine); map.removeLayer(measureEnds); }
  document.querySelectorAll('[data-act="measure"]').forEach((b) => b.classList.toggle("active", on));
  renderMeasure();
}

function measureText(from) {
  // Leaflet's own great-circle distance, in metres: the ruler agrees with the nav fields because
  // both are great-circle rather than one being a flat-earth shortcut.
  const nm = map.distance(from, measureEnd) / 1852;
  return `<b>${toDist(nm).toFixed(2)}</b> ${UNITS[unit].dist}` +
    `<span>${Math.round(bearingBetween(from, measureEnd)) % 360}°</span>`;
}

// Rebuilt only when the state changes (a tap, Set Ref, From Boat). The once-a-second refresh
// below touches the numbers alone, never the button, for the same reason as the cursor bar.
function renderMeasure() {
  if (!measureOn) { measureReadout.hidden = true; return; }
  const from = measureFrom();
  measureReadout.hidden = false;
  if (!from || !measureEnd) {
    measureLine.setLatLngs([]);
    measureEnds.clearLayers();
    measureReadout.innerHTML = `<span>${measureRef ? "Tap the chart to measure from the reference" : "Tap the chart to measure from the boat"}</span>`;
    return;
  }
  measureLine.setLatLngs([from, measureEnd]);
  measureEnds.clearLayers();
  [from, measureEnd].forEach((p, i) => L.circleMarker(p, {
    radius: 5, color: "#39d0d8", weight: 2, fillColor: i ? "#39d0d8" : "#0b0c0e", fillOpacity: 1, interactive: false,
  }).addTo(measureEnds));
  measureReadout.innerHTML = `<span class="mr-text">${measureText(from)}</span>` +
    `<button class="btn mr-ref">${measureRef ? "From Boat" : "Set Ref"}</button>`;
  measureReadout.querySelector(".mr-ref").addEventListener("click", () => {
    if (measureRef) measureRef = null;                          // back to measuring from the boat
    else { measureRef = measureEnd; measureEnd = null; }        // this point becomes the reference
    renderMeasure();
  });
}

// The chart's tap goes here instead of dropping a waypoint while the ruler is out.
function measureClick(latlng) {
  if (!measureOn) return false;
  measureEnd = latlng;
  renderMeasure();
  return true;
}

// With the boat as the reference the line has to leave from where the boat is drawn, which moves
// every frame; boatTick calls this. Nothing to do once the reference is a fixed point.
function followMeasureRef() {
  if (measureOn && !measureRef && measureEnd && shownLat != null) {
    measureLine.setLatLngs([[shownLat, shownLon], measureEnd]);
    const first = measureEnds.getLayers()[0];
    if (first) first.setLatLng([shownLat, shownLon]);
  }
}

document.querySelectorAll('[data-act="measure"]').forEach((b) =>
  b.addEventListener("click", () => setMeasure(!measureActive())));
// The distance text is refreshed once a second rather than every frame -- it is read, not watched.
setInterval(() => {
  const el = measureOn && !measureRef && measureEnd && measureFrom() && measureReadout.querySelector(".mr-text");
  if (el) el.innerHTML = measureText(measureFrom());
}, 1000);

function fmtCoord(v, isLat) {
  const dir = isLat ? (v >= 0 ? "N" : "S") : v >= 0 ? "E" : "W";
  const abs = Math.abs(v);
  const deg = Math.floor(abs);
  return `${deg}° ${((abs - deg) * 60).toFixed(3)}' ${dir}`;
}

function renderGps(gps) {
  if (!gps.has_fix) return;  // never move the boat (or the chart) to a position we don't have
  if (!hasFitted) {
    map.setView([gps.lat, gps.lon], 14);   // a sensible starting zoom/position either way, refined below in Heading Up
    hasFitted = true;
  }
  setBoatTarget(gps.lat, gps.lon, gps.heading_deg || 0, gps.cog_deg, gps.sog_kn);
  animBind("sog", toSpeed(gps.sog_kn), 1);
  animBind("hdg", gps.heading_deg, 0);
}

// A ~600-point polyline was getting fully re-projected and redrawn every second regardless of
// whether it had actually grown, and doing that while a zoom animation is also transitioning the
// tile pane's pixel origin made the whole track visibly swim/distort -- "freaks out" while zooming.
// Skip the rebuild when nothing changed, and defer it until the zoom settles (isZooming, the same
// flag lockBoatFrame() already respects) rather than fighting the in-progress transform.
let trackPointCount = 0;
function renderTrack(track) {
  if (!track || !track.length || track.length === trackPointCount || isZooming) return;
  trackPointCount = track.length;
  trackLine.setLatLngs(track.map((p) => [p.lat, p.lon]));
}

// ---------- Editable data overlays (Speed/Depth/... boxes on the Helm and Chart screens) ----------
// Matches a real GPSMAP's "Edit Overlays": every box shows one data field, and any box can be
// tapped to swap in a different one. Which fields are picked is a per-browser, per-screen
// preference (localStorage), like the other display settings this session added.
function fmtClockTime(value) {
  if (!value) return "--";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// A GPSMAP carries something like forty data fields and sorts them into groups in its "Edit
// Overlays" picker. A flat alphabetical list is not something anyone navigates while driving a
// boat, so every field here declares the group it belongs to and the picker renders headings.
//
// Fields that depend on something not currently true -- a nav field with no Go To running, a trip
// field with no trip started, anything at all without a GPS fix -- return null and read "--",
// which is what a real unit does rather than showing a stale or invented number.
const activeNav = (d) => (d && (d.nav || d.route_nav)) || null;
const navVal = (d, key) => { const n = activeNav(d); return n && n[key] != null ? n[key] : null; };
const fixOf = (d) => (d && d.gps && d.gps.has_fix ? d.gps : null);
const tripOf = (d) => (d && d.trip) || null;

// Distance per gallon at the current instant: how a GPSMAP's Fuel Economy field reads, and the
// basis for Range. Meaningless at idle, where the boat burns fuel and covers no ground.
function economyNow(d) {
  const fix = fixOf(d);
  const gph = d && d.engine ? d.engine.fuel_gph : null;
  if (!fix || !gph || gph <= 0.05 || fix.sog_kn < 0.5) return null;
  return toDist(fix.sog_kn) / gph;   // distance-per-hour over gallons-per-hour
}

const OVERLAY_FIELDS = {
  // ---- Navigation (needs an active Go To or route) ----
  brg: { isAngle: true, group: "Navigation", label: "Bearing", decimals: 0, unit: () => "°", get: (d) => navVal(d, "bearing_deg") },
  crs: { isAngle: true, group: "Navigation", label: "Course", decimals: 0, unit: () => "°", get: (d) => navVal(d, "course_deg") },
  dtw: { group: "Navigation", label: "Distance", decimals: 2, unit: () => UNITS[unit].dist,
         get: (d) => { const v = navVal(d, "distance_nm"); return v == null ? null : toDist(v); } },
  ete: { group: "Navigation", label: "Time To Dest", isDuration: true, get: (d) => navVal(d, "ete_s") },
  eta: { group: "Navigation", label: "Arrival Time", isTime: true,
         get: (d) => { const s = navVal(d, "ete_s"); return s == null ? null : new Date(Date.now() + s * 1000); } },
  vmg: { group: "Navigation", label: "VMG", decimals: 1, unit: () => UNITS[unit].speed,
         get: (d) => { const v = navVal(d, "vmg_kn"); return v == null ? null : toSpeed(v); } },
  xte: { group: "Navigation", label: "Cross Track", decimals: 2, unit: () => UNITS[unit].dist,
         get: (d) => { const v = navVal(d, "xte_nm"); return v == null ? null : toDist(v); } },
  turn: { group: "Navigation", label: "Turn", isText: true,
          get: (d) => { const v = navVal(d, "turn_deg");
                        return v == null ? null : `${Math.abs(v).toFixed(0)}° ${v < 0 ? "L" : "R"}`; } },
  wpname: { group: "Navigation", label: "Destination", isText: true,
            get: (d) => { const n = activeNav(d); return n && n.waypoint ? n.waypoint.name : null; } },

  // ---- Vessel ----
  sog: { group: "Vessel", label: "Speed", decimals: 1, unit: () => UNITS[unit].speed,
         get: (d) => { const f = fixOf(d); return f ? toSpeed(f.sog_kn) : null; } },
  hdg: { isAngle: true, group: "Vessel", label: "Heading", decimals: 0, unit: () => "°", get: (d) => { const f = fixOf(d); return f ? f.heading_deg : null; } },
  cog: { isAngle: true, group: "Vessel", label: "Course Over Ground", decimals: 0, unit: () => "°", get: (d) => { const f = fixOf(d); return f ? f.cog_deg : null; } },
  pos: { group: "Vessel", label: "Position", isText: true,
         get: (d) => { const f = fixOf(d); return f ? fmtCoord(f.lat, true) : null; },
         sub: (d) => { const f = fixOf(d); return f ? fmtCoord(f.lon, false) : ""; } },
  hdop: { group: "Vessel", label: "GPS Accuracy", decimals: 1, unit: () => "HDOP", get: (d) => (d && d.gps ? d.gps.hdop : null) },
  sats: { group: "Vessel", label: "Satellites", decimals: 0, unit: () => "", get: (d) => (d && d.gps ? d.gps.satellites : null) },

  // ---- Depth & water ----
  depth: { group: "Depth & Water", label: "Depth", decimals: 1, unit: () => "ft", get: (d) => (d ? d.boat_info.depth_ft : null) },
  wtemp: { group: "Depth & Water", label: "Water Temp", decimals: 0, unit: () => "°F", get: (d) => (d ? d.boat_info.water_temp_f : null) },

  // ---- Engine ----
  rpm: { group: "Engine", label: "RPM", decimals: 0, unit: () => "", get: (d) => (d ? d.engine.rpm : null) },
  coolant: { group: "Engine", label: "Engine Temp", decimals: 0, unit: () => "°F", get: (d) => (d ? d.engine.coolant_f : null) },
  oil: { group: "Engine", label: "Oil Pressure", decimals: 0, unit: () => "psi", get: (d) => (d ? d.engine.oil_pressure_psi : null) },
  trim: { group: "Engine", label: "Trim", decimals: 0, unit: () => "%", get: (d) => (d ? d.engine.trim_pct : null) },
  battery: { group: "Engine", label: "Battery", decimals: 1, unit: () => "V", get: (d) => (d ? d.boat_info.battery_voltage : null) },

  // ---- Fuel ----
  fuel: { group: "Fuel", label: "Fuel Level", decimals: 0, unit: () => "%", get: (d) => (d ? d.engine.fuel_pct : null) },
  fuelrem: { group: "Fuel", label: "Fuel Remaining", decimals: 1, unit: () => "gal", get: (d) => (d ? d.engine.fuel_remaining_gal : null) },
  fuelrate: { group: "Fuel", label: "Fuel Rate", decimals: 1, unit: () => "gph", get: (d) => (d ? d.engine.fuel_gph : null) },
  economy: { group: "Fuel", label: "Fuel Economy", decimals: 1, unit: () => `${UNITS[unit].dist}/gal`, get: economyNow },
  range: { group: "Fuel", label: "Range", decimals: 0, unit: () => UNITS[unit].dist,
           get: (d) => { const e = economyNow(d), gal = d && d.engine ? d.engine.fuel_remaining_gal : null;
                         return e == null || gal == null ? null : e * gal; } },

  // ---- Trip (needs a trip started on the Trip screen) ----
  tripdist: { group: "Trip", label: "Trip Distance", decimals: 2, unit: () => UNITS[unit].dist,
              get: (d) => { const t = tripOf(d); return t ? toDist(t.distance_nm) : null; } },
  triptime: { group: "Trip", label: "Trip Time", isDuration: true, get: (d) => { const t = tripOf(d); return t ? t.duration_s : null; } },
  avgspeed: { group: "Trip", label: "Average Speed", decimals: 1, unit: () => UNITS[unit].speed,
              get: (d) => { const t = tripOf(d); return t ? toSpeed(t.avg_speed_kn) : null; } },
  maxspeed: { group: "Trip", label: "Max Speed", decimals: 1, unit: () => UNITS[unit].speed,
              get: (d) => { const t = tripOf(d); return t ? toSpeed(t.max_speed_kn) : null; } },
  tripfuel: { group: "Trip", label: "Trip Fuel Used", decimals: 1, unit: () => "gal", get: (d) => { const t = tripOf(d); return t ? t.fuel_gal : null; } },

  // ---- Time ----
  time: { group: "Time", label: "Time of Day", isTime: true, get: () => new Date() },
  sunrise: { group: "Time", label: "Sunrise", isTime: true, get: (d) => (d && d.sun ? d.sun.sunrise : null) },
  sunset: { group: "Time", label: "Sunset", isTime: true, get: (d) => (d && d.sun ? d.sun.sunset : null) },
  moonphase: { group: "Time", label: "Moon Phase", isText: true,
    get: (d) => (d && d.sun ? d.sun.moon_phase : null),
    sub: (d) => (d && d.sun && d.sun.moon_illumination != null ? `${Math.round(d.sun.moon_illumination * 100)}%` : ""),
  },
};

let overlayPickCallback = null;
function openOverlayPicker(onPick) {
  overlayPickCallback = onPick;
  const body = $("overlayPickBody");
  // Thirty-odd fields in one flat list is unusable at the helm, so break them under their
  // group headings in declaration order -- Navigation first, the way a GPSMAP orders them.
  const rows = [];
  let lastGroup = null;
  Object.entries(OVERLAY_FIELDS).forEach(([key, f]) => {
    if (f.group !== lastGroup) {
      lastGroup = f.group;
      const h = document.createElement("div");
      h.className = "pick-group";
      h.textContent = f.group;
      rows.push(h);
    }
    const b = document.createElement("button");
    b.className = "row";
    b.innerHTML = `<span>${f.label}</span><span class="row-val">›</span>`;
    b.addEventListener("click", () => {
      closePanels();
      if (overlayPickCallback) overlayPickCallback(key);
    });
    rows.push(b);
  });
  body.replaceChildren(...rows);
  openPanel("overlaypick");
}

const overlayInstances = [];
function overlayBoxes(container, storageKey, defaultFields) {
  if (!container) return { render() {} };
  let fieldKeys = defaultFields.slice();
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    if (Array.isArray(saved) && saved.length === defaultFields.length) fieldKeys = saved;
  } catch (e) { /* storage unavailable */ }

  function persist() {
    try { localStorage.setItem(storageKey, JSON.stringify(fieldKeys)); } catch (e) { /* storage unavailable */ }
  }

  function build() {
    container.replaceChildren(...fieldKeys.map((key, i) => {
      const box = document.createElement("button");
      box.className = "gbox lg overlay-box";
      box.innerHTML = '<span class="gb-label"></span><span class="gb-val"><b>--</b><small></small></span>';
      // Hold to pick, the same gesture (and the same fill-as-you-hold feedback) as the RPM
      // smoothing and alarm menus elsewhere in the app, matching a real GPSMAP's "hold the
      // overlay box" — attachHold also swallows the click a completed hold would otherwise
      // also fire, so this never double-opens the picker.
      attachHold(box, () => openOverlayPicker((newKey) => {
        fieldKeys[i] = newKey;
        persist();
        build();
        render(lastData);
      }));
      return box;
    }));
  }
  build();

  function render(data) {
    const boxes = container.querySelectorAll(".overlay-box");
    fieldKeys.forEach((key, i) => {
      const f = OVERLAY_FIELDS[key];
      const box = boxes[i];
      if (!box || !f) return;
      box.querySelector(".gb-label").textContent = f.label;
      const val = f.get(data);
      const valEl = box.querySelector(".gb-val");
      if (f.isTime) {
        valEl.classList.remove("text");
        valEl.querySelector("b").textContent = fmtClockTime(val);
        valEl.querySelector("small").textContent = "";
      } else if (f.isDuration) {
        valEl.classList.remove("text");
        valEl.querySelector("b").textContent = val == null ? "--" : fmtDuration(val);
        valEl.querySelector("small").textContent = "";
      } else if (f.isText) {
        valEl.classList.add("text");
        valEl.querySelector("b").textContent = val == null ? "--" : val;
        valEl.querySelector("small").textContent = f.sub ? f.sub(data) : "";
      } else {
        valEl.classList.remove("text");
        // A rounded 359.96 must not print as "360": compass readings run 0..359.
        const shown = val == null ? null : f.isAngle ? Math.round(val) % 360 : val;
        valEl.querySelector("b").textContent = shown == null ? "--" : shown.toFixed(f.isAngle ? 0 : f.decimals);
        valEl.querySelector("small").textContent = f.unit ? f.unit() : "";
      }
    });
  }
  const instance = { render };
  overlayInstances.push(instance);
  return instance;
}

overlayBoxes($("overlayHelm"), "overlayHelm", ["depth", "wtemp"]);
overlayBoxes($("overlayChart"), "overlayChart", ["sog", "hdg", "depth", "wtemp"]);
// Time/sunrise/sunset keep moving even when telemetry hasn't sent a fresh frame in the last second.
setInterval(() => overlayInstances.forEach((o) => o.render(lastData)), 1000);

function renderNav(nav, routeNav, boatLatLng) {
  // A single Go To (nav) and an active Route (routeNav) are mutually exclusive server-side, but
  // either one drives the exact same on-chart marker/line and Waypoint-panel readout.
  const active = nav || (routeNav && { bearing_deg: routeNav.bearing_deg, distance_nm: routeNav.distance_nm,
    ete_s: routeNav.ete_s, xte_nm: routeNav.xte_nm, vmg_kn: routeNav.vmg_kn, turn_deg: routeNav.turn_deg,
    waypoint: { lat: routeNav.leg_lat, lon: routeNav.leg_lon, name: `${routeNav.route_name}: ${routeNav.leg_name}` } });
  if (!active) {
    $("wpEmpty").hidden = false;
    $("wpInfo").hidden = true;
    if (waypointMarker) { map.removeLayer(waypointMarker); waypointMarker = null; }
    if (wpLine) { map.removeLayer(wpLine); wpLine = null; }
    return;
  }
  $("wpEmpty").hidden = true;
  $("wpInfo").hidden = false;
  $("wpBearing").textContent = Math.round(active.bearing_deg) % 360;
  $("wpDist").textContent = toDist(active.distance_nm).toFixed(2);
  $("wpEte").textContent = active.ete_s != null ? fmtDuration(active.ete_s) : "--";
  $("wpXte").textContent = active.xte_nm != null ? toDist(active.xte_nm).toFixed(2) : "--";
  $("wpVmg").textContent = active.vmg_kn != null ? toSpeed(active.vmg_kn).toFixed(1) : "--";
  $("wpTurn").textContent = active.turn_deg != null
    ? `${Math.abs(active.turn_deg).toFixed(0)}° ${active.turn_deg < 0 ? "L" : "R"}` : "--";

  const wpLatLng = [active.waypoint.lat, active.waypoint.lon];
  if (!waypointMarker) waypointMarker = L.circleMarker(wpLatLng, { radius: 9, color: "#fff", weight: 2, fillColor: "#e0202b", fillOpacity: 1 }).addTo(map);
  else waypointMarker.setLatLng(wpLatLng);
  // The line ahead: a guided Go To's remaining route, from the boat on along the channel;
  // otherwise the straight line to the waypoint (or the active route's leg).
  const path = active.waypoint.path;
  const line = path && path.length > 2 ? [boatLatLng, ...path.slice(active.leg || 1)] : [boatLatLng, wpLatLng];
  if (!wpLine) wpLine = L.polyline(line, { color: "#e0202b", weight: 3, dashArray: "8 6" }).addTo(map);
  else wpLine.setLatLngs(line);
}

$("setWpBtn").addEventListener("click", () => {
  const lat = parseFloat($("wpLat").value);
  const lon = parseFloat($("wpLon").value);
  if (!Number.isNaN(lat) && !Number.isNaN(lon)) setWaypoint(lat, lon);
});
$("clearWp").addEventListener("click", clearWaypoint);

// ---------- Dials and tiles ----------
function buildSpeedDials() {
  const u = UNITS[unit];
  // GPS speed arrives once a second, so its needle follows a little more slowly than the engine gauges do
  dials.helmSpeed = makeDial($("dialHelmSpeed"), { size: "compact", min: 0, max: u.max, segments: 30, unit: u.gauge, label: "GPS Speed", decimals: 1, needle: "pointer", omega: 4.5 });
  dials.speed = makeDial($("dialSpeed"), { size: "big", min: 0, max: u.max, step: u.step, segments: 40, unit: u.gauge, label: "GPS Speed", decimals: 1, needle: "pointer", omega: 4.5 });
  dials.flow = makeDial($("dialFlow"), { size: "big", min: 0, max: 15, step: 5, segments: 30, unit: "GPH", label: "Fuel Flow", decimals: 1, omega: 5 });
}

let rpmKey = "";
function buildRpmDials(engine) {
  const key = `${engine.max_rpm}|${engine.redline_rpm}`;
  if (key === rpmKey) return;
  rpmKey = key;  // the redline depends on the engine, so build these from what the backend reports
  const zones = [{ from: engine.redline_rpm, to: engine.max_rpm, level: "red" }];
  // Needle response is a user setting (hold either RPM gauge; see GAUGE_LEVELS in alarms.js), not fixed at 10.
  const rpmOmega = typeof gaugeOmega === "function" ? gaugeOmega("rpm") : 10;
  dials.rpm = makeDial($("dialRpm"), { size: "big", min: 0, max: engine.max_rpm, step: 1000, segments: 30, zones, unit: "RPM", label: "rpm x100", numFmt: (v) => v / 100, omega: rpmOmega });
  dials.helmRpm = makeDial($("dialHelmRpm"), { size: "compact", min: 0, max: engine.max_rpm, segments: 30, zones, unit: "RPM", label: "Engine RPM", omega: rpmOmega });
}

dials.cool = makeDial($("dialCool"), { size: "small", min: 100, max: 260, segments: 22, zones: ZONES.cool, unit: "°F", icon: "temp", endLabels: ["100", "260"], omega: 6 });
dials.oil = makeDial($("dialOil"), { size: "small", min: 0, max: 80, segments: 22, zones: ZONES.oil, unit: "psi", icon: "oil", endLabels: ["0", "80"], omega: 6 });
dials.batt = makeDial($("dialBatt"), { size: "small", min: 10, max: 16, segments: 22, zones: ZONES.batt, unit: "V", decimals: 1, icon: "battery", endLabels: ["10", "16"], omega: 6 });
dials.fuel = makeDial($("dialFuel"), { size: "small", min: 0, max: 100, segments: 22, zones: ZONES.fuel, unit: "%", icon: "fuel", endLabels: ["E", "F"], omega: 6 });
dials.trim = makeDial($("dialTrim"), { size: "small", min: 0, max: 100, segments: 22, unit: "%", icon: "trim", endLabels: ["DN", "UP"], omega: 6 });

// Helm tiles: compact readouts with a segmented bar underneath.
const TILE_SPECS = [
  { id: "cool", label: "COOLANT", unit: "°F", min: 100, max: 260, zones: ZONES.cool },
  { id: "oil", label: "OIL", unit: "PSI", min: 0, max: 80, zones: ZONES.oil },
  { id: "batt", label: "BATTERY", unit: "V", min: 10, max: 16, zones: ZONES.batt, decimals: 1 },
  { id: "fuel", label: "FUEL", unit: "%", min: 0, max: 100, zones: ZONES.fuel, wide: true, economy: true },
  { id: "trim", label: "TRIM", unit: "%", min: 0, max: 100, zones: [], wide: true },
];
const tiles = {};
TILE_SPECS.forEach((s) => {
  const tile = document.createElement("div");
  tile.className = "tile" + (s.wide ? " wide" : "");
  if (s.economy) tile.title = "GPH is measured when a NMEA 2000 fuel sensor is connected; otherwise it is estimated from RPM (see Options > Sensor calibration)";
  tile.innerHTML = `<div class="tile-head"><span class="tile-label">${s.label}</span>` +
    (s.economy ? `<span class="tile-extra"><b data-bind="gph">--</b> GPH<span data-bind="gphTag"></span> &middot; <b data-bind="mpg">--</b> <span data-bind="mpgUnit">MPG</span></span>` : "") +
    `<span class="tile-value"><span class="tv">--</span><small>${s.unit}</small></span></div><div class="bar"></div>`;
  $("helmTiles").appendChild(tile);
  const val = tile.querySelector(".tv");
  tiles[s.id] = {
    spec: s, tile, val,
    num: makeAnimatedText(() => [val], { decimals: s.decimals || 0, omega: 6 }),
    bar: makeSegBar(tile.querySelector(".bar"), { min: s.min, max: s.max, zones: s.zones, segments: 32, omega: 6 }),
  };
});

function updateTile(id, v) {
  const t = tiles[id];
  if (!t) return;
  t.num.update(v);
  t.bar.update(v);
  const zone = v == null ? null : t.spec.zones.find((z) => v >= z.from && v <= z.to);
  t.tile.dataset.level = zone ? zone.level : "";
}

let latestReadings = { engine: {}, boat: {} };

function renderEngine(engine, boat, sogKn) {
  latestReadings = { engine, boat };
  buildRpmDials(engine);
  dials.rpm.update(engine.rpm);
  dials.helmRpm.update(engine.rpm);
  dials.flow.update(engine.fuel_gph);
  dials.cool.update(engine.coolant_f);
  dials.oil.update(engine.oil_pressure_psi);
  dials.batt.update(boat.battery_voltage);
  dials.fuel.update(engine.fuel_pct);
  dials.trim.update(engine.trim_pct);
  updateTile("cool", engine.coolant_f);
  updateTile("oil", engine.oil_pressure_psi);
  updateTile("batt", boat.battery_voltage);
  updateTile("fuel", engine.fuel_pct);
  updateTile("trim", engine.trim_pct);

  // Fuel burn is measured by a NMEA 2000 fuel sensor when there is one, otherwise estimated from RPM (tagged "est").
  // Economy is speed over burn: miles per gallon in mph mode, nautical miles per gallon in knots mode.
  const speed = toSpeed(sogKn);
  const gph = engine.fuel_gph;
  animBind("gph", gph, 1);
  bind("gphTag", gph != null && engine.fuel_gph_est ? " est" : "");
  bind("mpgUnit", unit === "mph" ? "MPG" : "NMPG");
  animBind("mpg", gph != null && gph >= 0.3 && speed >= 1 ? speed / gph : null, 1);
  animBind("fuelPct", engine.fuel_pct, 0);
  animBind("depth", boat.depth_ft, 1);
  animBind("wtemp", boat.water_temp_f, 0);
}

function applyUnitLabels() {
  const u = UNITS[unit];
  document.querySelectorAll(".u-speed").forEach((el) => (el.textContent = u.speed));
  document.querySelectorAll(".u-dist").forEach((el) => (el.textContent = u.dist));
  const opt = document.getElementById("optUnits");
  if (opt) opt.textContent = unit === "mph" ? "mph / mi" : "kn / nm";
}

function setUnit(next) {
  unit = next;
  try { localStorage.setItem("unit", unit); } catch (e) { /* storage unavailable */ }
  buildSpeedDials();
  applyUnitLabels();
  if (lastData) renderAll(lastData);
  loadTripHistory();
}
const toggleUnits = () => setUnit(unit === "mph" ? "kn" : "mph");
["helmSpeedCard", "gaugeSpeedCard"].forEach((id) => $(id).addEventListener("click", toggleUnits));

// ---------- Level control (volume, brightness) ----------
// Big -/+ buttons (hold to repeat) plus a bar you can tap or drag. Telemetry never overwrites the
// value for a moment after the driver touches it, so the bar doesn't jump back mid-adjustment.
function levelControl(root, { min = 0, getMax, step = 1, text, onChange }) {
  const bar = root.querySelector(".level-bar");
  const fill = root.querySelector(".level-fill");
  const label = root.querySelector(".level-text");
  let value = min;
  let holdUntil = 0;
  let sendTimer = null;
  let pending = null;
  let dragging = false;

  const snap = (v) => Math.max(min, Math.min(getMax(), Math.round(v / step) * step));
  const render = () => {
    fill.style.width = `${((value - min) / (getMax() - min)) * 100}%`;
    label.textContent = text(value);
  };
  // The first change goes out at once; while the finger keeps moving, at most one update per 60 ms (the latest wins).
  const flush = () => {
    const v = pending;
    pending = null;
    onChange(v);
    sendTimer = setTimeout(() => { sendTimer = null; if (pending != null) flush(); }, 60);
  };
  const send = (v) => {
    pending = v;
    if (!sendTimer) flush();
  };
  const commit = (v) => {
    v = snap(v);
    holdUntil = Date.now() + 1500;
    if (v === value) return;
    value = v;
    render();
    send(v);
  };

  root.querySelectorAll(".step-btn").forEach((btn) => {
    const dir = Number(btn.dataset.step) * step;
    let delay = null;
    let repeat = null;
    const stop = () => { clearTimeout(delay); clearInterval(repeat); };
    btn.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      commit(value + dir);
      stop();
      delay = setTimeout(() => { repeat = setInterval(() => commit(value + dir), 110); }, 400);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => btn.addEventListener(ev, stop));
  });

  const valueAt = (e) => {
    const r = bar.getBoundingClientRect();
    return min + Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (getMax() - min);
  };
  bar.addEventListener("pointerdown", (e) => { bar.setPointerCapture(e.pointerId); dragging = true; commit(valueAt(e)); });
  bar.addEventListener("pointermove", (e) => { if (dragging) commit(valueAt(e)); });
  ["pointerup", "pointercancel"].forEach((ev) => bar.addEventListener(ev, () => (dragging = false)));

  render();
  return {
    set(v, force = false) { if (force || Date.now() > holdUntil) { value = snap(v); render(); } },
    refresh: render,
    setEnabled(on) {
      root.classList.toggle("disabled", !on);
      root.querySelectorAll(".step-btn").forEach((b) => (b.disabled = !on));
    },
  };
}

const levelMarkup = (minusLabel, plusLabel) =>
  `<div class="level-ctl"><button class="step-btn" data-step="-1" aria-label="${minusLabel}">&minus;</button>` +
  `<div class="level-bar" role="slider"><div class="level-fill"></div><span class="level-text"></span></div>` +
  `<button class="step-btn" data-step="1" aria-label="${plusLabel}">+</button></div>`;

// ---------- Trip widget ----------
let tripRunning = false;
let lastTrips = [];

function fmtDuration(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(s).padStart(2, "0")}s`;
}

const gbox = (label, key, unitClass, unitText, onlyFull) =>
  `<div class="gbox${onlyFull ? " only-full" : ""}"><span class="gb-label">${label}</span><span class="gb-val"><b data-k="${key}">--</b><small class="${unitClass}">${unitText}</small></span></div>`;

function tripWidget(root, variant) {
  root.innerHTML = `<div class="tw ${variant}">
    <div class="tw-head"><h2 class="wtitle">Trip</h2><button class="btn primary" data-k="btn">Start Trip</button></div>
    <div class="tw-body">
      <div class="tw-boxes">
        ${gbox("Distance", "dist", "u-dist", "mi")}
        ${gbox("Time", "dur", "", "")}
        ${gbox("Avg Speed", "avg", "u-speed", "mph")}
        ${gbox("Max Speed", "max", "u-speed", "mph")}
        ${gbox("Fuel Used", "fuel", "", "gal", true)}
        ${gbox("Fuel Economy", "eco", "", "mpg", true)}
      </div>
      <div class="tw-history"><h3>Saved Trips</h3><div class="trip-list" data-k="list"></div></div>
    </div>
  </div>`;
  const q = (k) => root.querySelector(`[data-k="${k}"]`);
  const btn = q("btn");
  btn.addEventListener("click", async () => {
    if (tripRunning) {
      await fetch("/api/trips/stop", { method: "POST" });
      loadTripHistory();
    } else {
      const data = await (await fetch("/api/trips/start", { method: "POST" })).json();
      if (!data.ok) alert(data.error || "could not start trip");
    }
  });

  return {
    render(trip) {
      tripRunning = !!trip;   // the click handler (above) needs this kept in sync with the server, not just the button's own label
      btn.textContent = trip ? "Stop & Save" : "Start Trip";
      btn.className = "btn " + (trip ? "danger" : "primary");
      q("dist").textContent = trip ? toDist(trip.distance_nm).toFixed(2) : "--";
      q("dur").textContent = trip ? fmtDuration(trip.duration_s) : "--";
      q("avg").textContent = trip ? toSpeed(trip.avg_speed_kn).toFixed(1) : "--";
      q("max").textContent = trip ? toSpeed(trip.max_speed_kn).toFixed(1) : "--";
      q("fuel").textContent = trip ? trip.fuel_gal.toFixed(1) : "--";
      const miles = trip ? toDist(trip.distance_nm) : 0;
      q("eco").textContent = trip && trip.fuel_gal > 0.05 && miles > 0 ? (miles / trip.fuel_gal).toFixed(1) : "--";
      root.querySelector('[data-k="eco"]').nextElementSibling.textContent = unit === "mph" ? "mpg" : "nmpg";
    },
    setHistory(trips) {
      const list = q("list");
      list.replaceChildren();
      if (!trips.length) {
        list.innerHTML = '<div class="empty-note">No saved trips yet.</div>';
        return;
      }
      trips.forEach((t) => {
        const row = document.createElement("div");
        row.className = "trip-row";
        const date = new Date(t.start_time * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
        const avg = t.duration_s > 0 ? toSpeed(t.distance_nm / (t.duration_s / 3600)).toFixed(1) : "0.0";
        const fuel = t.fuel_gal > 0.05 ? ` &middot; ${t.fuel_gal.toFixed(1)} gal` : "";
        row.innerHTML = `<div><div class="trip-date">${date}</div>` +
          `<div class="trip-stats">${toDist(t.distance_nm).toFixed(2)} ${UNITS[unit].dist} &middot; ${fmtDuration(t.duration_s)} &middot; ${avg} ${UNITS[unit].speed}${fuel}</div></div>` +
          `<button class="trip-del" aria-label="Delete trip">&times;</button>`;
        row.querySelector(".trip-del").addEventListener("click", async () => {
          await fetch(`/api/trips/${t.id}`, { method: "DELETE" });
          loadTripHistory();
        });
        list.appendChild(row);
      });
    },
  };
}

// ---------- Media widget (Fusion stereo over NMEA 2000) ----------
let mediaState = null;
const ICON = {
  prev: '<path d="M6 6h2v12H6zM9.5 12L18 6v12z"/>',
  next: '<path d="M16 6h2v12h-2zM6 18V6l8.5 6z"/>',
  play: '<path d="M8 5v14l11-7z"/>',
  pause: '<path d="M6 5h4v14H6zM14 5h4v14h-4z"/>',
  power: '<path d="M13 3h-2v10h2V3zm4.83 2.17l-1.42 1.42A6.92 6.92 0 0 1 19 12c0 3.87-3.13 7-7 7s-7-3.13-7-7c0-2.05.88-3.9 2.28-5.19L5.87 5.4A8.96 8.96 0 0 0 3 12a9 9 0 0 0 18 0c0-2.74-1.23-5.19-3.17-6.83z"/>',
  mute: '<path d="M3 9v6h4l5 5V4L7 9H3z"/><path class="waves" d="M14 8.5a5 5 0 0 1 0 7v-2a3 3 0 0 0 0-3zM16.5 6a8.5 8.5 0 0 1 0 12v-2a6.5 6.5 0 0 0 0-8z"/><path class="slash" d="M4 4l16 16" stroke="currentColor" stroke-width="2.5" fill="none"/>',
  gear: '<path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.488.488 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/>',
};

async function mediaCommand(action, value, zone = 1) {
  const res = await (await postJson("/api/media", { action, value, zone })).json();
  if (res.media) renderMedia(res.media);
  if (!res.ok) console.warn("media command failed:", res.error);
}

// Stereos don't send cover art over NMEA 2000: use the looked-up image if there is one, else draw a cover.
function coverGradient(seed) {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const h1 = h % 360;
  const h2 = (h1 + 40 + ((h >> 8) % 80)) % 360;
  return `radial-gradient(circle at 72% 28%, hsla(${h2}, 85%, 62%, 0.95) 0 16%, transparent 17%), linear-gradient(135deg, hsl(${h1}, 65%, 38%), hsl(${h2}, 70%, 20%))`;
}

const fmtClock = (sec) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;

function mediaWidget(root, variant) {
  const full = variant === "full";
  const muteButton = `<button class="mute-btn" aria-label="Mute all zones" title="Mute all zones"><svg viewBox="0 0 24 24">${ICON.mute}</svg></button>`;
  root.innerHTML = `<div class="mw ${variant}">
    <div class="mw-art"><img alt="Album art" hidden /><div class="mw-art-fallback"><span>&#9835;</span></div><span class="mw-badge">OFFLINE</span></div>
    <div class="mw-main">
      <div class="mw-top">
        <div class="mw-text"><div class="mw-title">--</div><div class="mw-sub"></div></div>
        <button class="btn mw-source-btn" title="Tap to change source"><small>SOURCE</small><span>--</span></button>
        <button class="btn icon mw-boost-btn" aria-label="RPM volume boost settings" title="RPM volume boost"><svg viewBox="0 0 24 24">${ICON.gear}</svg></button>
        <button class="btn icon mw-power" aria-label="Power"><svg viewBox="0 0 24 24">${ICON.power}</svg></button>
      </div>
      <div class="mw-progress"><span class="t t-el">0:00</span><div class="bar"><i></i></div><span class="t t-left">-0:00</span></div>
      <div class="mw-sources"></div>
      <div class="mw-transport">
        <button class="t-btn mw-prev" aria-label="Previous"><svg viewBox="0 0 24 24">${ICON.prev}</svg></button>
        <button class="t-btn play mw-play" aria-label="Play or pause"><svg viewBox="0 0 24 24">${ICON.play}</svg></button>
        <button class="t-btn mw-next" aria-label="Next"><svg viewBox="0 0 24 24">${ICON.next}</svg></button>
        ${full ? muteButton : ""}
      </div>
      <div class="mw-vol">${full ? "" : muteButton}<div class="mw-zones"></div></div>
    </div>
  </div>`;
  const q = (s) => root.querySelector(s);
  const img = q(".mw-art img");
  const fallback = q(".mw-art-fallback");
  let artKey = null;
  let sourcesKey = "";

  // ---- volume ----
  // Full size: a Master row (all zones together) plus one row per zone, each with its own mute
  // button and volume bar. Compact (Helm): Master only, nothing per-zone -- fine-grained mixing
  // belongs on the full Media screen (one tap away), this is a quick "louder/quieter overall"
  // control for while underway, not a place to manage individual zones.
  const zoneOf = (id) => (mediaState && mediaState.zones ? mediaState.zones.find((z) => z.id === id) : null);
  const zoneMax = (id) => { const z = zoneOf(id); return z ? z.limit : mediaState ? mediaState.max_volume : 24; };
  const masterMax = () => (mediaState ? mediaState.max_volume : 24);
  const zoneArea = q(".mw-zones");
  let zoneKey = null;
  let rows = [];           // full: { id, control, row, name, btn }
  let master = null;       // both variants: the Master control (only omitted in full mode with a single zone)

  function buildZones(zones) {
    zoneArea.replaceChildren();
    rows = [];
    master = null;
    const list = zones.length ? zones : [{ id: 1, name: "Zone 1" }];  // no stereo yet: a disabled placeholder
    if (full) {
      if (list.length > 1) {
        const row = document.createElement("div");
        row.className = "zone-row master-row";
        row.innerHTML = `<span class="zone-btn master-label">Master</span>${levelMarkup("All zones down", "All zones up")}`;
        const control = levelControl(row.querySelector(".level-ctl"), {
          getMax: masterMax,
          text: (v) => String(v),
          onChange: (v) => mediaCommand("master_volume", v),
        });
        zoneArea.appendChild(row);
        master = { control, row };
      }
      list.forEach((z) => {
        const row = document.createElement("div");
        row.className = "zone-row";
        row.innerHTML = `<button class="zone-btn" aria-label="Mute this zone"><svg viewBox="0 0 24 24">${ICON.mute}</svg><span class="zone-name"></span></button>${levelMarkup("Volume down", "Volume up")}`;
        const control = levelControl(row.querySelector(".level-ctl"), {
          getMax: () => zoneMax(z.id),
          text: (v) => String(v),
          onChange: (v) => mediaCommand("volume", v, z.id),
        });
        const btn = row.querySelector(".zone-btn");
        btn.addEventListener("click", () => { const cur = zoneOf(z.id); mediaCommand("zone_mute", cur && cur.muted ? 0 : 1, z.id); });
        zoneArea.appendChild(row);
        rows.push({ id: z.id, control, row, name: row.querySelector(".zone-name"), btn });
      });
      return;
    }
    const holder = document.createElement("div");
    holder.innerHTML = levelMarkup("All zones down", "All zones up");
    const ctl = holder.firstChild;
    zoneArea.appendChild(ctl);
    master = {
      control: levelControl(ctl, {
        getMax: masterMax,
        text: (v) => String(v),
        onChange: (v) => mediaCommand("master_volume", v),
      }),
    };
  }

  function renderZones(media) {
    const zones = media.zones || [];
    const key = JSON.stringify(zones.map((z) => [z.id, z.name]));
    if (key !== zoneKey) { zoneKey = key; buildZones(zones); }
    const live = media.connected && media.power;
    root.querySelectorAll(".zone-btn").forEach((b) => (b.disabled = !live));
    if (master) {
      const vols = zones.map((z) => z.volume);
      master.control.setEnabled(live && vols.length > 0);
      if (vols.length) master.control.set(Math.max(...vols));
      master.control.refresh();
    }
    if (full) {
      rows.forEach((r) => {
        const z = zones.find((x) => x.id === r.id);
        r.control.setEnabled(live && !!z);
        r.name.textContent = z ? z.name : `Zone ${r.id}`;
        r.btn.classList.toggle("active", !!(z && z.muted));
        r.row.classList.toggle("muted", !!(z && z.muted));
        if (z) r.control.set(z.volume);
      });
    }
  }

  q(".mw-power").addEventListener("click", () => mediaCommand("power", mediaState.power ? 0 : 1));
  q(".mw-boost-btn").addEventListener("click", openVolumeBoostMenu);
  q(".mw-prev").addEventListener("click", () => mediaCommand("prev"));
  q(".mw-next").addEventListener("click", () => mediaCommand("next"));
  q(".mw-play").addEventListener("click", () => mediaCommand(mediaState.playing ? "pause" : "play"));
  q(".mute-btn").addEventListener("click", () => mediaCommand("mute", mediaState.muted ? 0 : 1));
  q(".mw-source-btn").addEventListener("click", () => {
    const ids = mediaState.sources.map((s) => s.id);
    if (ids.length) mediaCommand("source", ids[(ids.indexOf(mediaState.source_id) + 1) % ids.length]);
  });

  function updateArt(media, live) {
    const url = live ? media.art_url : null;
    const key = live ? url || `${media.title}|${media.artist}|${media.album}` : "";
    if (key === artKey) return;
    artKey = key;
    fallback.style.background = live ? coverGradient(key) : "#1f2124";
    fallback.style.display = "flex";
    img.hidden = true;
    if (url) {
      img.onload = () => { img.hidden = false; fallback.style.display = "none"; };
      img.onerror = () => { img.hidden = true; fallback.style.display = "flex"; };
      img.src = url;
    }
  }

  return {
    render(media) {
      const live = media.connected && media.power;
      const badge = q(".mw-badge");
      badge.textContent = media.simulated ? "DEMO" : media.connected ? (media.power ? "ON" : "STANDBY") : "OFFLINE";
      badge.className = "mw-badge " + (media.simulated ? "" : media.connected ? "ok" : "bad");

      q(".mw-power").disabled = !media.connected;
      q(".mw-power").classList.toggle("on", live);
      root.querySelectorAll(".t-btn, .mute-btn, .mw-source-btn, .mw-sources .btn").forEach((b) => (b.disabled = !live));

      q(".mw-title").textContent = live ? media.title || "--" : media.connected ? "Standby" : "No stereo found";
      q(".mw-sub").textContent = live ? [media.artist, media.album].filter(Boolean).join(" · ") : "";
      const pct = live && media.length_s ? Math.min(100, (media.position_s / media.length_s) * 100) : 0;
      q(".mw-progress .bar i").style.transform = `scaleX(${pct / 100})`;
      q(".t-el").textContent = live && media.length_s ? fmtClock(media.position_s) : "";
      q(".t-left").textContent = live && media.length_s ? "-" + fmtClock(Math.max(0, media.length_s - media.position_s)) : "";
      const playIcon = media.playing ? ICON.pause : ICON.play;
      const playSvg = q(".mw-play svg");
      if (playSvg._icon !== playIcon) { playSvg.innerHTML = playIcon; playSvg._icon = playIcon; }  // not re-parsed every second
      q(".mute-btn").classList.toggle("active", media.muted);

      const source = media.sources.find((s) => s.id === media.source_id);
      q(".mw-source-btn span").textContent = source ? source.name : "--";
      const key = JSON.stringify(media.sources.map((s) => [s.id, s.name]));
      if (key !== sourcesKey) {  // rebuild the source chips only when the list actually changes
        sourcesKey = key;
        q(".mw-sources").replaceChildren(...media.sources.map((s) => {
          const b = document.createElement("button");
          b.className = "btn";
          b.dataset.id = s.id;
          b.textContent = s.name;
          b.addEventListener("click", () => mediaCommand("source", s.id));
          return b;
        }));
      }
      root.querySelectorAll(".mw-sources .btn").forEach((b) => b.classList.toggle("primary", Number(b.dataset.id) === media.source_id));

      updateArt(media, live);
      renderZones(media);
    },
  };
}

function renderMedia(media) {
  mediaState = media;
  widgets.media.forEach((w) => w.render(media));
}

// ---------- RPM Volume Boost settings (gear icon on the Media screen) ----------
// The car-stereo idea of speed-compensated volume, but for a boat: as RPM climbs toward redline
// the stereo gets a bit louder to stay ahead of engine and wind noise, then eases back down. The
// actual ramping happens server-side (app/volume_boost.py); this is just the settings UI for it.
let boostCfg = null;

async function loadVolumeBoost() {
  boostCfg = await (await fetch("/api/volume-boost")).json();
  return boostCfg;
}

async function saveVolumeBoost(patch) {
  const res = await (await postJson("/api/volume-boost", patch)).json();
  if (res.ok !== false) boostCfg = res;
  return res;
}

function openVolumeBoostMenu() {
  openPanel("boost");
  buildVolumeBoostMenu();
}

async function buildVolumeBoostMenu() {
  const body = $("boostBody");
  const cfg = await loadVolumeBoost();
  if ($("panel-boost").hidden) return;  // closed while the settings were loading
  body.replaceChildren();

  const enableBlock = document.createElement("div");
  enableBlock.className = "al-block";
  enableBlock.innerHTML = '<div class="al-head"><span class="al-name">RPM Volume Boost</span></div>' +
    '<p class="al-cap">Turns the stereo up as RPM climbs toward redline, to stay ahead of engine and wind noise, and eases it back down toward idle.</p>' +
    '<button class="btn al-toggle"></button>';
  body.appendChild(enableBlock);
  const toggle = enableBlock.querySelector(".al-toggle");
  const refreshToggle = () => {
    toggle.textContent = boostCfg.enabled ? "Boost is ON" : "Boost is OFF";
    toggle.classList.toggle("primary", boostCfg.enabled);
  };
  refreshToggle();
  toggle.addEventListener("click", async () => {
    await saveVolumeBoost({ enabled: !boostCfg.enabled });
    refreshToggle();
  });

  const pctBlock = document.createElement("div");
  pctBlock.className = "al-block";
  pctBlock.innerHTML = '<div class="al-head"><span class="al-name">Boost at full RPM</span></div>' +
    '<div class="al-cap">How much louder to go by redline</div>' + levelMarkup("Less", "More");
  body.appendChild(pctBlock);
  levelControl(pctBlock.querySelector(".level-ctl"), {
    min: 0, getMax: () => 100, step: 5,
    text: (v) => `+${Math.round(v)}%`,
    onChange: (v) => saveVolumeBoost({ boost_pct: v }),
  }).set(cfg.boost_pct, true);

  const smoothBlock = document.createElement("div");
  smoothBlock.className = "al-block";
  smoothBlock.innerHTML = '<div class="al-head"><span class="al-name">Smoothing</span></div>' +
    "<div class=\"al-cap\">How gradually the boost follows RPM, so it doesn't chase every flicker</div>" + levelMarkup("Snappier", "Smoother");
  body.appendChild(smoothBlock);
  levelControl(smoothBlock.querySelector(".level-ctl"), {
    min: 0, getMax: () => 100, step: 5,
    text: (v) => `${Math.round(v)}%`,
    onChange: (v) => saveVolumeBoost({ smoothing_pct: v }),
  }).set(cfg.smoothing_pct, true);
}

// ---------- Lights widget (RGB, color presets) ----------
const rgbToHex = (r, g, b) => "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
let lightingState = null;
let lightingHold = 0;   // don't let a telemetry frame overwrite a change we just made
let lightSeq = 0;
let presetsPromise = null;

// The display answers the tap immediately (the swatch, power state and preview strip change at once); the server's
// answer, which comes back within milliseconds, then confirms it.
function applyLightingLocal(body) {
  if (!lightingState) return;
  const l = { ...lightingState };
  if (body.on !== undefined) l.on = body.on;
  if (body.brightness !== undefined) l.brightness = body.brightness;
  if (body.r !== undefined) { l.solid_color = [body.r, body.g, body.b]; l.preset = "solid"; }
  if (body.preset !== undefined) l.preset = body.preset;
  if (l.preset === "solid" || !l.on) {
    const px = l.on ? l.solid_color.map((v) => Math.round(v * l.brightness)) : [0, 0, 0];
    l.frame = l.frame.map(() => px);
  }
  lightingState = l;
  lightingHold = Date.now() + 1200;
  widgets.lights.forEach((w) => w.render(l));
}

async function postLighting(body) {
  const seq = ++lightSeq;
  applyLightingLocal(body);
  try {
    const res = await (await postJson("/api/lighting", body)).json();
    if (seq === lightSeq && res.state) {  // only the newest answer counts
      lightingState = res.state;
      lightingHold = 0;
      widgets.lights.forEach((w) => w.render(res.state));
    }
  } catch (e) { /* the next telemetry frame puts the display right */ }
}

const loadPresets = () => (presetsPromise = presetsPromise || fetch("/api/lighting/presets").then((r) => r.json()));

function lightsWidget(root, variant) {
  root.innerHTML = `<div class="lw ${variant}">
    <div class="lw-head"><h2 class="wtitle">RGB Lights</h2><span class="lw-badge">OFF</span><button class="btn lw-rainbow">Rainbow</button><button class="btn lw-power">Power</button></div>
    <div class="lw-body">
      <div class="swatches"></div>
      <div class="lw-side"><div class="lw-caption">Brightness</div>${levelMarkup("Dimmer", "Brighter")}<div class="led-strip"></div></div>
    </div>
  </div>`;
  const q = (s) => root.querySelector(s);
  const brightness = levelControl(q(".level-ctl"), {
    min: 0, getMax: () => 100, step: 5,
    text: (v) => `${v}%`,
    onChange: (v) => postLighting({ brightness: v / 100 }),
  });
  q(".lw-rainbow").addEventListener("click", () => postLighting({ preset: "rainbow", on: true }));
  q(".lw-power").addEventListener("click", () => postLighting({ on: !(lightingState && lightingState.on) }));
  loadPresets().then(({ colors }) => {
    colors.forEach((c) => {
      const b = document.createElement("button");
      b.className = "swatch";
      b.title = c.name;
      b.setAttribute("aria-label", c.name);
      b.dataset.rgb = c.rgb.join(",");
      b.style.background = `rgb(${c.rgb.join(",")})`;
      b.addEventListener("click", () => postLighting({ r: c.rgb[0], g: c.rgb[1], b: c.rgb[2], on: true }));
      q(".swatches").appendChild(b);
    });
  });

  return {
    render(l) {
      q(".lw-badge").textContent = l.on ? "ON" : "OFF";
      q(".lw-badge").classList.toggle("ok", l.on);
      q(".lw-power").classList.toggle("on", l.on);
      brightness.set(Math.round(l.brightness * 100));
      // The brightness bar's fill tints to whatever color the strip actually is right now, dim-to-full
      // left to right (the same dark-to-light shape the default blue gradient already had) -- solid_color
      // when it's a plain color, or the live first pixel while rainbow is cycling through the wheel.
      const fill = q(".level-fill");
      if (fill) {
        const rgb = l.on ? (l.preset === "rainbow" ? l.frame[0] : l.solid_color) : null;
        fill.style.background = rgb ? `linear-gradient(90deg, rgb(${rgb.map((c) => Math.round(c * 0.35)).join(",")}), rgb(${rgb.join(",")}))` : "#2a2c30";
      }
      const strip = q(".led-strip");
      if (strip.children.length !== l.frame.length) {
        strip.replaceChildren(...l.frame.map(() => Object.assign(document.createElement("div"), { className: "px" })));
      }
      l.frame.forEach((px, i) => (strip.children[i].style.background = rgbToHex(px[0], px[1], px[2])));
      const solid = l.solid_color.join(",");
      root.querySelectorAll(".swatch").forEach((s) => s.classList.toggle("active", l.preset === "solid" && s.dataset.rgb === solid));
      q(".lw-rainbow").classList.toggle("on", l.preset === "rainbow");
    },
  };
}

// ---------- Widgets: the same three appear compact on the Helm screen and full-size on their own screens ----------
const widgets = { trip: [], media: [], lights: [] };
const WIDGET_FACTORIES = { trip: tripWidget, media: mediaWidget, lights: lightsWidget };
function mountWidgets() {
  document.querySelectorAll("[data-widget]").forEach((root) => {
    const kind = root.dataset.widget;
    widgets[kind].push(WIDGET_FACTORIES[kind](root, root.dataset.variant));
  });
}

async function loadTripHistory() {
  lastTrips = (await (await fetch("/api/trips")).json()).trips;
  widgets.trip.forEach((w) => w.setHistory(lastTrips));
}

// ---------- Render everything from one telemetry frame ----------
// ---------- Digital Switching (a grid of simulated relay circuits) ----------
let switchGridKey = "";
function renderSwitching(circuits) {
  const grid = $("switchGrid");
  if (!grid || !circuits) return;
  const key = circuits.map((c) => c.id).join(",");
  if (key !== switchGridKey) {
    switchGridKey = key;
    grid.replaceChildren(...circuits.map((c) => {
      const row = document.createElement("button");
      row.className = "sw-circuit";
      row.dataset.id = c.id;
      row.innerHTML = `<span class="sw-name">${c.name}</span><span class="sw-toggle"></span>`;
      row.addEventListener("click", () => postJson(`/api/switching/${c.id}`, { on: !row.classList.contains("on") }));
      grid.appendChild(row);
      return row;
    }));
  }
  circuits.forEach((c) => {
    const row = grid.querySelector(`[data-id="${c.id}"]`);
    if (!row) return;
    row.classList.toggle("on", c.on);
    row.querySelector(".sw-toggle").classList.toggle("on", c.on);
  });
}

// ---------- AIS targets drawn on the chart ----------
const AIS_ICON = L.divIcon({
  className: "",
  html: '<svg viewBox="0 0 20 20"><path d="M10 1 L18 17 L10 13.5 L2 17 Z" fill="#2fd6c8" stroke="#06302c" stroke-width="1.2"/></svg>',
  iconSize: [20, 20], iconAnchor: [10, 10],
});
const aisMarkers = {};  // mmsi -> L.Marker

// Rotate the inner <svg>, not the marker's own root element -- that root's transform is how
// Leaflet positions the marker. Through onScreen(), because the marker pane does not turn with
// the chart (see screenUpAngle).
function orientAisIcons() {
  Object.values(aisMarkers).forEach((m) => {
    const svg = m.getElement() && m.getElement().querySelector("svg");
    if (svg) svg.style.transform = `rotate(${onScreen(m.cogDeg || 0)}deg)`;
  });
}
function renderAis(targets) {
  if (!targets) return;
  const seen = new Set();
  targets.forEach((t) => {
    seen.add(t.mmsi);
    let marker = aisMarkers[t.mmsi];
    if (!marker) {
      marker = L.marker([t.lat, t.lon], { icon: AIS_ICON, interactive: false }).addTo(map);
      aisMarkers[t.mmsi] = marker;
    }
    marker.setLatLng([t.lat, t.lon]);
    marker.cogDeg = t.cog_deg || 0;
  });
  orientAisIcons();
  Object.keys(aisMarkers).forEach((mmsi) => {
    if (!seen.has(Number(mmsi))) { map.removeLayer(aisMarkers[mmsi]); delete aisMarkers[mmsi]; }
  });
}

function renderAll(data) {
  lastData = data;
  const speed = toSpeed(data.gps.sog_kn);
  dials.speed.update(speed);
  dials.helmSpeed.update(speed);
  renderGps(data.gps);
  renderTrack(data.track);
  renderNav(data.nav, data.route_nav, [data.gps.lat, data.gps.lon]);
  overlayInstances.forEach((o) => o.render(data));
  applyAlarmConfig(data);
  renderEngine(data.engine, data.boat_info, data.gps.sog_kn);
  applyAlarms(data.alarms);
  widgets.trip.forEach((w) => w.render(data.trip));
  renderMedia(data.media);
  renderSwitching(data.switching);
  renderAis(data.ais);
  if (Date.now() > lightingHold) {
    widgets.lights.forEach((w) => w.render(data.lighting));
    lightingState = data.lighting;
  }
  document.dispatchEvent(new CustomEvent("telemetry", { detail: data }));
}

// The engine and boat readings come five times a second between the full frames, so the gauges keep moving smoothly.
function renderFast(data) {
  renderEngine(data.engine, data.boat_info, lastData ? lastData.gps.sog_kn : 0);
  applyAlarms(data.alarms);
}
