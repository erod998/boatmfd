"use strict";
// The MFD "chrome": screens, the Home overlay, the bottom menu bar, side panels and alerts.

// ---------- Screens and Home ----------
const svgThumb = (inner) => `<svg viewBox="0 0 160 100" xmlns="http://www.w3.org/2000/svg">${inner}</svg>`;
const dialThumb = (cx, cy, r, arc) =>
  `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#0b0c0d" stroke="#5a5d62" stroke-width="2.5"/>` +
  `<circle cx="${cx}" cy="${cy}" r="${r - 5}" fill="none" stroke="#3b7be8" stroke-width="5" stroke-dasharray="${arc} 400" transform="rotate(135 ${cx} ${cy})"/>` +
  `<path d="M${cx} ${cy} L${cx + r * 0.6} ${cy - r * 0.6}" stroke="#fff" stroke-width="2"/>`;
const CHART_ART = `<rect width="160" height="100" fill="#2c6db5"/>` +
  `<path d="M0 0H72C64 24 78 40 58 60C46 74 22 70 0 88Z" fill="#e9d76a"/><path d="M112 100C120 72 148 62 160 42V100Z" fill="#e9d76a"/>` +
  `<path d="M24 92L128 22" stroke="#d63a8c" stroke-width="3"/><path d="M100 46l8 16h-16z" fill="#fff" stroke="#111" stroke-width="1.5" transform="rotate(38 100 54)"/>`;

const SCREENS = {
  helm: { label: "Helm", thumb: svgThumb(
    `<rect width="160" height="100" fill="#000"/><rect x="2" y="2" width="36" height="22" fill="#131416"/><rect x="40" y="2" width="36" height="22" fill="#131416"/><rect x="78" y="2" width="80" height="22" fill="#131416"/>` +
    `<circle cx="20" cy="13" r="8" fill="none" stroke="#3b7be8" stroke-width="3"/><circle cx="58" cy="13" r="8" fill="none" stroke="#3b7be8" stroke-width="3"/>` +
    `<g transform="translate(2 26) scale(0.96 0.5)">${CHART_ART}</g><rect x="2" y="78" width="48" height="20" fill="#131416"/><rect x="52" y="78" width="60" height="20" fill="#131416"/><rect x="114" y="78" width="44" height="20" fill="#131416"/>`) },
  chart: { label: "Nav. Chart", thumb: svgThumb(CHART_ART) },
  gauges: { label: "Gauges", thumb: svgThumb(`<rect width="160" height="100" fill="#111214"/>${dialThumb(30, 52, 26, 70)}${dialThumb(80, 50, 30, 20)}${dialThumb(130, 52, 26, 90)}`) },
  trip: { label: "Trip", thumb: svgThumb(
    `<rect width="160" height="100" fill="#111214"/>` + [[8, 10], [86, 10], [8, 40], [86, 40], [8, 70], [86, 70]].map(([x, y]) => `<rect x="${x}" y="${y}" width="66" height="24" fill="none" stroke="#7a7d82" stroke-width="2"/><rect x="${x + 8}" y="${y + 9}" width="30" height="9" fill="#e8eaec"/>`).join("")) },
  media: { label: "Media", thumb: svgThumb(
    `<rect width="160" height="100" fill="#18122e"/><rect x="12" y="14" width="66" height="66" fill="#5b3aa8"/><circle cx="60" cy="32" r="10" fill="#f0a15a"/><text x="45" y="66" font-size="34" fill="#fff" text-anchor="middle">&#9835;</text>` +
    `<rect x="92" y="24" width="56" height="7" fill="#e8eaec"/><rect x="92" y="38" width="40" height="5" fill="#8a8d92"/><rect x="92" y="60" width="56" height="4" fill="#3a3d42"/><rect x="92" y="60" width="26" height="4" fill="#3b9cf5"/><circle cx="106" cy="80" r="8" fill="#0b4f97"/>`) },
  lights: { label: "RGB Lights", thumb: svgThumb(
    `<rect width="160" height="100" fill="#111214"/>` + ["#ff0000", "#ff5a00", "#ffb000", "#ffee00", "#00ff00", "#00ffaa", "#00ffff", "#003cff", "#8c00ff", "#ff00c8", "#ffb46e", "#ffffff"].map((c, i) => `<circle cx="${22 + (i % 6) * 23}" cy="${34 + Math.floor(i / 6) * 30}" r="9" fill="${c}"/>`).join("")) },
  switching: { label: "Switching", thumb: svgThumb(
    `<rect width="160" height="100" fill="#111214"/>` + [0, 1, 2, 3].map((i) => `<rect x="${10 + i * 38}" y="20" width="30" height="60" rx="15" fill="#26282b" stroke="#5a5d62" stroke-width="2"/><circle cx="${25 + i * 38}" cy="${i % 2 ? 62 : 38}" r="10" fill="${i % 2 ? '#3a3d42' : '#3b7be8'}"/>`).join("")) },
};
const PINNED = ["helm", "chart", "gauges", "media", "trip", "lights"];
const CATEGORIES = [
  { id: "pinned", label: "Pinned", items: PINNED },
  { id: "charts", label: "Charts", items: ["chart"] },
  { id: "combo", label: "Combo", items: ["helm"] },
  { id: "vessel", label: "Vessel", items: ["gauges", "trip", "switching"] },
  { id: "media", label: "Media", items: ["media"] },
  { id: "lights", label: "Lights", items: ["lights"] },
];
const thumbUri = (id) => `url("data:image/svg+xml;utf8,${encodeURIComponent(SCREENS[id].thumb)}")`;

let currentScreen = "helm";
let homeCategory = "pinned";

function closeOverlays() {
  $("home").hidden = true;
  closePanels();
}

