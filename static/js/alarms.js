"use strict";
// Alarms: the settings and state the server sends, the red/amber bands on the gauges, hold-a-gauge-to-open-its-menu,
// the alarm menu itself, the banner and the sound. The server decides when an alarm fires (see app/alarms.py);
// this file only shows it and lets the driver change the levels.

let alarmCfg = {};          // id -> { enabled, level, warn, side }, from the telemetry frames
let alarmRev = -1;          // revision of those settings
let alarmActive = [];       // alarms and warnings right now: { id, group, severity, acked, message, ... }
let alarmSoundOn = true;
let alarmDefs = null;       // the full definitions, loaded when the menu opens
let alarmMenuRev = -1;      // settings revision the open menu reflects
let alarmMenuGroup = null;  // which gauge's menu is open (null = every alarm)
let alarmControls = {};     // id -> the menu's level control

const ALARM_GROUPS = {
  coolant: { title: "Coolant / engine temp alarm", ids: ["coolant"] },
  oil: { title: "Oil pressure alarm", ids: ["oil"] },
  battery: { title: "Battery voltage alarms", ids: ["battery_low", "battery_high"] },
  fuel: { title: "Fuel level alarm", ids: ["fuel"] },
  depth: { title: "Depth alarm", ids: ["depth"] },
};

// the gauges' scales, for painting their red/amber bands where the alarms are set
const ALARM_SCALES = { coolant: [100, 260], oil: [0, 80], battery: [10, 16], fuel: [0, 100] };

const ALARM_VALUE = {
  coolant: () => latestReadings.engine.coolant_f,
  oil: () => latestReadings.engine.oil_pressure_psi,
  battery_low: () => latestReadings.boat.battery_voltage,
  battery_high: () => latestReadings.boat.battery_voltage,
  fuel: () => latestReadings.engine.fuel_pct,
  depth: () => latestReadings.boat.depth_ft,
};

const alarmFmt = (def, v) => (v == null ? "--" : Number(v).toFixed(def.decimals) + (def.unit === "°F" || def.unit === "%" ? def.unit : ` ${def.unit}`));

// ---------- Gauge bands follow the alarm levels ----------
function alarmBands(group) {
  const [min, max] = ALARM_SCALES[group];
  const bands = [];
  ALARM_GROUPS[group].ids.forEach((id) => {
    const a = alarmCfg[id];
    if (!a || !a.enabled) return;
    if (a.side === "high") bands.push({ from: a.level - a.warn, to: a.level, level: "amber" }, { from: a.level, to: max, level: "red" });
    else bands.push({ from: min, to: a.level, level: "red" }, { from: a.level, to: a.level + a.warn, level: "amber" });
  });
  return bands;
}

function applyGaugeBands() {
  const gauges = { coolant: ["cool", dials.cool], oil: ["oil", dials.oil], battery: ["batt", dials.batt], fuel: ["fuel", dials.fuel] };
  Object.entries(gauges).forEach(([group, [tileId, dial]]) => {
    const bands = alarmBands(group);
    dial.setZones(bands);
    tiles[tileId].spec.zones = bands;
    tiles[tileId].bar.setZones(bands);
  });
}

function applyAlarmSettings(cfg, rev, sound) {
  alarmCfg = cfg;
  alarmRev = rev;
  alarmSoundOn = sound;
  applyGaugeBands();
  refreshBells();
}

// called with every full telemetry frame
function applyAlarmConfig(data) {
  if (!data.alarm_cfg || data.alarm_rev < alarmRev) return;  // older than what we already know (a frame that was in flight)
  alarmSoundOn = data.alarm_sound;
  if (data.alarm_rev !== alarmRev) {
    applyAlarmSettings(data.alarm_cfg, data.alarm_rev, data.alarm_sound);
    // changed on another screen (a phone, say): bring an open menu up to date
    if (!$("panel-alarm").hidden && data.alarm_rev > alarmMenuRev) reloadAlarmMenu();
  }
}

// called with every frame, fast or full
function applyAlarms(list) {
  alarmActive = list || [];
  const worst = {};
  alarmActive.forEach((a) => { if (!worst[a.group] || a.severity === "alarm") worst[a.group] = a; });
  document.querySelectorAll("[data-hold]").forEach((el) => {
    const a = worst[el.dataset.hold];
    const state = !a ? "" : a.severity === "alarm" ? (a.acked ? "acked" : "alarm") : "warn";
    if (el.dataset.alarm !== state) el.dataset.alarm = state;
  });
  updateBanner();
  updateMenuLive();
  updateGaugeMenuLive();
}

