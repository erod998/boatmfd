// A chart app of its own in the chart's place (BOAT_CHART_APP; kiosk/chart-app.py moves its
// window). Only the helm display's own page does this -- a tablet on the WiFi showing the
// dashboard mustn't move the helm's window -- and only when the server says there's an app.
//
// Four times a second: where the chart is on the screen, less whatever of ours has to stay in
// sight over it (the data boxes, an open side panel, the alarm banner, the notices at the top),
// in screen pixels; nothing while the Home overlay is up or no screen with the chart is showing.
(function chartApp() {
  if (!/[?&]kiosk\b/.test(location.search)) return;
  const KEEP = ["#alarmBanner", "#linkBanner", "#soundHint", ".panel", ".map-boxes"];
  let sent = "";

  const area = (r) => Math.max(0, r.right - r.left) * Math.max(0, r.bottom - r.top);

  // The biggest part of the rectangle left clear of one thing over it: cut off above, below,
  // left or right of it, whichever keeps the most.
  function carve(r, o) {
    if (o.right <= r.left || o.left >= r.right || o.bottom <= r.top || o.top >= r.bottom) return r;
    const cuts = [{ ...r, top: o.bottom }, { ...r, bottom: o.top }, { ...r, left: o.right }, { ...r, right: o.left }];
    return cuts.reduce((a, b) => (area(b) > area(a) ? b : a));
  }

  function wanted() {
    const map = document.getElementById("map");
    const home = document.getElementById("home");
    if (!map || !map.offsetParent || (home && !home.hidden)) return { visible: false };
    const m = map.getBoundingClientRect();
    let r = { left: m.left, top: m.top, right: m.right, bottom: m.bottom };
    for (const sel of KEEP) {
      for (const el of document.querySelectorAll(sel)) {
        if (el.hidden || !el.offsetParent || !el.childElementCount && el.classList.contains("map-boxes")) continue;
        const o = el.getBoundingClientRect();
        if (o.width && o.height) r = carve(r, { left: o.left - 3, top: o.top - 3, right: o.right + 3, bottom: o.bottom + 3 });
      }
    }
    if (area(r) < 200 * 150) return { visible: false };
    const k = window.devicePixelRatio || 1;
    return {
      visible: true,
      x: Math.round((window.screenX + r.left) * k), y: Math.round((window.screenY + r.top) * k),
      w: Math.round((r.right - r.left) * k), h: Math.round((r.bottom - r.top) * k),
    };
  }

  function report(rect) {
    const body = JSON.stringify(rect);
    if (body === sent) return;
    sent = body;
    fetch("/api/chart-app/rect", { method: "POST", headers: { "Content-Type": "application/json" }, body })
      .catch(() => { sent = ""; });   // the server restarting: say it again next time
  }

  fetch("/api/chart-app").then((r) => r.json()).then((cfg) => {
    if (!cfg.enabled) return;
    document.documentElement.classList.add("chart-app");
    setInterval(() => report(wanted()), 250);
    // The page going away (a reload): the window gets out of the way until it's back.
    window.addEventListener("pagehide", () =>
      navigator.sendBeacon("/api/chart-app/rect", new Blob([JSON.stringify({ visible: false })], { type: "application/json" })));
  }).catch(() => {});
})();