function showScreen(id) {
  currentScreen = id;
  document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("active", s.id === `screen-${id}`));
  attachMap(document.querySelector(`#screen-${id} .map-slot`));
  refreshGauges();  // dials and bars that changed while their screen was hidden redraw now
  closeOverlays();
  updateMiniThumbs();
  try { localStorage.setItem("screen", id); } catch (e) { /* storage unavailable */ }
}

function stepPinned(dir) {
  const i = PINNED.indexOf(currentScreen);
  const next = PINNED[(i + dir + PINNED.length) % PINNED.length];
  showScreen(next);
}

function updateMiniThumbs() {
  const i = Math.max(0, PINNED.indexOf(currentScreen));
  $("miniPrev").style.backgroundImage = thumbUri(PINNED[(i - 1 + PINNED.length) % PINNED.length]);
  $("miniNext").style.backgroundImage = thumbUri(PINNED[(i + 1) % PINNED.length]);
}

function renderHome() {
  const cat = CATEGORIES.find((c) => c.id === homeCategory);
  $("homeCats").replaceChildren(...CATEGORIES.map((c) => {
    const b = document.createElement("button");
    b.className = "ht-cat" + (c.id === homeCategory ? " selected" : "");
    b.textContent = c.label;
    b.addEventListener("click", () => { homeCategory = c.id; renderHome(); });
    return b;
  }));
  $("homeFeatures").replaceChildren(...cat.items.map((id) => {
    const b = document.createElement("button");
    b.className = "feature" + (id === currentScreen ? " current" : "");
    b.innerHTML = `<div class="thumb">${SCREENS[id].thumb}</div><span>${SCREENS[id].label}</span>`;
    b.addEventListener("click", () => showScreen(id));
    return b;
  }));
}

function toggleHome() {
  const opening = $("home").hidden;
  closePanels();
  $("home").hidden = !opening;
  if (opening) { renderHome(); updateHomeClock(); }
}

function updateHomeClock() {
  if ($("home").hidden) return;
  const now = new Date();
  $("homeTime").textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  $("homeDate").textContent = now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}
setInterval(updateHomeClock, 1000);
updateHomeClock();
$("homeClose").addEventListener("click", () => ($("home").hidden = true));
$("homeSettings").addEventListener("click", () => { $("home").hidden = true; openPanel("options"); });

// ---------- Side panels: Options, Alerts, Info, Waypoint ----------
function closePanels() {
  document.querySelectorAll(".panel").forEach((p) => (p.hidden = true));
}

function openPanel(name) {
  $("home").hidden = true;
  const panel = $(`panel-${name}`);
  const wasOpen = !panel.hidden;
  closePanels();
  if (wasOpen && name !== "waypoint" && name !== "alarm") return;  // pressing the same menu button again closes it
  panel.hidden = false;
  if (name === "alerts") renderAlerts();
  if (name === "info") renderInfo();
  if (name === "options") renderOptions();
  if (name === "mapsettings") buildMapSettingsMenu();
  if (name === "navdata") buildNavDataMenu();
  if (name === "navalarms") buildNavAlarmsMenu();
  if (name === "ais") buildAisMenu();
}
document.querySelectorAll(".p-close").forEach((b) => b.addEventListener("click", closePanels));

// Whether a new NMEA alarm mutes the stereo for a few seconds (app/telemute.py); a server setting,
// not a per-browser one like night mode, so every screen agrees on it.
let telemuteEnabled = true;
fetch("/api/telemute").then((r) => r.json()).then((d) => { telemuteEnabled = d.enabled; renderOptions(); }).catch(() => {});

function renderOptions() {
  $("optNight").textContent = chartColorMode === "day" ? "Off" : chartColorMode === "dusk" ? "Dusk" : "On";
  $("optUnits").textContent = unit === "mph" ? "mph / mi" : "kn / nm";
  $("optOrient").textContent = headingUp ? "Heading Up" : "North Up";
  $("optTelemute").textContent = telemuteEnabled ? "On" : "Off";
}

document.querySelectorAll("[data-opt]").forEach((b) => b.addEventListener("click", async () => {
  const opt = b.dataset.opt;
  if (opt === "night") { setChartMode(nightMode ? "day" : "night"); renderOptions(); }
  else if (opt === "units") { toggleUnits(); renderOptions(); }
  else if (opt === "alarms") openAlarmMenu(null);
  else if (opt === "orient") { setChartOrient(headingUp ? "north" : "heading"); renderOptions(); }
  else if (opt === "telemute") {
    const res = await (await postJson("/api/telemute", { enabled: !telemuteEnabled })).json();
    telemuteEnabled = res.enabled;
    renderOptions();
  }
  else if (opt === "mapsettings") openPanel("mapsettings");
  else if (opt === "navdata") openPanel("navdata");
  else if (opt === "navalarms") openPanel("navalarms");
  else if (opt === "ais") openPanel("ais");
  else if (opt === "center") { centerOnBoat(); closePanels(); }
  else if (opt === "clearwp") { clearWaypoint(); closePanels(); }
  else if (opt === "waypoint") openPanel("waypoint");
}));

// ---------- Map Settings: chart colors (Day/Dusk/Night) and which chart layers are drawn ----------
function mapSettingsSection(body, title) {
  const h = document.createElement("div");
  h.className = "info-sec";
  h.textContent = title;
  body.appendChild(h);
}

