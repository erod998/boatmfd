"use strict";
// Garmin-style dials: a bezel around a segmented scale ring that fills with a blue gradient up to the
// value, a thin white needle tick, red/amber zone segments, numerals inside the ring, and a big
// digital readout with the unit stacked beside it.
const SVGNS = "http://www.w3.org/2000/svg";
const DIAL_START = -135;  // degrees from 12 o'clock
const DIAL_SWEEP = 270;

const ZONE_COLORS = {
  red: { off: "#96121a", on: "#ff2b35", text: "#ff5b63" },
  amber: { off: "#7d5c0e", on: "#ffb31f", text: "#ffb31f" },
};
const SEG_OFF = "#26282b";

function svgNode(tag, attrs, parent) {
  const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}

function polar(r, deg) {
  const a = ((deg - 90) * Math.PI) / 180;
  return [100 + r * Math.cos(a), 100 + r * Math.sin(a)];
}

function arcPath(r, a0, a1) {
  const [x0, y0] = polar(r, a0);
  const [x1, y1] = polar(r, a1);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

// ---------- Colours (a segment blends between its "off" and "on" colours, so the leading edge fills gradually) ----------
function hslToRgb(h, s, l) {
  s /= 100; l /= 100;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [f(0) * 255, f(8) * 255, f(4) * 255];
}
const hexToRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

// deep blue at the low end of the ring, sky blue at the high end
const fillRgb = (t) => hslToRgb(228 - 28 * t, 88, 40 + 27 * t);
const rgbCss = (c) => `rgb(${c.map(Math.round).join(",")})`;
const mixRgb = (a, b, t) => rgbCss(a.map((v, i) => v + (b[i] - v) * t));

// ---------- Animation: one loop for every gauge ----------
// Each animated value follows its target on a critically damped spring: it starts and stops gently,
// never overshoots, and keeps moving between readings, which only arrive a few times a second.
class Spring {
  constructor(omega, eps) {
    this.w = omega;
    this.eps = eps;
    this.x = null;       // what is shown
    this.v = 0;          // how fast it is moving
    this.target = null;
  }
  set(target) {
    this.target = target == null || Number.isNaN(Number(target)) ? null : Number(target);
    if (this.target == null) { this.x = null; this.v = 0; }
    else if (this.x == null) { this.x = this.target; this.v = 0; }  // first reading: appear in place, don't sweep in from nowhere
  }
  snap() { if (this.target != null) { this.x = this.target; this.v = 0; } }
  get settled() { return this.target == null || (Math.abs(this.x - this.target) < this.eps && Math.abs(this.v) < this.eps * 4); }
  step(dt) {
    if (this.target == null) return;
    const d = this.x - this.target;
    const e = Math.exp(-this.w * dt);
    const t = this.v + this.w * d;
    this.x = this.target + (d + t * dt) * e;
    this.v = (this.v - t * this.w * dt) * e;
    if (this.settled) this.snap();
  }
}

const animating = new Set();
const allGauges = new Set();
let animFrame = 0;
let animLast = 0;

// Whether each gauge is on a screen that's showing, kept up to date by the browser. Each gauge used
// to ask for itself (getClientRects) inside its animation step, between one gauge's DOM writes and
// the next's -- and every such question forced a layout of the whole page, once per gauge per frame.
// On the Pi that was nearly all of Chromium's CPU. An IntersectionObserver answers without any.
const gaugeVisibility = new IntersectionObserver((entries) => entries.forEach((e) => {
  const gauge = e.target._gauge;
  if (!gauge) return;
  gauge.shown = e.isIntersecting;
  if (gauge.shown && gauge.stale) wake(gauge);   // changed while hidden: redraw now it's back
}));
function watchVisibility(el, gauge) {
  // A gauge rebuilt in place (a units change) keeps what the observer last said about its element,
  // which it won't repeat; a new one counts as shown until the first report, a moment after this.
  gauge.shown = el._gauge ? el._gauge.shown : true;
  el._gauge = gauge;
  gaugeVisibility.observe(el);
}

function wake(gauge) {
  animating.add(gauge);
  if (!animFrame) {
    animLast = performance.now();
    animFrame = requestAnimationFrame(animTick);
  }
}

function animTick(now) {
  const dt = Math.min(0.05, (now - animLast) / 1000);  // a stalled frame doesn't make a needle lurch
  animLast = now;
  animating.forEach((g) => { if (!g.step(dt)) animating.delete(g); });
  animFrame = animating.size ? requestAnimationFrame(animTick) : 0;
}

/** Call when a screen becomes visible: gauges that changed while it was hidden redraw now. */
function refreshGauges() {
  allGauges.forEach((g) => { if (g.stale) wake(g); });
}

// simple line icons, drawn around (0,0)
const DIAL_ICONS = {
  oil: (g) => svgNode("path", { d: "M0,-9 C4,-3 7,0 7,4 A7,7 0 0 1 -7,4 C-7,0 -4,-3 0,-9 Z" }, g),
  temp: (g) => {
    svgNode("path", { d: "M-2.5,-9 a2.5,2.5 0 0 1 5,0 v9 a5,5 0 1 1 -5,0 z" }, g);
    svgNode("path", { d: "M8,-6 h4 M8,-1 h4 M8,4 h4" }, g);
  },
  water: (g) => {
    svgNode("path", { d: "M-9,2 q3,-3 6,0 t6,0 t6,0" }, g);
    svgNode("path", { d: "M-9,8 q3,-3 6,0 t6,0 t6,0" }, g);
    svgNode("path", { d: "M-3,-9 v8 a3,3 0 1 0 3,0 v-8 z" }, g);
  },
  fuel: (g) => {
    svgNode("rect", { x: -6, y: -9, width: 9, height: 17, rx: 1.5 }, g);
    svgNode("path", { d: "M3,-3 h3 l3,4 v7" }, g);
    svgNode("path", { d: "M-3.5,-5.5 h4" }, g);
  },
  battery: (g) => {
    svgNode("rect", { x: -9, y: -5, width: 18, height: 12, rx: 1.5 }, g);
    svgNode("path", { d: "M-5,-8 v3 M5,-8 v3 M-5,1 h4 M3,1 h4 M5,-1 v4" }, g);
  },
  trim: (g) => {
    svgNode("path", { d: "M-9,7 h13" }, g);
    svgNode("path", { d: "M-9,7 L6,-7" }, g);
    svgNode("path", { d: "M2,-9 l6,2 l-2,6" }, g);
  },
};

let dialCounter = 0;

/** The segments shared by the round dials and the flat bars: each knows its colours and how far along it is filled. */
function makeSegments(count, min, max, zones) {
  const segs = [];
  const width = (max - min) / count;
  for (let i = 0; i < count; i++) {
    segs.push({ lo: min + i * width, width, mid: min + (i + 0.5) * width, t: i / Math.max(1, count - 1), level: null, key: null });
  }
  const setZones = (list) => {
    segs.forEach((s) => {
      const zone = (list || []).find((z) => s.mid >= z.from && s.mid <= z.to);
      s.level = zone ? zone.level : null;
      s.off = hexToRgb(s.level ? ZONE_COLORS[s.level].off : SEG_OFF);
      s.on = s.level ? hexToRgb(ZONE_COLORS[s.level].on) : fillRgb(s.t);
      s.offCss = rgbCss(s.off);
      s.key = null;  // repaint
    });
  };
  setZones(zones);
  // Paint every segment for the value v (null = empty); only segments whose look changed are touched.
  const paint = (v, apply) => {
    segs.forEach((s) => {
      const fill = v == null ? 0 : Math.max(0, Math.min(1, (v - s.lo) / s.width));
      const q = Math.round(fill * 8);  // eighths are plenty for a smooth leading edge
      if (q === s.key) return;
      s.key = q;
      apply(s, q === 0 ? s.offCss : mixRgb(s.off, s.on, q / 8));
    });
  };
  return { segs, setZones, paint };
}

/**
 * cfg: min, max, segments, zones [{from,to,level:"red"|"amber"}], size "big"|"compact"|"small",
 *      step (numbered ticks), decimals, unit, label, needle "tick"|"pointer", icon, endLabels [min,max],
 *      numbers (bool), omega (how quickly the needle follows: higher is snappier)
 */
function makeDial(svg, cfg) {
  const c = Object.assign({ size: "big", segments: 28, decimals: 0, needle: "tick", zones: [], unit: "", label: "", omega: 8, numbers: cfg.size === "big" }, cfg);
  const id = `dial${dialCounter++}`;
  if (svg._dial) { animating.delete(svg._dial); allGauges.delete(svg._dial); }  // rebuilding (e.g. after a units change): retire the old dial
  svg.setAttribute("viewBox", "0 0 200 200");
  svg.replaceChildren();

  const defs = svgNode("defs", {}, svg);
  const bezel = svgNode("radialGradient", { id: `${id}-bezel`, cx: "50%", cy: "30%", r: "80%" }, defs);
  svgNode("stop", { offset: "0%", "stop-color": "#3b3d41" }, bezel);
  svgNode("stop", { offset: "100%", "stop-color": "#0e0f10" }, bezel);
  svgNode("circle", { cx: 100, cy: 100, r: 98, fill: `url(#${id}-bezel)`, stroke: "#4a4c50", "stroke-width": 1.2 }, svg);
  svgNode("circle", { cx: 100, cy: 100, r: 92, fill: "#0b0c0d", stroke: "#202224", "stroke-width": 1.5 }, svg);

  const geo = {
    big: { rOut: 87, rIn: 71, numR: 59, numSize: 13, valSize: 34, valY: 126, labelY: 163, labelSize: 14 },
    compact: { rOut: 88, rIn: 66, numR: 0, numSize: 0, valSize: 46, valY: 122, labelY: 160, labelSize: 15 },
    small: { rOut: 90, rIn: 76, numR: 0, numSize: 0, valSize: 48, valY: 118, labelY: 0, labelSize: 0 },
  }[c.size];

  const n = c.segments;
  const segAngle = DIAL_SWEEP / n;
  const gap = Math.min(1.6, segAngle * 0.25);
  const rMid = (geo.rOut + geo.rIn) / 2;
  const ring = makeSegments(n, c.min, c.max, c.zones);
  ring.segs.forEach((s, i) => {
    const a0 = DIAL_START + i * segAngle + gap / 2;
    const a1 = DIAL_START + (i + 1) * segAngle - gap / 2;
    s.path = svgNode("path", { d: arcPath(rMid, a0, a1), fill: "none", "stroke-width": geo.rOut - geo.rIn, stroke: SEG_OFF }, svg);
  });

  if (c.numbers && c.step) {
    for (let v = c.min; v <= c.max + 1e-9; v += c.step) {
      const [x, y] = polar(geo.numR, DIAL_START + ((v - c.min) / (c.max - c.min)) * DIAL_SWEEP);
      const t = svgNode("text", { x, y, fill: "#d3d6da", "font-size": geo.numSize, "text-anchor": "middle", "dominant-baseline": "central" }, svg);
      t.textContent = c.numFmt ? c.numFmt(v) : v;
    }
  }

  const needle = svgNode("line", { stroke: "#fff", "stroke-width": c.needle === "pointer" ? 4 : 3.4, "stroke-linecap": "round" }, svg);
  const needleR = c.needle === "pointer" ? [geo.rIn - 20, geo.rOut + 1] : [geo.rIn - 3, geo.rOut + 3];

  const valueText = svgNode("text", { fill: "#fff", "font-weight": 500, "font-size": geo.valSize, "font-variant-numeric": "tabular-nums" }, svg);
  if (c.size === "small") {
    valueText.setAttribute("x", 100);
    valueText.setAttribute("y", geo.valY);
    valueText.setAttribute("text-anchor", "middle");
    const v = svgNode("tspan", {}, valueText);
    v.textContent = "--";
    const unitTspan = svgNode("tspan", { "font-size": 22, fill: "#aeb2b7", dx: 5 }, valueText);
    unitTspan.textContent = c.unit;
    valueText.valueSpan = v;
  } else {
    valueText.setAttribute("x", 132);
    valueText.setAttribute("y", geo.valY);
    valueText.setAttribute("text-anchor", "end");
    valueText.textContent = "--";
    [...c.unit].forEach((ch, i) => {
      const u = svgNode("text", { x: 136, y: geo.valY - 22 + i * 11, fill: "#aeb2b7", "font-size": 10, "text-anchor": "start" }, svg);
      u.textContent = ch;
    });
  }
  if (c.label && geo.labelY) {
    const l = svgNode("text", { x: 100, y: geo.labelY, fill: "#fff", "font-size": geo.labelSize, "text-anchor": "middle" }, svg);
    l.textContent = c.label;
  }
  if (c.size === "small") {
    if (c.icon && DIAL_ICONS[c.icon]) {
      const g = svgNode("g", { transform: "translate(100 154) scale(1.35)", fill: "none", stroke: "#c9ccd0", "stroke-width": 1.7, "stroke-linecap": "round", "stroke-linejoin": "round" }, svg);
      DIAL_ICONS[c.icon](g);
    }
    if (c.endLabels) {
      [[c.endLabels[0], 34, 172], [c.endLabels[1], 166, 172]].forEach(([text, x, y]) => {
        const t = svgNode("text", { x, y, fill: "#aeb2b7", "font-size": 17, "text-anchor": "middle" }, svg);
        t.textContent = text;
      });
    }
  }

  const fmt = (v) => (c.fmt ? c.fmt(v) : v.toFixed(c.decimals));
  const spring = new Spring(c.omega, (c.max - c.min) * 0.0006);
  let shownText = null;
  let shownColor = null;
  const dial = { spring, stale: false };

  function draw() {
    const v = spring.x;
    if (v == null) {
      needle.style.display = "none";
    } else {
      needle.style.display = "";
      const angle = DIAL_START + Math.max(0, Math.min(1, (v - c.min) / (c.max - c.min))) * DIAL_SWEEP;
      const [x1, y1] = polar(needleR[0], angle);
      const [x2, y2] = polar(needleR[1], angle);
      needle.setAttribute("x1", x1.toFixed(2));
      needle.setAttribute("y1", y1.toFixed(2));
      needle.setAttribute("x2", x2.toFixed(2));
      needle.setAttribute("y2", y2.toFixed(2));
    }
    // a pointer dial doesn't fill: only its red/amber bands show, dimly
    ring.paint(c.needle === "pointer" ? null : v, (s, color) => s.path.setAttribute("stroke", color));
    const target = spring.target;
    const zone = target == null ? null : c.zones.find((z) => target >= z.from && target <= z.to);
    const text = v == null ? "--" : fmt(v);
    const color = zone ? ZONE_COLORS[zone.level].text : target == null ? "#8b9096" : "#fff";
    const el = valueText.valueSpan || valueText;
    if (text !== shownText) { el.textContent = text; shownText = text; }
    if (color !== shownColor) { el.setAttribute("fill", color); shownColor = color; }
  }

  dial.update = (value) => {
    spring.set(value);
    dial.stale = true;
    wake(dial);
  };
  dial.setZones = (zones) => {
    c.zones = zones || [];
    ring.setZones(c.zones);
    dial.stale = true;
    wake(dial);
  };
  dial.setOmega = (omega) => { spring.w = omega; };  // how quickly the needle follows a new reading, from Options > (hold the gauge)
  dial.step = (dt) => {
    if (!dial.shown) {  // on a hidden screen: no animation, and it redraws when the screen is shown
      spring.snap();
      return false;
    }
    spring.step(dt);
    draw();
    dial.stale = !spring.settled;
    return !spring.settled;
  };

  draw();
  svg._dial = dial;
  allGauges.add(dial);
  watchVisibility(svg, dial);
  return dial;
}

/** A flat version of the dial ring: segments that fill blue up to the value, with red/amber zones. */
function makeSegBar(el, cfg) {
  const n = cfg.segments || 30;
  el.classList.add("sbar");
  el.replaceChildren();
  const ring = makeSegments(n, cfg.min, cfg.max, cfg.zones);
  ring.segs.forEach((s) => { s.cell = document.createElement("i"); el.appendChild(s.cell); });
  const spring = new Spring(cfg.omega || 7, (cfg.max - cfg.min) * 0.0006);
  const bar = { spring, stale: false };
  const draw = () => ring.paint(spring.x, (s, color) => (s.cell.style.background = color));
  bar.update = (value) => { spring.set(value); bar.stale = true; wake(bar); };
  bar.setZones = (zones) => { ring.setZones(zones); bar.stale = true; wake(bar); };
  bar.setOmega = (omega) => { spring.w = omega; };
  bar.step = (dt) => {
    if (!bar.shown) { spring.snap(); return false; }
    spring.step(dt);
    draw();
    bar.stale = !spring.settled;
    return !spring.settled;
  };
  allGauges.add(bar);
  watchVisibility(el, bar);
  draw();
  return bar;
}

/**
 * A number that glides to each new reading instead of jumping. elements() returns the DOM nodes to write into;
 * a missing reading shows "--" at once.
 */
function makeAnimatedText(elements, { decimals = 0, omega = 7 } = {}) {
  const spring = new Spring(omega, Math.pow(10, -decimals) * 0.4);
  let shown = null;
  const anim = { spring, decimals };
  const write = () => {
    const text = spring.x == null ? "--" : spring.x.toFixed(anim.decimals);
    if (text === shown) return;
    shown = text;
    elements().forEach((e) => (e.textContent = text));
  };
  anim.update = (value) => { spring.set(value); wake(anim); };
  anim.setOmega = (omega) => { spring.w = omega; };
  anim.step = (dt) => {
    spring.step(dt);
    write();
    return !spring.settled;
  };
  return anim;
}