const unackedAlarms = () => alarmActive.filter((a) => a.severity === "alarm" && !a.acked);

// ---------- Banner and sound ----------
// Navigation alerts (Arrival, Off Course, Anchor Drag, GPS Accuracy, boundary crossings --
// app/nav_alarms.py and app/boundaries.py) have no "acked" concept of their own: they clear when
// their own condition does (the boat is back on course, the anchor stops dragging, ...), not by
// tapping Silence, so they're folded into the banner but not into unackedAlarms()/Silence, which
// stay scoped to the engine/boat threshold alarms as before.
function updateBanner() {
  const unacked = unackedAlarms();
  const navAlarms = ((lastData && lastData.nav_alerts) || []).filter((a) => a.severity === "alarm");
  const showing = [...navAlarms, ...unacked];  // nav alerts lead, since Silence can't dismiss them
  const banner = $("alarmBanner");
  banner.hidden = !showing.length;
  if (showing.length) {
    const text = showing[0].message || showing[0].text;
    $("alarmText").textContent = text + (showing.length > 1 ? `   +${showing.length - 1} more` : "");
  }
  alarmAudio.set(showing.length > 0 && alarmSoundOn);
}

$("alarmSilence").addEventListener("click", async () => {
  alarmActive = alarmActive.map((a) => (a.severity === "alarm" ? { ...a, acked: true } : a));  // silent at once, the server confirms
  applyAlarms(alarmActive);
  updateAlerts(lastData);
  try { await postJson("/api/alarms/ack", {}); } catch (e) { /* the next frame corrects it */ }
});

// A double beep once a second while an alarm is unacknowledged. Browsers only allow sound after the person has touched
// the page once, so the first touch anywhere wakes the audio; the kiosk command in the README also lifts that rule.
const alarmAudio = (() => {
  let ctx = null;
  let timer = null;
  let lastError = null;
  const unlock = () => {
    try {
      if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
    } catch (e) { lastError = String(e); /* no audio available: the banner still works */ }
  };
  ["pointerdown", "keydown", "touchstart"].forEach((ev) => addEventListener(ev, unlock, { passive: true }));
  const beep = (delay = 0, freq = 880) => {
    if (!ctx || ctx.state !== "running") return;
    const t = ctx.currentTime + delay;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.35, t + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.2);
  };
  // Two alternating tones read as more "alarm" and less "text message" than one pitch repeated.
  const pattern = () => { beep(0, 880); beep(0.26, 660); };
  return {
    unlock,
    unlocked: () => !!ctx && ctx.state === "running",
    test() {
      unlock();
      // resume() is async: firing the pattern in the same tick as a context that was still
      // suspended can lose the very first test to the race (beep() sees "suspended" and silently
      // no-ops). Wait for the real resume to land before the one-shot test, so it's never silent
      // on what's usually someone's very first attempt.
      if (ctx && ctx.state !== "running") ctx.resume().then(pattern, pattern);
      else pattern();
    },
    playing: () => !!timer,
    set(on) {
      if (on && !timer) { pattern(); timer = setInterval(pattern, 1000); }
      else if (!on && timer) { clearInterval(timer); timer = null; }
    },
    debug() {
      return {
        supported: !!(window.AudioContext || window.webkitAudioContext),
        contextCreated: !!ctx,
        state: ctx ? ctx.state : "no context yet",
        sampleRate: ctx ? ctx.sampleRate : null,
        lastError,
      };
    },
  };
})();

// iOS/Safari (and any browser not launched with the kiosk --autoplay-policy flag below) blocks
// all Web Audio output until the page has been touched at least once. A Pi running its own kiosk
// Chromium gets that flag and never hits this; a phone or tablet loading the dashboard over WiFi
// doesn't, and would otherwise sit through a real alarm in total silence with no clue why. This
// hint shows once per page load if sound is still locked a couple seconds in, and disappears the
// instant any tap/click/key does the unlocking (same gesture list as unlock() itself, above).
const soundHint = document.createElement("div");
soundHint.id = "soundHint";
soundHint.textContent = "Tap anywhere to enable alarm sound";
soundHint.hidden = true;
$("stage").appendChild(soundHint);
setTimeout(() => { if (!alarmAudio.unlocked()) soundHint.hidden = false; }, 1500);
["pointerdown", "keydown", "touchstart"].forEach((ev) =>
  addEventListener(ev, () => (soundHint.hidden = true), { passive: true, once: true }));