function buildMapSettingsMenu() {
  const body = $("mapSettingsBody");
  body.replaceChildren();

  // ---------------- Chart: colors and the ENC layer groups ----------------
  mapSettingsSection(body, "Chart");

  const colorBlock = document.createElement("div");
  colorBlock.className = "al-block";
  colorBlock.innerHTML = '<div class="al-head"><span class="al-name">Chart colors</span></div>' +
    '<div class="al-row"><button class="btn" data-color="day">Day</button><button class="btn" data-color="dusk">Dusk</button><button class="btn" data-color="night">Night</button></div>';
  body.appendChild(colorBlock);
  const refreshColors = () => colorBlock.querySelectorAll("[data-color]").forEach((b) => b.classList.toggle("primary", b.dataset.color === chartColorMode));
  refreshColors();
  colorBlock.querySelectorAll("[data-color]").forEach((b) => b.addEventListener("click", () => {
    setChartMode(b.dataset.color);
    refreshColors();
    renderOptions();
  }));

  Object.entries(CHART_LAYER_GROUPS).forEach(([key, g]) => {
    const block = document.createElement("div");
    block.className = "al-block";
    block.innerHTML = `<div class="al-head"><span class="al-name">${g.label}</span></div>` +
      `<div class="al-cap">${g.hint}</div><button class="btn al-toggle"></button>`;
    body.appendChild(block);
    const toggle = block.querySelector(".al-toggle");
    const refresh = () => {
      toggle.textContent = layerGroupOn(key) ? "Shown" : "Hidden";
      toggle.classList.toggle("primary", layerGroupOn(key));
    };
    refresh();
    toggle.addEventListener("click", () => { setChartLayerGroup(key, !layerGroupOn(key)); refresh(); });
  });

  const qdBlock = document.createElement("div");
  qdBlock.className = "al-block";
  qdBlock.innerHTML = '<div class="al-head"><span class="al-name">My Depth Map (Quickdraw)</span></div>' +
    "<div class=\"al-cap\">Records a colored dot at the boat's own depth reading as you cruise -- real (or simulated) data, not invented, but simplified from Garmin's own smoothed contour lines</div>" +
    '<div class="al-row"><button class="btn" id="qdRecordBtn"></button><button class="btn" id="qdClearBtn">Clear</button></div>';
  body.appendChild(qdBlock);
  const qdRecordBtn = qdBlock.querySelector("#qdRecordBtn");
  const refreshQdButton = () => {
    qdRecordBtn.textContent = quickdrawRecording ? "Recording" : "Start Recording";
    qdRecordBtn.classList.toggle("primary", quickdrawRecording);
  };
  refreshQdButton();
  qdRecordBtn.addEventListener("click", async () => {
    quickdrawRecording = !quickdrawRecording;
    await postJson("/api/quickdraw", { enabled: quickdrawRecording });
    refreshQdButton();
  });
  qdBlock.querySelector("#qdClearBtn").addEventListener("click", async () => {
    await fetch("/api/quickdraw", { method: "DELETE" });
    clearQuickdrawDots();
  });

  // ---------------- My Vessel: heading line and compass rose ----------------
  mapSettingsSection(body, "My Vessel");

  const hlBlock = document.createElement("div");
  hlBlock.className = "al-block";
  hlBlock.innerHTML = '<div class="al-head"><span class="al-name">Heading Line</span></div>' +
    '<div class="al-cap">A line ahead of the boat showing the direction of travel</div>' +
    '<button class="btn al-toggle" data-hl-enable></button>' +
    '<div class="al-row"><button class="btn" data-hl-mode="distance">Distance</button><button class="btn" data-hl-mode="time">Time</button></div>' +
    '<div class="hl-value"></div>';
  body.appendChild(hlBlock);
  const hlEnable = hlBlock.querySelector("[data-hl-enable]");
  const refreshHlEnable = () => {
    hlEnable.textContent = vesselSettings.headingLineOn ? "Heading Line is ON" : "Heading Line is OFF";
    hlEnable.classList.toggle("primary", vesselSettings.headingLineOn);
  };
  refreshHlEnable();
  hlEnable.addEventListener("click", () => { setVesselSetting("headingLineOn", !vesselSettings.headingLineOn); refreshHlEnable(); });
  hlBlock.querySelectorAll("[data-hl-mode]").forEach((b) => b.classList.toggle("primary", b.dataset.hlMode === vesselSettings.headingLineMode));
  hlBlock.querySelectorAll("[data-hl-mode]").forEach((b) => b.addEventListener("click", () => {
    setVesselSetting("headingLineMode", b.dataset.hlMode);
    buildMapSettingsMenu();   // the value control below switches units (nm vs minutes), simplest to just redraw
  }));
  const hlValueHolder = hlBlock.querySelector(".hl-value");
  hlValueHolder.innerHTML = levelMarkup("Shorter", "Longer");
  if (vesselSettings.headingLineMode === "time") {
    levelControl(hlValueHolder.querySelector(".level-ctl"), {
      min: 1, getMax: () => 30, step: 1,
      text: (v) => `${Math.round(v)} min`,
      onChange: (v) => setVesselSetting("headingLineMinutes", v),
    }).set(vesselSettings.headingLineMinutes, true);
  } else {
    levelControl(hlValueHolder.querySelector(".level-ctl"), {
      min: 0.1, getMax: () => 2, step: 0.1,
      text: (v) => `${v.toFixed(1)} nm`,
      onChange: (v) => setVesselSetting("headingLineNm", v),
    }).set(vesselSettings.headingLineNm, true);
  }

  const crBlock = document.createElement("div");
  crBlock.className = "al-block";
  crBlock.innerHTML = '<div class="al-head"><span class="al-name">Compass Rose</span></div>' +
    '<div class="al-cap">A compass ring around the boat, always pointing to true directions</div>' +
    '<button class="btn al-toggle"></button>';
  body.appendChild(crBlock);
  const crToggle = crBlock.querySelector(".al-toggle");
  const refreshCr = () => {
    crToggle.textContent = vesselSettings.compassRoseOn ? "Compass Rose is ON" : "Compass Rose is OFF";
    crToggle.classList.toggle("primary", vesselSettings.compassRoseOn);
  };
  refreshCr();
  crToggle.addEventListener("click", () => { setVesselSetting("compassRoseOn", !vesselSettings.compassRoseOn); refreshCr(); });

  // ---------------- User Data: the recorded breadcrumb trail ----------------
  mapSettingsSection(body, "User Data");

  const trackBlock = document.createElement("div");
  trackBlock.className = "al-block";
  trackBlock.innerHTML = '<div class="al-head"><span class="al-name">Track</span></div>' +
    '<div class="al-cap">The breadcrumb trail recorded as the boat moves</div>' +
    '<div class="al-row"><button class="btn al-toggle" data-track-visible></button><button class="btn" data-track-clear>Clear Track</button></div>' +
    '<div class="al-row"><input class="track-name" placeholder="Name this track (optional)" /><button class="btn primary" data-track-save>Save Track</button></div>';
  body.appendChild(trackBlock);
  const trackToggle = trackBlock.querySelector("[data-track-visible]");
  const refreshTrack = () => {
    trackToggle.textContent = trackVisible ? "Shown" : "Hidden";
    trackToggle.classList.toggle("primary", trackVisible);
  };
  refreshTrack();
  trackToggle.addEventListener("click", () => { setTrackVisible(!trackVisible); refreshTrack(); });
  trackBlock.querySelector("[data-track-clear]").addEventListener("click", clearTrack);
  const nameInput = trackBlock.querySelector(".track-name");
  trackBlock.querySelector("[data-track-save]").addEventListener("click", async () => {
    const res = await (await postJson("/api/tracks", { name: nameInput.value })).json();
    if (res.ok) { nameInput.value = ""; loadSavedTracks(); }
  });

  const savedBlock = document.createElement("div");
  savedBlock.className = "al-block";
  savedBlock.innerHTML = '<div class="al-head"><span class="al-name">Saved Tracks</span></div>' +
    '<div class="al-cap">Tap a track to show or hide it on the chart</div>' +
    '<div id="savedTrackList"></div>';
  body.appendChild(savedBlock);
  loadSavedTracks();
}

