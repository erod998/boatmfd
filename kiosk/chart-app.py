#!/usr/bin/env python3
"""A chart app of its own (GPS Nautical Charts' Boating App) in the dashboard's chart area.

Runs in the kiosk session (labwc's autostart starts it when BOAT_CHART_APP names the app's
launcher in /var/lib/boatmfd/boat.env). It keeps the app running -- starting it again if it
exits -- and keeps its window over the dashboard's chart: the dashboard reports where its chart
is on the screen (POST /api/chart-app/rect), this asks for that every 0.2 s and moves the
window there, or off the screen while the chart isn't showing.

The app is an X11 program (Qt 5 with only its xcb plugin), so it runs under labwc's XWayland,
and the window is moved as any X client can move one: a configure request, which labwc carries
out. labwc's window rule for it (/etc/boatmfd/labwc/rc.xml) takes its title bar off and keeps it
above the dashboard's full-screen window.

    chart-app.py                  keep the app running and in place
    chart-app.py --list           the X windows there are, and their classes (to find the app's)
    chart-app.py --move x,y,w,h   move the app's window once
"""
import ctypes
import fnmatch
import json
import os
import subprocess
import sys
import time
import urllib.request

APP = os.environ.get("BOAT_CHART_APP", "")
CLASS = os.environ.get("BOAT_CHART_APP_CLASS", "boating*")       # the app's WM_CLASS, a glob
DASHBOARD = os.environ.get("BOAT_DASHBOARD_URL", "http://127.0.0.1:8090")
OFF = (-4000, 0)          # where the window waits while the chart isn't showing


class X:
    """Just enough Xlib, through ctypes: find a window by class and move it."""

    def __init__(self, display=None):
        self.x = ctypes.CDLL("libX11.so.6")
        x = self.x
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                 ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                                 ctypes.POINTER(ctypes.c_uint)]
        x.XGetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
        x.XGetGeometry.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)] + \
            [ctypes.POINTER(ctypes.c_int)] * 2 + [ctypes.POINTER(ctypes.c_uint)] * 4
        x.XMoveResizeWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint, ctypes.c_uint]
        x.XFree.argtypes = [ctypes.c_void_p]
        x.XFlush.argtypes = [ctypes.c_void_p]
        # An X error (a window gone between finding and moving it) is reported, not fatal.
        self._handler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda d, e: 0)
        x.XSetErrorHandler(self._handler)
        self.d = x.XOpenDisplay((display or os.environ.get("DISPLAY", ":0")).encode())
        if not self.d:
            raise OSError("can't open the X display")
        self.root = x.XDefaultRootWindow(self.d)

    def windows(self):
        """Top-level windows: (id, res_name, res_class, x, y, w, h)."""
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        kids, n = ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
        if not self.x.XQueryTree(self.d, self.root, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(kids),
                                 ctypes.byref(n)):
            return []
        out = []
        for k in range(n.value):
            win = kids[k]
            hint = (ctypes.c_char_p * 2)()
            name = cls = ""
            if self.x.XGetClassHint(self.d, win, hint):
                name, cls = (hint[0] or b"").decode(errors="replace"), (hint[1] or b"").decode(errors="replace")
            r, gx, gy = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int()
            gw, gh, bw, dp = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
            self.x.XGetGeometry(self.d, win, ctypes.byref(r), ctypes.byref(gx), ctypes.byref(gy), ctypes.byref(gw),
                                ctypes.byref(gh), ctypes.byref(bw), ctypes.byref(dp))
            out.append((win, name, cls, gx.value, gy.value, gw.value, gh.value))
        if n.value:
            self.x.XFree(kids)
        return out

    def find(self, pattern):
        """The app's main window: the biggest one whose class matches (Qt has small helper ones)."""
        hits = [w for w in self.windows() if fnmatch.fnmatch(w[1].lower(), pattern.lower())
                or fnmatch.fnmatch(w[2].lower(), pattern.lower())]
        hits = [w for w in hits if w[5] * w[6] > 10000]
        return max(hits, key=lambda w: w[5] * w[6]) if hits else None

    def move(self, win, x, y, w, h):
        self.x.XMoveResizeWindow(self.d, win, x, y, max(w, 1), max(h, 1))
        self.x.XFlush(self.d)


def wanted():
    """Where the dashboard wants the chart: (x, y, w, h), or None while it isn't showing."""
    with urllib.request.urlopen(DASHBOARD + "/api/chart-app/rect", timeout=2) as r:
        rect = json.load(r)
    if not rect.get("visible"):
        return None
    return tuple(int(round(rect[k])) for k in ("x", "y", "w", "h"))


def run():
    if not APP or not os.access(APP, os.X_OK):
        sys.exit(f"chart-app: BOAT_CHART_APP isn't an app to run: {APP!r}")
    x = X()
    proc, placed, last_seen = None, None, None
    while True:
        if proc is None or proc.poll() is not None:
            if proc is not None:
                time.sleep(2)            # it exited: start it again, not in a tight loop
            proc = subprocess.Popen([APP], cwd=os.path.dirname(APP), stdin=subprocess.DEVNULL)
            placed = None
        try:
            rect = wanted()
        except OSError:
            rect = None                  # the dashboard is restarting: keep the window out of the way
        win = x.find(CLASS)
        if win:
            target = rect or (OFF[0], OFF[1], win[5], win[6])
            # again when the window is new, or has been moved or resized some other way
            if (win[0], target) != placed or (win[3], win[4], win[5], win[6]) != target:
                x.move(win[0], *target)
                placed = (win[0], target)
            last_seen = win[0]
        elif last_seen:
            placed = last_seen = None
        time.sleep(0.2)


def main():
    if "--list" in sys.argv:
        for w in X().windows():
            print(*w)
    elif "--move" in sys.argv:
        x = X()
        win = x.find(CLASS)
        if not win:
            sys.exit("no window of class " + CLASS)
        x.move(win[0], *[int(v) for v in sys.argv[sys.argv.index("--move") + 1].split(",")])
    else:
        run()


if __name__ == "__main__":
    main()