// ---------- Hold a gauge to open a menu (the alarm menu, or the gauge-display menu below) ----------
// attachHold() itself lives in app.js now (loaded before this file): the overlay boxes there need
// it at script-load time, before alarms.js has even been fetched.
const BELL = '<svg viewBox="0 0 24 24"><path d="M12 22a2 2 0 0 0 2-2h-4a2 2 0 0 0 2 2zm6-6V11c0-3.1-1.6-5.6-4.5-6.3V4a1.5 1.5 0 0 0-3 0v.7C7.6 5.4 6 7.9 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>';

/** Marks el as an alarm target: gets the bell icon and the red/amber border driven by app/alarms.py. */
function attachAlarmHold(el, group) {
  if (!el || el.dataset.hold) return;
  el.dataset.hold = group;
  const bell = document.createElement("span");
  bell.className = "bell";
  bell.hidden = true;
  bell.innerHTML = BELL;
  el.appendChild(bell);
  attachHold(el, () => openAlarmMenu(group));
}

function refreshBells() {
  document.querySelectorAll("[data-hold]").forEach((el) => {
    const on = ALARM_GROUPS[el.dataset.hold].ids.some((id) => alarmCfg[id] && alarmCfg[id].enabled);
    const bell = el.querySelector(":scope > .bell");
    if (bell) bell.hidden = !on;
  });
}

// ---------- The alarm menu ----------
async function loadAlarmDefs() {
  alarmDefs = await (await fetch("/api/alarms")).json();
  alarmMenuRev = alarmDefs.revision;
}

async function openAlarmMenu(group) {
  alarmMenuGroup = group;
  try { await loadAlarmDefs(); } catch (e) { return; }
  buildAlarmMenu();
  openPanel("alarm");
}

async function reloadAlarmMenu() {
  try { await loadAlarmDefs(); buildAlarmMenu(); } catch (e) { /* try again on the next change */ }
}

async function saveAlarm(id, body) {
  try {
    const res = await (await postJson(`/api/alarms/${id}`, body)).json();
    if (res.alarms) {
      alarmDefs = res;
      alarmMenuRev = res.revision;
      const cfg = {};
      res.alarms.forEach((a) => { cfg[a.id] = { enabled: a.enabled, level: a.level, warn: a.warn, side: a.side }; });
      applyAlarmSettings(cfg, res.revision, res.sound);
    }
    return res;
  } catch (e) { return null; }
}

function alarmHint(def) {
  const dir = def.side === "high" ? "above" : "below";
  const warn = def.side === "high" ? def.level - def.warn : def.level + def.warn;
  return `Sounds when the reading stays ${dir} ${alarmFmt(def, def.level)} for ${def.delay} seconds. An amber warning shows from ${alarmFmt(def, warn)}.`;
}