const shownSavedTracks = {};   // saved-track id -> its L.Polyline, once fetched and drawn
const SAVED_TRACK_COLOR = "#3bd67b";

async function loadSavedTracks() {
  const list = $("savedTrackList");
  if (!list) return;
  const { tracks } = await (await fetch("/api/tracks")).json();
  if (!tracks.length) {
    list.innerHTML = '<div class="empty-note">No saved tracks yet.</div>';
    return;
  }
  list.replaceChildren(...tracks.map((t) => {
    const row = document.createElement("div");
    row.className = "trip-row";
    const date = new Date(t.created_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    row.innerHTML = `<div><div class="trip-date">${date}</div>` +
      `<div class="trip-stats">${t.name} &middot; ${toDist(t.distance_nm).toFixed(2)} ${UNITS[unit].dist} &middot; ${t.points} pts</div></div>` +
      `<button class="trip-icon" data-nav aria-label="Navigate to the start of this track" title="Navigate to start"><svg viewBox="0 0 24 24"><path d="M21 3L3 10.53v.98l6.84 2.65L12.48 21h.98L21 3z"/></svg></button>` +
      `<button class="trip-icon" data-rename aria-label="Rename this track" title="Rename"><svg viewBox="0 0 24 24"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg></button>` +
      `<button class="trip-del" aria-label="Delete saved track">&times;</button>`;
    row.querySelector("div").addEventListener("click", () => toggleSavedTrackOnChart(t.id));
    row.classList.toggle("shown-on-chart", !!shownSavedTracks[t.id]);
    row.querySelector("[data-nav]").addEventListener("click", async (e) => {
      e.stopPropagation();
      await navigateToTrackStart(t);
    });
    row.querySelector("[data-rename]").addEventListener("click", (e) => {
      e.stopPropagation();
      startRenamingTrack(row, t);
    });
    row.querySelector(".trip-del").addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch(`/api/tracks/${t.id}`, { method: "DELETE" });
      if (shownSavedTracks[t.id]) { map.removeLayer(shownSavedTracks[t.id]); delete shownSavedTracks[t.id]; }
      loadSavedTracks();
    });
    return row;
  }));
}

