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
const map = L.map("map", { zoomControl: false, attributionControl: false, minZoom: 5, maxZoom: 18, rotate: true, rotateControl: false, shiftKeyRotate: false }).setView([36.306, -86.563], 14);
L.control.scale({ position: "bottomleft", metric: false, imperial: true, maxWidth: 110 }).addTo(map);

// ---------- The chart itself: USACE Inland ENC vectors, drawn here, from local disk ----------
// This used to be a raster tile layer pulling rendered pictures from the Corps of Engineers' map
// server -- thousands of throttled requests and ~88 MB to cover one lake at five fixed zooms, and
// nothing at all without internet. It now draws the actual S-57 feature data (fetched once by
// `python -m app.fetch_charts`, see app/chart_data.py): 822 features and 2.1 MB for the whole lake,
// which is why it's fast, works entirely offline, stays crisp at any zoom, restyles for night by
// changing colours rather than re-rendering, and can tell you what a buoy is when you tap it.
//
// Canvas renderer rather than SVG: a few hundred chart features plus the boat, track, AIS targets
// and Quickdraw dots all redraw on every pan and rotation frame, and canvas handles that without
// the per-element DOM cost SVG pays.
const chartRenderer = L.canvas({ padding: 0.3 });
const chartPane = map.createPane("chart");
chartPane.style.zIndex = 200;   // under every marker pane, over the map background

// Depth shading bands, in metres, shallowest first. IENC gives each depth area a range
// (Depth_Area_Value_1..2); the band is chosen from the deepest edge, so a 0-2.74 m polygon shades
// as the shallow water it is. The whole point of a chart at a glance is "can I go there".
const DEPTH_BANDS = [2.0, 5.0, 10.0];