function buildAlarmMenu() {
  const body = $("alarmBody");
  body.replaceChildren();
  alarmControls = {};
  $("alarmTitle").textContent = alarmMenuGroup ? ALARM_GROUPS[alarmMenuGroup].title : "Alarms";
  const groups = alarmMenuGroup ? [alarmMenuGroup] : Object.keys(ALARM_GROUPS);
  groups.forEach((g) => ALARM_GROUPS[g].ids.forEach((id) => {
    const def = alarmDefs.alarms.find((a) => a.id === id);
    if (!def) return;
    const block = document.createElement("div");
    block.className = "al-block";
    block.dataset.id = id;
    block.innerHTML = `<div class="al-head"><span class="al-name"></span><span class="al-state"></span></div>` +
      `<div class="al-now">Now <b class="al-val">--</b></div>` +
      `<button class="btn al-toggle"></button>` +
      `<div class="al-cap"></div>${levelMarkup("Lower", "Higher")}` +
      `<p class="al-hint"></p><button class="btn al-reset"></button>`;
    body.appendChild(block);
    const q = (s) => block.querySelector(s);
    q(".al-name").textContent = def.label;
    q(".al-cap").textContent = def.side === "high" ? "Alarm when it rises to" : "Alarm when it falls to";
    const control = levelControl(q(".level-ctl"), {
      min: def.min, getMax: () => def.max, step: def.step,
      text: (v) => alarmFmt(def, v),
      onChange: async (v) => {
        const res = await saveAlarm(id, { level: v });
        if (res && !res.ok) control.set(alarmDefs.alarms.find((a) => a.id === id).level, true);
        refreshAlarmBlock(id);
      },
    });
    control.set(def.level, true);
    alarmControls[id] = control;
    q(".al-toggle").addEventListener("click", async () => {
      const cur = alarmDefs.alarms.find((a) => a.id === id);
      await saveAlarm(id, { enabled: !cur.enabled });
      refreshAlarmBlock(id);
    });
    q(".al-reset").addEventListener("click", async () => {
      await saveAlarm(id, { level: def.default, enabled: def.default_enabled });
      control.set(def.default, true);
      refreshAlarmBlock(id);
    });
    refreshAlarmBlock(id);
  }));

  const sound = document.createElement("div");
  sound.className = "al-block al-sound";
  sound.innerHTML = '<div class="al-head"><span class="al-name">Alarm sound</span></div><div class="al-row"><button class="btn al-sound-toggle"></button><button class="btn al-test">Test sound</button></div>';
  body.appendChild(sound);
  sound.querySelector(".al-sound-toggle").addEventListener("click", async () => {
    const res = await (await postJson("/api/alarms/sound", { on: !alarmDefs.sound })).json();
    alarmDefs = res;
    alarmMenuRev = res.revision;
    alarmSoundOn = res.sound;
    refreshSoundButton();
    updateBanner();
  });
  sound.querySelector(".al-test").addEventListener("click", () => {
    alarmAudio.test();
    // A device that plays nothing gives no error to look at -- surface what the Web Audio API
    // itself thinks is going on, since there's no way to plug this screen into a debugger.
    setTimeout(() => {
      const d = alarmAudio.debug();
      alert(`Didn't hear it? Audio check:\nsupported: ${d.supported}\ncontext created: ${d.contextCreated}\nstate: ${d.state}\nsample rate: ${d.sampleRate}\nlast error: ${d.lastError || "none"}\n\nAlso check: volume up, Control Center not muted, silent switch (if present) off.`);
    }, 400);
  });
  refreshSoundButton();
}

function refreshSoundButton() {
  const b = document.querySelector(".al-sound-toggle");
  if (!b || !alarmDefs) return;
  b.textContent = alarmDefs.sound ? "Sound is ON" : "Sound is OFF";
  b.classList.toggle("primary", alarmDefs.sound);
}

function refreshAlarmBlock(id) {
  const block = document.querySelector(`.al-block[data-id="${id}"]`);
  const def = alarmDefs && alarmDefs.alarms.find((a) => a.id === id);
  if (!block || !def) return;
  const toggle = block.querySelector(".al-toggle");
  toggle.textContent = def.enabled ? "Alarm is ON" : "Alarm is OFF";
  toggle.classList.toggle("primary", def.enabled);
  block.classList.toggle("off", !def.enabled);
  block.querySelector(".al-hint").textContent = alarmHint(def);
  block.querySelector(".al-reset").textContent = `Reset to ${alarmFmt(def, def.default)}, ${def.default_enabled ? "on" : "off"}`;
}

// the live reading and state, on every frame while the menu is open
function updateMenuLive() {
  if ($("panel-alarm").hidden || !alarmDefs) return;
  Object.keys(alarmControls).forEach((id) => {
    const block = document.querySelector(`.al-block[data-id="${id}"]`);
    const def = alarmDefs.alarms.find((a) => a.id === id);
    if (!block || !def) return;
    block.querySelector(".al-val").textContent = alarmFmt(def, ALARM_VALUE[id]());
    const active = alarmActive.find((a) => a.id === id);
    const state = block.querySelector(".al-state");
    state.textContent = !def.enabled ? "" : active ? (active.severity === "alarm" ? "ALARM" : "Warning") : "OK";
    state.dataset.state = !def.enabled ? "" : active ? active.severity : "ok";
  });
}

// ---------- Hook the gauges up ----------
(function attachAlarmTargets() {
  const tileGroups = { cool: "coolant", oil: "oil", batt: "battery", fuel: "fuel" };
  Object.entries(tileGroups).forEach(([id, group]) => attachAlarmHold(tiles[id].tile, group));
  [["dialCool", "coolant"], ["dialOil", "oil"], ["dialBatt", "battery"], ["dialFuel", "fuel"]].forEach(([id, group]) => attachAlarmHold($(id).closest(".gd"), group));
  document.querySelectorAll('[data-bind="depth"]').forEach((b) => attachAlarmHold(b.closest(".gbox"), "depth"));
  document.querySelectorAll('[data-bind="fuelPct"]').forEach((b) => attachAlarmHold(b.closest(".gbox"), "fuel"));
})();