function startRenamingTrack(row, t) {
  const stats = row.querySelector(".trip-stats");
  stats.replaceChildren();
  const input = document.createElement("input");
  input.className = "track-name";
  input.value = t.name;
  const confirm = async () => {
    const name = input.value.trim();
    if (name && name !== t.name) await fetch(`/api/tracks/${t.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    loadSavedTracks();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") confirm(); });
  input.addEventListener("blur", confirm);
  stats.appendChild(input);
  input.focus();
  input.select();
}

// "Retracing the Active Track," for a saved one: point a waypoint back at wherever it began,
// the same way a driver would want to get back to the dock after an out-and-back cruise.
async function navigateToTrackStart(t) {
  const res = await (await fetch(`/api/tracks/${t.id}`)).json();
  if (!res.ok || !res.points.length) return;
  const start = res.points[0];
  await setWaypoint(start.lat, start.lon, `Start: ${t.name}`);
  closePanels();
  openPanel("waypoint");
}

async function toggleSavedTrackOnChart(id) {
  if (shownSavedTracks[id]) {
    map.removeLayer(shownSavedTracks[id]);
    delete shownSavedTracks[id];
  } else {
    const res = await (await fetch(`/api/tracks/${id}`)).json();
    if (!res.ok) return;
    shownSavedTracks[id] = L.polyline(res.points.map((p) => [p.lat, p.lon]), { color: SAVED_TRACK_COLOR, weight: 3, opacity: 0.85, dashArray: "6 4" }).addTo(map);
  }
  loadSavedTracks();
}

// ---------- Waypoints, Routes & Boundaries ----------
async function buildNavDataMenu() {
  const body = $("navDataBody");
  body.replaceChildren();

  mapSettingsSection(body, "Waypoints");
  const wpAddBlock = document.createElement("div");
  wpAddBlock.className = "al-block";
  wpAddBlock.innerHTML = '<div class="al-head"><span class="al-name">Mark a Waypoint</span></div>' +
    '<div class="al-row"><input class="track-name" id="newWpName" placeholder="Name (optional)" /><button class="btn primary" id="addWpBtn">Mark Here</button></div>';
  body.appendChild(wpAddBlock);
  wpAddBlock.querySelector("#addWpBtn").addEventListener("click", async () => {
    if (!lastData || !lastData.gps.has_fix) return;
    const nameInput = wpAddBlock.querySelector("#newWpName");
    await postJson("/api/waypoints", { lat: lastData.gps.lat, lon: lastData.gps.lon, name: nameInput.value });
    nameInput.value = "";
    loadWaypointList();
    buildRoutePickList();
  });
  const wpListBlock = document.createElement("div");
  wpListBlock.className = "al-block";
  wpListBlock.innerHTML = '<div id="wpListArea"></div>';
  body.appendChild(wpListBlock);
  loadWaypointList();

  mapSettingsSection(body, "Routes");
  const routeBuildBlock = document.createElement("div");
  routeBuildBlock.className = "al-block";
  routeBuildBlock.innerHTML = '<div class="al-head"><span class="al-name">Build a Route from Waypoints</span></div>' +
    '<div class="al-cap">Check two or more, in the order you want to visit them</div>' +
    '<div id="routePickList"></div>' +
    '<div class="al-row"><input class="track-name" id="newRouteName" placeholder="Route name (optional)" /><button class="btn primary" id="createRouteBtn">Create Route</button></div>';
  body.appendChild(routeBuildBlock);
  buildRoutePickList();
  routeBuildBlock.querySelector("#createRouteBtn").addEventListener("click", async () => {
    const checked = [...routeBuildBlock.querySelectorAll("[data-wp-check]:checked")];
    if (checked.length < 2) return;
    const points = checked.map((c) => ({ lat: Number(c.dataset.lat), lon: Number(c.dataset.lon), name: c.dataset.wpName }));
    const nameInput = routeBuildBlock.querySelector("#newRouteName");
    await postJson("/api/routes", { points, name: nameInput.value });
    nameInput.value = "";
    loadRouteList();
  });
  const routeListBlock = document.createElement("div");
  routeListBlock.className = "al-block";
  routeListBlock.innerHTML = '<div id="routeListArea"></div>';
  body.appendChild(routeListBlock);
  loadRouteList();

  mapSettingsSection(body, "Boundaries");
  const boundaryAddBlock = document.createElement("div");
  boundaryAddBlock.className = "al-block";
  boundaryAddBlock.innerHTML = '<div class="al-head"><span class="al-name">Add a Boundary at My Position</span></div>' +
    '<div class="al-row"><input class="track-name" id="newBoundaryName" placeholder="Name (optional)" /></div>' +
    '<div class="al-cap">Radius</div>' + levelMarkup("Smaller", "Bigger") +
    '<div class="al-row"><button class="btn" data-alarm-on="exit">Alarm: Exit</button><button class="btn" data-alarm-on="enter">Alarm: Enter</button><button class="btn" data-alarm-on="both">Alarm: Both</button></div>' +
    '<button class="btn primary" id="addBoundaryBtn">Add Boundary Here</button>';
  body.appendChild(boundaryAddBlock);
  let newBoundaryRadius = 300;
  let newBoundaryAlarmOn = "exit";
  levelControl(boundaryAddBlock.querySelector(".level-ctl"), {
    min: 50, getMax: () => 2000, step: 50,
    text: (v) => `${Math.round(v)} ft`,
    onChange: (v) => { newBoundaryRadius = v; },
  }).set(newBoundaryRadius, true);
  const refreshAlarmOnButtons = () => boundaryAddBlock.querySelectorAll("[data-alarm-on]").forEach((b) => b.classList.toggle("primary", b.dataset.alarmOn === newBoundaryAlarmOn));
  refreshAlarmOnButtons();
  boundaryAddBlock.querySelectorAll("[data-alarm-on]").forEach((b) => b.addEventListener("click", () => { newBoundaryAlarmOn = b.dataset.alarmOn; refreshAlarmOnButtons(); }));
  boundaryAddBlock.querySelector("#addBoundaryBtn").addEventListener("click", async () => {
    if (!lastData || !lastData.gps.has_fix) return;
    const nameInput = boundaryAddBlock.querySelector("#newBoundaryName");
    await postJson("/api/boundaries", { lat: lastData.gps.lat, lon: lastData.gps.lon, radius_ft: newBoundaryRadius, name: nameInput.value, alarm_on: newBoundaryAlarmOn });
    nameInput.value = "";
    loadBoundaryList();
  });
  const boundaryListBlock = document.createElement("div");
  boundaryListBlock.className = "al-block";
  boundaryListBlock.innerHTML = '<div id="boundaryListArea"></div>';
  body.appendChild(boundaryListBlock);
  loadBoundaryList();
}

async function loadWaypointList() {
  const area = $("wpListArea");
  if (!area) return;
  const { waypoints } = await (await fetch("/api/waypoints")).json();
  if (!waypoints.length) { area.innerHTML = '<div class="empty-note">No saved waypoints yet.</div>'; return; }
  area.replaceChildren(...waypoints.map((w) => {
    const row = document.createElement("div");
    row.className = "trip-row";
    row.innerHTML = `<div><div class="trip-date">${fmtCoord(w.lat, true)}, ${fmtCoord(w.lon, false)}</div><div class="trip-stats">${w.name}</div></div>` +
      `<button class="trip-icon" data-goto aria-label="Go to" title="Go to"><svg viewBox="0 0 24 24"><path d="M21 3L3 10.53v.98l6.84 2.65L12.48 21h.98L21 3z"/></svg></button>` +
      `<button class="trip-icon" data-rename aria-label="Rename" title="Rename"><svg viewBox="0 0 24 24"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg></button>` +
      `<button class="trip-del" aria-label="Delete waypoint">&times;</button>`;
    row.querySelector("[data-goto]").addEventListener("click", async () => {
      await fetch(`/api/waypoints/${w.id}/goto`, { method: "POST" });
      closePanels();
      openPanel("waypoint");
    });
    row.querySelector("[data-rename]").addEventListener("click", () => {
      const stats = row.querySelector(".trip-stats");
      stats.replaceChildren();
      const input = document.createElement("input");
      input.className = "track-name";
      input.value = w.name;
      const confirmRename = async () => {
        const name = input.value.trim();
        if (name && name !== w.name) await fetch(`/api/waypoints/${w.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
        loadWaypointList();
      };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") confirmRename(); });
      input.addEventListener("blur", confirmRename);
      stats.appendChild(input);
      input.focus();
      input.select();
    });
    row.querySelector(".trip-del").addEventListener("click", async () => {
      await fetch(`/api/waypoints/${w.id}`, { method: "DELETE" });
      loadWaypointList();
      buildRoutePickList();
    });
    return row;
  }));
}