const CHART_PALETTES = {
  day: {
    land: "#e9dcc3", landEdge: "#b9ab8d", builtUp: "#ded2bc", water: ["#8ec9e8", "#b3dcf0", "#d3ebf8", "#e8f4fb"],
    coast: "#4a4636", contour: "#6f98ad", caution: "#e8d24a", danger: "#d1382f", track: "#b02a8f",
    hazard: "#e07b20", text: "#1c1c1c", textHalo: "#ffffff", chartBg: "#c9e3f2",
  },
  dusk: {
    land: "#8d8369", landEdge: "#6e664f", builtUp: "#847b63", water: ["#3d6c86", "#4e7f99", "#5e91ab", "#6ea0b9"],
    coast: "#3a372c", contour: "#7fa5b8", caution: "#c2ae3e", danger: "#c2352c", track: "#a3287f",
    hazard: "#c46c1c", text: "#f0f0f0", textHalo: "#1a1a1a", chartBg: "#4a7891",
  },
  night: {
    land: "#241f16", landEdge: "#4a412e", builtUp: "#2c261b", water: ["#0d2a3a", "#0a2231", "#071a26", "#05131c"],
    coast: "#6e6449", contour: "#3f6b82", caution: "#8a7a24", danger: "#a12b22", track: "#7d1f63",
    hazard: "#8a5416", text: "#d6dade", textHalo: "#000000", chartBg: "#06131c",
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
  switch (kind) {
    case "depth":
      return { renderer: chartRenderer, pane: "chart", stroke: false,
               fillColor: p.water[depthBandIndex(props)], fillOpacity: 1 };
    case "area":
      return { renderer: chartRenderer, pane: "chart", color: p.landEdge, weight: 1,
               fillColor: p.land, fillOpacity: 1 };
    case "caution":
      return { renderer: chartRenderer, pane: "chart", color: p.caution, weight: 2, dashArray: "6 4",
               fillColor: p.caution, fillOpacity: 0.16 };
    case "danger":
      return { renderer: chartRenderer, pane: "chart", color: p.danger, weight: 2,
               fillColor: p.danger, fillOpacity: 0.25 };
    case "coastline":
      return { renderer: chartRenderer, pane: "chart", color: p.coast, weight: 1.6, fill: false };
    case "contour":
      return { renderer: chartRenderer, pane: "chart", color: p.contour, weight: 1, dashArray: "5 4", fill: false };
    case "track":
      return { renderer: chartRenderer, pane: "chart", color: p.track, weight: 2, dashArray: "10 6", fill: false, opacity: 0.85 };
    case "hazard_line":
      return { renderer: chartRenderer, pane: "chart", color: p.hazard, weight: 2, dashArray: "3 4", fill: false };
    default:
      return { renderer: chartRenderer, pane: "chart", color: p.coast, weight: 1, fill: false };
  }
}

// Point features become small chart symbols. Kept as canvas circleMarkers rather than DOM icons so
// hundreds of them cost nothing to redraw while the chart rotates.
function chartPoint(kind, feature, latlng) {
  const p = palette();
  const props = feature.properties || {};
  const base = { renderer: chartRenderer, pane: "chart" };
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
  base: { label: "Land & shoreline", hint: "Land fill, the shoreline, bridges, dams and built-up areas",
          layers: ["land", "rivers", "built_up", "harbour", "berths", "bridge", "dam", "shoreline_construction", "pylons_area", "coastline"] },
  depths: { label: "Depths & contours", hint: "Depth-shaded water and depth contour lines",
            layers: ["depth_area", "depth_contour"] },
  aids: { label: "Aids to navigation", hint: "Buoys, beacons, lights, daymarks and river mile markers",
          layers: ["lateral_buoy", "isolated_danger_buoy", "special_purpose_buoy", "lateral_beacon", "daymark", "light", "distance_mark", "recommended_track"] },
  hazards: { label: "Hazards & caution areas", hint: "Wrecks, rocks, obstructions, cables, pipelines and caution areas",
             layers: ["caution", "rock_area", "wreck_area", "obstruction_area", "obstruction", "underwater_rock", "wreck", "pile", "pylons", "overhead_cable", "submarine_pipeline", "obstruction_line"] },
  landmarks: { label: "Landmarks", hint: "Charted landmarks and shoreline structures",
               layers: ["landmark", "shoreline_construction_point"] },
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
const DRAW_ORDER = ["area", "depth", "caution", "danger", "coastline", "contour", "hazard_line",
                     "track", "landmark", "distance_mark", "danger_point", "beacon", "buoy", "light"];

const chartGroup = L.layerGroup().addTo(map);
const chartLayers = {};        // layer name -> { kind, leafletLayer }
let chartArea = null;          // the area currently loaded
let chartDetail = null;        // "detail" or "overview" -- whichever the zoom calls for
let chartLoading = false;
const DETAIL_FROM_ZOOM = (z) => (z >= 13 ? "detail" : "overview");

function renderChartBundle(bundle) {
  chartGroup.clearLayers();
  Object.keys(chartLayers).forEach((k) => delete chartLayers[k]);
  const entries = Object.entries(bundle.layers || {});
  entries.sort((a, b) => DRAW_ORDER.indexOf(a[1].kind) - DRAW_ORDER.indexOf(b[1].kind));
  entries.forEach(([name, { kind, geojson }]) => {
    const layer = L.geoJSON(geojson, {
      renderer: chartRenderer,
      pane: "chart",
      style: (f) => chartStyle(kind, f),
      pointToLayer: (f, latlng) => chartPoint(kind, f, latlng),
      onEachFeature: (f, lyr) => lyr.on("click", (e) => {
        L.DomEvent.stopPropagation(e);
        showChartFeature(name, kind, f.properties || {});
      }),
    });
    chartLayers[name] = { kind, layer };
    if (layerGroupOn(groupForLayer(name))) layer.addTo(chartGroup);
  });
}

async function loadChart(area, detail) {
  if (chartLoading) return;
  chartLoading = true;
  try {
    const bundle = await (await fetch(`/api/chart/${area}/${detail}`)).json();
    if (!bundle || !bundle.layers) return;
    chartArea = area;
    chartDetail = detail;
    renderChartBundle(bundle);
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
    if (!areas || !areas.length) return;
    await loadChart(areas[0].name, DETAIL_FROM_ZOOM(map.getZoom()));
  } catch (e) { /* offline or nothing fetched yet */ }
}

// Swap between the generalised overview and the full-detail chart as the zoom crosses the threshold.
function refreshChartDetail() {
  if (!chartArea) return;
  const wanted = DETAIL_FROM_ZOOM(map.getZoom());
  if (wanted !== chartDetail) loadChart(chartArea, wanted);
}

// Night mode is now a real restyle rather than a CSS filter over a picture: same geometry, new colours.
function restyleChart() {
  Object.entries(chartLayers).forEach(([name, { kind, layer }]) => {
    layer.eachLayer((lyr) => {
      if (lyr.feature && lyr.feature.geometry && lyr.feature.geometry.type.includes("Point")) {
        const p = palette();
        const props = lyr.feature.properties || {};
        if (kind === "buoy" || kind === "beacon") lyr.setStyle({ fillColor: aidColor(props, p), color: p.textHalo });
        else if (kind === "light") lyr.setStyle({ color: aidColor(props, p), fillColor: aidColor(props, p) });
        else if (kind === "danger_point") lyr.setStyle({ color: p.danger, fillColor: p.danger });
        else lyr.setStyle({ color: p.coast, fillColor: p.coast });
      } else if (lyr.setStyle && lyr.feature) {
        lyr.setStyle(chartStyle(kind, lyr.feature));
      }
    });
  });
  mapEl.style.background = palette().chartBg;
}

function setChartLayerGroup(key, on) {
  chartLayerState[key] = on;
  try { localStorage.setItem("chartLayers", JSON.stringify(chartLayerState)); } catch (e) { /* storage unavailable */ }
  Object.entries(chartLayers).forEach(([name, { layer }]) => {
    if (groupForLayer(name) !== key) return;
    if (on) layer.addTo(chartGroup);
    else chartGroup.removeLayer(layer);
  });
}

// ---------- Identify: tap a charted feature and find out what it is ----------
// The thing a raster chart fundamentally cannot do. IENC carries the full S-57 attribution, so a
// beacon can report itself properly: name, what kind of mark it is, and its light characteristic
// in the notation actually printed on charts ("Fl(2)R 5s").
const CHART_KIND_LABELS = {
  buoy: "Buoy", beacon: "Beacon", light: "Light", distance_mark: "River mile marker",
  danger_point: "Hazard", landmark: "Landmark", depth: "Depth area", contour: "Depth contour",
  coastline: "Shoreline", track: "Recommended track", caution: "Caution area",
  danger: "Hazard area", area: "Land", hazard_line: "Hazard",
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
  if (kind === "depth") {
    const lo = parseFloat(props.Depth_Area_Value_1), hi = parseFloat(props.Depth_Area_Value_2);
    if (isFinite(hi)) {
      const f = (m) => `${(m * 3.28084).toFixed(1)} ft`;
      rows.push(["Depth", isFinite(lo) ? `${f(lo)} to ${f(hi)}` : `to ${f(hi)}`]);
    }
  }
  if (kind === "contour" && props.Value_of_Depth_Contour != null) {
    rows.push(["Contour", `${(parseFloat(props.Value_of_Depth_Contour) * 3.28084).toFixed(0)} ft`]);
  }
  if (props.Category_of_Recommended_Track) rows.push(["Track", props.Category_of_Recommended_Track]);
  if (props.Traffic_Flow) rows.push(["Traffic", props.Traffic_Flow]);
  if (props.Vertical_Clearance_Value) rows.push(["Clearance", `${props.Vertical_Clearance_Value} m`]);
  if (props.Source_Dataset) rows.push(["Chart", props.Source_Dataset.replace(/\.000$/, "")]);
  return { title: name || CHART_KIND_LABELS[kind] || "Charted feature",
           subtitle: name ? CHART_KIND_LABELS[kind] || "" : "", rows };
}

function showChartFeature(layerName, kind, props) {
  // Live inside the map container so the panel travels with the chart between the Helm and the
  // full Nav. Chart screen (attachMap moves #map between them) instead of covering the Trip widget.
  const el = $("chartFeature");
  if (el.parentElement !== mapEl) mapEl.appendChild(el);
  const { title, subtitle, rows } = chartFeatureSummary(kind, props);
  const body = rows.map(([k, v]) => `<div class="cf-row"><span>${k}</span><b>${v}</b></div>`).join("");
  el.innerHTML = `<div class="cf-head"><div><div class="cf-title">${title}</div>` +
    `${subtitle ? `<div class="cf-sub">${subtitle}</div>` : ""}</div>` +
    `<button class="cf-close" aria-label="Close">&times;</button></div>${body}`;
  el.hidden = false;
  el.querySelector(".cf-close").addEventListener("click", () => (el.hidden = true));
}

// ---------- My Vessel: heading line and compass rose (Options -> Map layers & colors -> My Vessel) ----------
// Mirrors Garmin's own "Layers > My Vessel" menu: a heading line projects ahead of the boat, either a fixed
// distance or how far it will travel in a set time at its current speed, and a compass rose is a fixed-size
// ring around the boat marked with the compass points. Both are per-browser display preferences, like the
// chart layer toggles above, not shared server-side.
let vesselSettings = { headingLineOn: true, headingLineMode: "distance", headingLineNm: 0.3, headingLineMinutes: 10, compassRoseOn: false };
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
// Not rotated in CSS on purpose: leaflet-rotate spins the whole marker pane along with the chart (the same
// reason the boat icon has to counter-rotate to stay pointing up in Heading Up), so a compass rose left alone
// naturally keeps its N/E/S/W points aimed at true directions on screen, exactly like a real one would.
const compassRoseMarker = L.marker([0, 0], { icon: compassRoseIcon, interactive: false, zIndexOffset: -1000 });

const headingLine = L.polyline([], { color: "#ffcf40", weight: 2, opacity: 0.85, dashArray: "1 7" }).addTo(map);

function updateHeadingLine() {
  if (!vesselSettings.headingLineOn || shownLat == null) { headingLine.setLatLngs([]); return; }
  const sogKn = lastData && lastData.gps.has_fix ? lastData.gps.sog_kn || 0 : 0;
  const distNm = vesselSettings.headingLineMode === "time" ? sogKn * (vesselSettings.headingLineMinutes / 60) : vesselSettings.headingLineNm;
  if (!(distNm > 0)) { headingLine.setLatLngs([]); return; }
  headingLine.setLatLngs([[shownLat, shownLon], projectLatLng(shownLat, shownLon, shownHeading, distNm)]);
}

function applyVesselSettings() {
  if (vesselSettings.compassRoseOn) compassRoseMarker.addTo(map);
  else map.removeLayer(compassRoseMarker);
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
map.on("zoomend", () => { isZooming = false; lockBoatFrame(); refreshChartDetail(); });

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
let headingUp = false;
try { headingUp = localStorage.getItem("chartOrient") === "heading"; } catch (e) { /* storage unavailable */ }

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
  const svg = boatMarker.getElement() && boatMarker.getElement().querySelector("svg");
  if (headingUp) {
    map.setBearing((360 - shownHeading) % 360);   // the current heading points up; the boat icon stays put
    if (svg) svg.style.transform = "rotate(0deg)";
    lockBoatFrame();
  } else if (svg) {
    svg.style.transform = `rotate(${shownHeading}deg)`;
  }

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
function lockBoatFrame() {
  if (!headingUp || shownLat == null || isZooming) return;
  const size = map.getSize();
  if (size.x < 10 || size.y < 10) return;  // not laid out yet (e.g. its screen isn't showing)
  const boatPt = map.latLngToContainerPoint(L.latLng(shownLat, shownLon));
  const desiredPt = L.point(size.x / 2, (size.y * 2) / 3);
  const delta = boatPt.subtract(desiredPt);
  if (Math.abs(delta.x) > 0.25 || Math.abs(delta.y) > 0.25) map.panBy(delta, { animate: false });
}

function syncOrientUI() {
  document.querySelectorAll(".zoom button.orient").forEach((b) => b.classList.toggle("active", headingUp));
  const opt = document.getElementById("optOrient");
  if (opt) opt.textContent = headingUp ? "Heading Up" : "North Up";
}

function setChartOrient(mode) {
  headingUp = mode === "heading";
  if (headingUp) {
    map.setBearing((360 - shownHeading) % 360);
    lockBoatFrame();
  } else {
    map.setBearing(0);
    if (shownLat != null) map.panTo([shownLat, shownLon]);  // north-up expects the boat back at plain center
  }
  syncOrientUI();
  try { localStorage.setItem("chartOrient", headingUp ? "heading" : "north"); } catch (e) { /* storage unavailable */ }
}
document.querySelectorAll('[data-act="orient"]').forEach((b) => b.addEventListener("click", () => setChartOrient(headingUp ? "north" : "heading")));
syncOrientUI();  // the saved preference (read into headingUp above) needs applying to the button/Options too, not just the map

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

// Keep the boat on screen, but leave the chart alone for a while after the driver pans or zooms it.
let lastUserMove = 0;
["pointerdown", "wheel"].forEach((ev) => map.getContainer().addEventListener(ev, () => (lastUserMove = Date.now()), true));
const centerOnBoat = () => { lastUserMove = 0; if (lastData && lastData.gps.has_fix) map.setView([lastData.gps.lat, lastData.gps.lon], map.getZoom()); };

async function setWaypoint(lat, lon, name = "WP") {
  await postJson("/api/waypoint", { lat, lon, name });
}
const clearWaypoint = () => fetch("/api/waypoint", { method: "DELETE" });
map.on("click", async (e) => {
  $("chartFeature").hidden = true;   // a tap on open water dismisses a stale feature readout
  await setWaypoint(e.latlng.lat, e.latlng.lng);
  if (typeof openPanel === "function") openPanel("waypoint");
});

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
  if (!headingUp && Date.now() - lastUserMove > 20000 && !map.getBounds().pad(-0.3).contains([gps.lat, gps.lon])) {
    map.panTo([gps.lat, gps.lon]);
  }
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

const OVERLAY_FIELDS = {
  sog: { label: "Speed", decimals: 1, unit: () => UNITS[unit].speed, get: (d) => (d && d.gps.has_fix ? toSpeed(d.gps.sog_kn) : null) },
  hdg: { label: "Heading", decimals: 0, unit: () => "°", get: (d) => (d && d.gps.has_fix ? d.gps.heading_deg : null) },
  cog: { label: "Course", decimals: 0, unit: () => "°", get: (d) => (d && d.gps.has_fix ? d.gps.cog_deg : null) },
  depth: { label: "Depth", decimals: 1, unit: () => "ft", get: (d) => (d ? d.boat_info.depth_ft : null) },
  wtemp: { label: "Water Temp", decimals: 0, unit: () => "°F", get: (d) => (d ? d.boat_info.water_temp_f : null) },
  battery: { label: "Battery", decimals: 1, unit: () => "V", get: (d) => (d ? d.boat_info.battery_voltage : null) },
  fuel: { label: "Fuel", decimals: 0, unit: () => "%", get: (d) => (d ? d.engine.fuel_pct : null) },
  rpm: { label: "RPM", decimals: 0, unit: () => "", get: (d) => (d ? d.engine.rpm : null) },
  coolant: { label: "Coolant", decimals: 0, unit: () => "°F", get: (d) => (d ? d.engine.coolant_f : null) },
  tripdist: { label: "Trip Dist", decimals: 1, unit: () => UNITS[unit].dist, get: (d) => (d && d.trip ? toDist(d.trip.distance_nm) : null) },
  time: { label: "Time", isTime: true, get: () => new Date() },
  sunrise: { label: "Sunrise", isTime: true, get: (d) => (d && d.sun ? d.sun.sunrise : null) },
  sunset: { label: "Sunset", isTime: true, get: (d) => (d && d.sun ? d.sun.sunset : null) },
  moonphase: {
    label: "Moon Phase", isText: true,
    get: (d) => (d && d.sun ? d.sun.moon_phase : null),
    sub: (d) => (d && d.sun && d.sun.moon_illumination != null ? `${Math.round(d.sun.moon_illumination * 100)}%` : ""),
  },
};

let overlayPickCallback = null;
function openOverlayPicker(onPick) {
  overlayPickCallback = onPick;
  const body = $("overlayPickBody");
  body.replaceChildren(...Object.entries(OVERLAY_FIELDS).map(([key, f]) => {
    const b = document.createElement("button");
    b.className = "row";
    b.innerHTML = `<span>${f.label}</span><span class="row-val">›</span>`;
    b.addEventListener("click", () => {
      closePanels();
      if (overlayPickCallback) overlayPickCallback(key);
    });
    return b;
  }));
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
      } else if (f.isText) {
        valEl.classList.add("text");
        valEl.querySelector("b").textContent = val == null ? "--" : val;
        valEl.querySelector("small").textContent = f.sub ? f.sub(data) : "";
      } else {
        valEl.classList.remove("text");
        valEl.querySelector("b").textContent = val == null ? "--" : val.toFixed(f.decimals);
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
    eta_minutes: routeNav.eta_minutes, xte_nm: routeNav.xte_nm,
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
  $("wpBearing").textContent = active.bearing_deg.toFixed(0);
  $("wpDist").textContent = toDist(active.distance_nm).toFixed(2);
  $("wpEta").textContent = active.eta_minutes != null ? active.eta_minutes.toFixed(0) : "--";
  $("wpXte").textContent = active.xte_nm != null ? toDist(active.xte_nm).toFixed(2) : "--";

  const wpLatLng = [active.waypoint.lat, active.waypoint.lon];
  if (!waypointMarker) waypointMarker = L.circleMarker(wpLatLng, { radius: 9, color: "#fff", weight: 2, fillColor: "#e0202b", fillOpacity: 1 }).addTo(map);
  else waypointMarker.setLatLng(wpLatLng);
  if (!wpLine) wpLine = L.polyline([boatLatLng, wpLatLng], { color: "#e0202b", weight: 3, dashArray: "8 6" }).addTo(map);
  else wpLine.setLatLngs([boatLatLng, wpLatLng]);
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
      q(".mw-progress .bar i").style.width = `${pct}%`;
      q(".t-el").textContent = live && media.length_s ? fmtClock(media.position_s) : "";
      q(".t-left").textContent = live && media.length_s ? "-" + fmtClock(Math.max(0, media.length_s - media.position_s)) : "";
      q(".mw-play svg").innerHTML = media.playing ? ICON.pause : ICON.play;
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
    // Rotate the inner <svg>, not the marker's own root element -- that root's transform is how
    // Leaflet positions the marker, the same reason the boat icon's own rotation works this way.
    const svg = marker.getElement() && marker.getElement().querySelector("svg");
    if (svg) svg.style.transform = `rotate(${t.cog_deg}deg)`;
  });
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