// ---------- Gauge display settings (hold the RPM gauge): how quickly its needle follows a new reading ----------
// A per-browser display preference (localStorage), not shared like the alarms: it doesn't need every screen
// to agree, and it makes sense for a helm display and a nav-station display to feel different.
const GAUGE_LEVELS = {
  rpm: [
    { v: 1, omega: 2.5, label: "Very smooth" },
    { v: 2, omega: 4, label: "Smooth" },
    { v: 3, omega: 6, label: "Normal" },
    { v: 4, omega: 8, label: "Responsive" },
    { v: 5, omega: 10, label: "Snappy (default)" },
  ],
};
const GAUGE_DEFAULT_LEVEL = { rpm: 5 };
const GAUGE_TITLES = { rpm: "Engine RPM display" };
const GAUGE_TARGETS = { rpm: () => [dials.rpm, dials.helmRpm] };
const GAUGE_UNIT = { rpm: "rpm" };
const GAUGE_VALUE = { rpm: () => latestReadings.engine.rpm };

function gaugeLevel(group) {
  let v = GAUGE_DEFAULT_LEVEL[group];
  try {
    const saved = Number(localStorage.getItem("gaugeLevel:" + group));
    if (GAUGE_LEVELS[group].some((l) => l.v === saved)) v = saved;
  } catch (e) { /* storage unavailable */ }
  return v;
}
function gaugeOmega(group) {
  return GAUGE_LEVELS[group].find((l) => l.v === gaugeLevel(group)).omega;
}
function applyGaugeLevel(group, v) {
  const omega = GAUGE_LEVELS[group].find((l) => l.v === v).omega;
  (GAUGE_TARGETS[group]() || []).forEach((d) => d && d.setOmega && d.setOmega(omega));
}
function setGaugeLevel(group, v) {
  try { localStorage.setItem("gaugeLevel:" + group, v); } catch (e) { /* storage unavailable */ }
  applyGaugeLevel(group, v);
}

function openGaugeMenu(group) {
  buildGaugeMenu(group);
  openPanel("gauge");
}

let gaugeMenuGroup = null;
function buildGaugeMenu(group) {
  gaugeMenuGroup = group;
  const defs = GAUGE_LEVELS[group];
  const label = (v) => defs.find((l) => l.v === Math.round(v)).label;
  const body = $("gaugeBody");
  body.replaceChildren();
  $("gaugeTitle").textContent = GAUGE_TITLES[group];
  const block = document.createElement("div");
  block.className = "al-block";
  block.innerHTML = "<div class=\"al-head\"><span class=\"al-name\">Needle response</span></div>" +
    "<div class=\"al-now\">Now <b class=\"gg-val\">--</b> " + GAUGE_UNIT[group] + "</div>" +
    "<div class=\"al-cap\">How quickly the needle follows a new reading</div>" + levelMarkup("Smoother", "Snappier") +
    "<p class=\"al-hint\">A snappier needle reacts fast to a real change but jitters with a noisy signal; a smoother one settles down but lags a beat behind quick changes.</p>" +
    "<button class=\"btn al-reset\"></button>";
  body.appendChild(block);
  const control = levelControl(block.querySelector(".level-ctl"), {
    min: defs[0].v, getMax: () => defs[defs.length - 1].v, step: 1,
    text: label,
    onChange: (v) => setGaugeLevel(group, Math.round(v)),
  });
  control.set(gaugeLevel(group), true);
  block.querySelector(".al-reset").textContent = "Reset to " + label(GAUGE_DEFAULT_LEVEL[group]);
  block.querySelector(".al-reset").addEventListener("click", () => {
    setGaugeLevel(group, GAUGE_DEFAULT_LEVEL[group]);
    control.set(GAUGE_DEFAULT_LEVEL[group], true);
  });
}

// the live reading, while the gauge menu is open (piggybacks on the same per-frame hook as the alarm menu, below)
function updateGaugeMenuLive() {
  if ($("panel-gauge").hidden || !gaugeMenuGroup) return;
  const val = document.querySelector("#gaugeBody .gg-val");
  if (val) val.textContent = fmtNum(GAUGE_VALUE[gaugeMenuGroup](), 0);
}

Object.keys(GAUGE_LEVELS).forEach((group) => applyGaugeLevel(group, gaugeLevel(group)));
attachHold($("dialRpm").closest(".gd"), () => openGaugeMenu("rpm"));
attachHold($("dialHelmRpm").closest(".dial-pane"), () => openGaugeMenu("rpm"));