async function buildRoutePickList() {
  const area = $("routePickList");
  if (!area) return;
  const { waypoints } = await (await fetch("/api/waypoints")).json();
  if (!waypoints.length) { area.innerHTML = '<p class="p-note">Mark some waypoints above first.</p>'; return; }
  area.replaceChildren(...waypoints.map((w) => {
    const label = document.createElement("label");
    label.className = "route-pick-row";
    label.innerHTML = `<input type="checkbox" data-wp-check data-lat="${w.lat}" data-lon="${w.lon}" data-wp-name="${w.name}" /><span>${w.name}</span>`;
    return label;
  }));
}

async function loadRouteList() {
  const area = $("routeListArea");
  if (!area) return;
  const { routes } = await (await fetch("/api/routes")).json();
  if (!routes.length) { area.innerHTML = '<div class="empty-note">No saved routes yet.</div>'; return; }
  area.replaceChildren(...routes.map((r) => {
    const row = document.createElement("div");
    row.className = "trip-row";
    row.classList.toggle("shown-on-chart", r.active);
    row.innerHTML = `<div><div class="trip-date">${r.legs} leg${r.legs === 1 ? "" : "s"} &middot; ${toDist(r.distance_nm).toFixed(2)} ${UNITS[unit].dist}</div><div class="trip-stats">${r.name}</div></div>` +
      `<button class="trip-icon" data-toggle aria-label="${r.active ? "Stop" : "Start"}" title="${r.active ? "Stop" : "Start"}"><svg viewBox="0 0 24 24">${r.active ? ICON.pause : ICON.play}</svg></button>` +
      `<button class="trip-del" aria-label="Delete route">&times;</button>`;
    row.querySelector("[data-toggle]").addEventListener("click", async () => {
      if (r.active) await fetch("/api/routes/stop", { method: "POST" });
      else await fetch(`/api/routes/${r.id}/start`, { method: "POST" });
      loadRouteList();
    });
    row.querySelector(".trip-del").addEventListener("click", async () => {
      await fetch(`/api/routes/${r.id}`, { method: "DELETE" });
      loadRouteList();
    });
    return row;
  }));
}

async function loadBoundaryList() {
  const area = $("boundaryListArea");
  if (!area) return;
  const { boundaries } = await (await fetch("/api/boundaries")).json();
  if (!boundaries.length) { area.innerHTML = '<div class="empty-note">No boundaries yet.</div>'; return; }
  area.replaceChildren(...boundaries.map((b) => {
    const row = document.createElement("div");
    row.className = "trip-row";
    row.classList.toggle("shown-on-chart", b.enabled);
    row.innerHTML = `<div><div class="trip-date">${Math.round(b.radius_ft)} ft &middot; alarm on ${b.alarm_on}</div><div class="trip-stats">${b.name}</div></div>` +
      `<button class="trip-icon" data-enable aria-label="${b.enabled ? "Disable" : "Enable"}" title="${b.enabled ? "Disable" : "Enable"}"><svg viewBox="0 0 24 24">${ICON.power}</svg></button>` +
      `<button class="trip-del" aria-label="Delete boundary">&times;</button>`;
    row.querySelector("[data-enable]").addEventListener("click", async () => {
      await fetch(`/api/boundaries/${b.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !b.enabled }) });
      loadBoundaryList();
    });
    row.querySelector(".trip-del").addEventListener("click", async () => {
      await fetch(`/api/boundaries/${b.id}`, { method: "DELETE" });
      loadBoundaryList();
    });
    return row;
  }));
}

// ---------- Navigation Alarms: Arrival, Off Course, Anchor Drag, GPS Accuracy ----------
function navAlarmToggleSlider(body, s, save, { title, hint, enabledKey, valueKey, min, max, step, fmt }) {
  const block = document.createElement("div");
  block.className = "al-block";
  block.innerHTML = `<div class="al-head"><span class="al-name">${title}</span></div>` +
    `<div class="al-cap">${hint}</div><button class="btn al-toggle"></button>` + levelMarkup("Less", "More");
  body.appendChild(block);
  const toggle = block.querySelector(".al-toggle");
  const refresh = () => {
    toggle.textContent = s[enabledKey] ? title + " is ON" : title + " is OFF";
    toggle.classList.toggle("primary", s[enabledKey]);
  };
  refresh();
  toggle.addEventListener("click", async () => { Object.assign(s, await save({ [enabledKey]: !s[enabledKey] })); refresh(); });
  levelControl(block.querySelector(".level-ctl"), {
    min, getMax: () => max, step, text: fmt,
    onChange: (v) => { s[valueKey] = v; save({ [valueKey]: v }); },
  }).set(s[valueKey], true);
}

async function buildNavAlarmsMenu() {
  const body = $("navAlarmsBody");
  body.replaceChildren();
  const { settings, anchor_dropped } = await (await fetch("/api/nav-alarms")).json();
  const s = settings;
  const save = async (patch) => (await (await postJson("/api/nav-alarms", patch)).json()).settings;

  navAlarmToggleSlider(body, s, save, {
    title: "Arrival", hint: "Alerts when nearing the active waypoint or route leg",
    enabledKey: "arrival_enabled", valueKey: "arrival_radius_nm", min: 0.05, max: 1, step: 0.05,
    fmt: (v) => `${v.toFixed(2)} nm`,
  });
  navAlarmToggleSlider(body, s, save, {
    title: "Off Course", hint: "Alerts when too far off the direct line to the destination",
    enabledKey: "off_course_enabled", valueKey: "off_course_xte_nm", min: 0.05, max: 2, step: 0.05,
    fmt: (v) => `${v.toFixed(2)} nm`,
  });
  navAlarmToggleSlider(body, s, save, {
    title: "GPS Accuracy", hint: "Alerts when the GPS fix gets too imprecise to trust (high HDOP)",
    enabledKey: "gps_accuracy_enabled", valueKey: "gps_accuracy_hdop_max", min: 1, max: 10, step: 0.5,
    fmt: (v) => `HDOP ${v.toFixed(1)}`,
  });

  const anchorBlock = document.createElement("div");
  anchorBlock.className = "al-block";
  anchorBlock.innerHTML = '<div class="al-head"><span class="al-name">Anchor Drag</span></div>' +
    '<div class="al-cap">Alerts if the boat drifts too far from where the anchor was dropped</div>' +
    '<div class="al-row"><button class="btn" id="anchorDropBtn"></button><button class="btn" id="anchorRaiseBtn">Raise Anchor</button></div>' +
    levelMarkup("Tighter", "Looser");
  body.appendChild(anchorBlock);
  let dropped = anchor_dropped;
  const dropBtn = anchorBlock.querySelector("#anchorDropBtn");
  const raiseBtn = anchorBlock.querySelector("#anchorRaiseBtn");
  const refreshAnchorButtons = () => {
    dropBtn.textContent = dropped ? "Anchor is Down" : "Drop Anchor Here";
    dropBtn.classList.toggle("primary", dropped);
    raiseBtn.disabled = !dropped;
  };
  refreshAnchorButtons();
  dropBtn.addEventListener("click", async () => {
    if (dropped || !lastData || !lastData.gps.has_fix) return;
    const res = await (await fetch("/api/anchor/drop", { method: "POST" })).json();
    if (res.ok) { dropped = true; refreshAnchorButtons(); }
  });
  raiseBtn.addEventListener("click", async () => {
    await fetch("/api/anchor/raise", { method: "POST" });
    dropped = false;
    refreshAnchorButtons();
  });
  levelControl(anchorBlock.querySelector(".level-ctl"), {
    min: 20, getMax: () => 500, step: 10,
    text: (v) => `${Math.round(v)} ft`,
    onChange: (v) => { s.anchor_radius_ft = v; save({ anchor_radius_ft: v }); },
  }).set(s.anchor_radius_ft, true);
}

// ---------- AIS Targets ----------
function fmtClockDuration(minutes) {
  if (minutes == null) return "--";
  if (minutes < 60) return `${minutes.toFixed(0)} min`;
  return `${(minutes / 60).toFixed(1)} hr`;
}

function renderAisList() {
  const body = $("aisBody");
  if (!body || $("panel-ais").hidden || !lastData) return;
  const targets = lastData.ais || [];
  if (!targets.length) { body.innerHTML = '<p class="p-note">No AIS targets. (No real AIS receiver is connected -- these are simulated nearby vessels, for demo purposes.)</p>'; return; }
  body.replaceChildren(...targets.map((t) => {
    const row = document.createElement("div");
    row.className = "trip-row";
    const cpa = t.cpa_nm != null ? `${t.cpa_nm.toFixed(2)} ${UNITS[unit].dist} CPA in ${fmtClockDuration(t.tcpa_min)}` : "not closing";
    row.innerHTML = `<div><div class="trip-date">${t.range_nm != null ? toDist(t.range_nm).toFixed(2) + " " + UNITS[unit].dist : "--"} &middot; ${Math.round(t.bearing_deg || 0)}&deg; &middot; ${cpa}</div>` +
      `<div class="trip-stats">${t.name} &middot; ${toSpeed(t.sog_kn).toFixed(1)} ${UNITS[unit].speed}</div></div>`;
    return row;
  }));
}

function buildAisMenu() {
  const body = $("aisBody");
  body.innerHTML = '<p class="p-note">No real AIS receiver is connected -- these are simulated nearby vessels, for demo purposes.</p>';
  renderAisList();
}
document.addEventListener("telemetry", renderAisList);

// ---------- Alerts ----------
// Coolant, oil, battery, fuel and depth alarms and warnings come from the server (see app/alarms.py), where their levels
// are set. The two checks below need the GPS or the engine speed together with another reading, so they stay here.
function computeAlerts(d) {
  const alerts = [];
  const e = d.engine;
  const b = d.boat_info;
  const running = (e.rpm || 0) > 500;
  if (lastData && !lastData.gps.has_fix) alerts.push({ level: "amber", text: "No GPS fix" });
  alarmActive.forEach((a) => alerts.push({ level: a.severity === "alarm" ? "red" : "amber", text: a.message, silenced: a.severity === "alarm" && a.acked }));
  // nav_alerts only arrives on the full (1 Hz) frame, not the fast (5 Hz) one -- like the GPS-fix
  // check above, read it from the last full frame rather than `d`, so a fast-frame call five times
  // a second doesn't wipe it out again before anyone sees it.
  ((lastData && lastData.nav_alerts) || []).forEach((a) => alerts.push({ level: a.severity === "alarm" ? "red" : "amber", text: a.message }));
  if (running && (e.rpm || 0) > 1200 && b.battery_voltage != null && b.battery_voltage >= 11.8 && b.battery_voltage < 13.0) {
    alerts.push({ level: "amber", text: `Battery not charging: ${b.battery_voltage.toFixed(1)} V` });
  }
  return alerts;
}

let activeAlerts = [];
function renderAlerts() {
  const list = $("alertList");
  if (!activeAlerts.length) {
    list.innerHTML = '<p class="p-note">No active alerts.</p>';
    return;
  }
  list.replaceChildren(...activeAlerts.map((a) => {
    const row = document.createElement("div");
    row.className = "alert-row";
    row.innerHTML = `<span class="alert-dot ${a.level}"></span><span></span>`;
    row.lastChild.textContent = a.text + (a.silenced ? "  (silenced)" : "");
    return row;
  }));
}

function updateAlerts(data) {
  data = data || lastData;
  if (!data) return;
  activeAlerts = computeAlerts(data);
  const btn = $("mbAlerts");
  const worst = activeAlerts.some((a) => a.level === "red") ? "red" : activeAlerts.length ? "amber" : "";
  btn.classList.toggle("alert-red", worst === "red");
  btn.classList.toggle("alert-amber", worst === "amber");
  const badge = btn.querySelector(".badge");
  badge.hidden = !activeAlerts.length;
  badge.textContent = activeAlerts.length;
  if (!$("panel-alerts").hidden) renderAlerts();
}

// ---------- Info ----------
async function renderInfo() {
  const d = lastData;
  let health = {};
  try { health = await (await fetch("/api/health")).json(); } catch (e) { /* offline */ }
  const rows = [];
  const sec = (t) => rows.push(`<div class="info-sec">${t}</div>`);
  const row = (k, v) => rows.push(`<div class="info-row"><span>${k}</span><span>${v}</span></div>`);
  if (d) {
    sec("Position");
    row("GPS", d.gps.has_fix ? (d.gps.fix_quality === 2 ? "DGPS fix" : "GPS fix") : "No fix");
    row("Latitude", d.gps.has_fix ? fmtCoord(d.gps.lat, true) : "--");
    row("Longitude", d.gps.has_fix ? fmtCoord(d.gps.lon, false) : "--");
    row("Satellites", d.gps.satellites);
    row("HDOP", d.gps.hdop.toFixed(1));
  }
  sec("Data sources");
  row("GPS", health.gps_source === "SimulatedGPS" ? "Simulated" : health.gps_source === "NoFixGPS" ? "Not connected" : "Serial NMEA");
  row("Engine data", health.sensors === "n2k" ? (health.engine_heard ? "NMEA 2000, receiving" : "NMEA 2000, nothing heard") : health.sensors === "real" ? "Analog senders on the Pi" : "Simulated");
  row("Stereo", health.stereo === "simulated" ? "Simulated" : health.stereo === "connected" ? "Fusion on NMEA 2000" : "Not found");
  row("NMEA 2000 interface", health.can_channel || "None");
  row("Lights", health.led_driver === "pwm" ? "12 V PWM" : health.led_driver === "ws281x" ? "Addressable" : "Preview only");
  sec("Charts");
  row("Sources", "USACE IENC");
  row("Mode", chartColorMode === "night" ? "Night" : chartColorMode === "dusk" ? "Dusk" : "Day");
  $("infoList").innerHTML = rows.join("");
}

// ---------- Menu bar ----------
function markHere() {
  if (lastData && lastData.gps.has_fix) {
    setWaypoint(lastData.gps.lat, lastData.gps.lon).then(() => openPanel("waypoint"));
  } else {
    openPanel("alerts");
  }
}

function syncNightButton() {
  $("nightLabel").textContent = nightMode ? "Day" : "Night";
}
document.addEventListener("chartmode", syncNightButton);

document.querySelectorAll("#menubar [data-act]").forEach((b) => b.addEventListener("click", () => {
  const act = b.dataset.act;
  if (act === "home") toggleHome();
  else if (act === "prev") stepPinned(-1);
  else if (act === "next") stepPinned(1);
  else if (act === "mark") markHere();
  else if (act === "night") setChartMode(nightMode ? "day" : "night");
  else openPanel(act);
}));

// ---------- Live link ----------
const banner = document.createElement("div");
banner.id = "linkBanner";
banner.textContent = "Reconnecting to the boat computer…";
banner.hidden = true;
$("stage").appendChild(banner);

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/telemetry`);
  ws.onopen = () => {
    banner.hidden = true;
    alarmRev = -1;  // a restarted server counts its settings revisions from the start again, so take whatever it says first
  };
  ws.onclose = () => {
    banner.hidden = false;
    setTimeout(connect, 1500);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    if (data.type === "fast") {
      renderFast(data);
      updateAlerts({ engine: data.engine, boat_info: data.boat_info });
    } else {
      renderAll(data);
      updateAlerts(data);
    }
  };
}

// ---------- Start ----------
mountWidgets();
setUnit(unit);          // builds the speed dials, unit labels and trip history
syncNightButton();
let startScreen = "helm";
try { startScreen = localStorage.getItem("screen") || "helm"; } catch (e) { /* storage unavailable */ }
showScreen(SCREENS[startScreen] ? startScreen : "helm");
connect();
