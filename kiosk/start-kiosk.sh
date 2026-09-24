#!/bin/bash
# Opens the dashboard full screen on the Pi's own display: Chromium in kiosk mode.
#
#   Over SSH (or from a desktop's autostart):   ~/boatmfd/kiosk/start-kiosk.sh
#   Close it:                                   ~/boatmfd/kiosk/start-kiosk.sh --stop
#   The kiosk session (install-autostart.sh):   start-kiosk.sh --session
#       keeps the dashboard up for as long as the session runs: closed (Alt+F4) or crashed, it's
#       back a second later. --stop ends that too; start-kiosk.sh brings it back.
#
# At boot, Chromium only starts once the dashboard's server answers -- until then the session's
# background is the splash -- and opens it with ?kiosk, which keeps the same splash over the page
# until it's fully drawn (index.html, app.js). So the screen never shows "can't connect", a white
# window or a half-built page. If the server hasn't answered after two minutes it opens
# starting.html instead, which keeps waiting and says what to check.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE="$HOME/.config/boatmfd-kiosk"      # its own profile: kept apart from normal browsing, and findable
DASHBOARD_URL="${BOAT_KIOSK_URL:-http://127.0.0.1:8090/}"
MODE="${1:-}"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STOPPED="$XDG_RUNTIME_DIR/boatmfd-kiosk.stopped"   # --stop's note to the --session loop
LOG="$XDG_RUNTIME_DIR/boatmfd-kiosk.log"

stop_browser() { pkill -f -- "--user-data-dir=$PROFILE" 2>/dev/null; }
session_running() { pgrep -f -- "start-kiosk.sh --session" >/dev/null; }

if [ "$MODE" = "--stop" ]; then
    touch "$STOPPED"
    stop_browser
    exit 0
fi

BROWSER="$(command -v chromium || command -v chromium-browser)" || {
    echo "Chromium isn't installed: sudo apt install chromium" >&2
    exit 1
}

# Over SSH there is no display in the environment: use the desktop session's (Wayland on current
# Pi OS, X11 on older).
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ]; then
    sock="$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -E '^wayland-[0-9]+$' | head -n 1)"
    if [ -n "$sock" ]; then
        export WAYLAND_DISPLAY="$sock"
    else
        export DISPLAY=:0
    fi
fi
# Chromium picks X11 unless told otherwise (it only guesses Wayland from XDG_SESSION_TYPE, which an
# SSH session doesn't have), and then exits with "Missing X server or $DISPLAY".
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    PLATFORM=wayland
else
    PLATFORM=x11
fi

browser() {
    # The power is cut with a switch, never a clean shutdown, so Chromium always thinks it
    # crashed and offers to "restore pages". Tell it last time ended normally.
    local prefs="$PROFILE/Default/Preferences"
    if [ -f "$prefs" ]; then
        sed -i -e 's/"exited_cleanly":false/"exited_cleanly":true/' -e 's/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$prefs"
    fi
    "$BROWSER" \
        --user-data-dir="$PROFILE" \
        --ozone-platform="$PLATFORM" \
        --kiosk \
        --noerrdialogs \
        --disable-infobars \
        --no-first-run \
        --password-store=basic \
        --check-for-update-interval=31536000 \
        --disable-features=Translate \
        --autoplay-policy=no-user-gesture-required \
        --disable-pinch \
        --overscroll-history-navigation=0 \
        "$(page_url)" >"$LOG" 2>&1
}

page_url() {
    # The dashboard itself once its server answers; the waiting page if it doesn't in two minutes.
    local i
    for i in $(seq 1 120); do
        if curl -fs -o /dev/null --max-time 2 "$DASHBOARD_URL"; then
            echo "${DASHBOARD_URL}?kiosk=1"
            return
        fi
        sleep 1
    done
    echo "file://$DIR/starting.html#${DASHBOARD_URL}?kiosk=1"
}

rm -f "$STOPPED"

if [ "$MODE" = "--session" ]; then
    while [ ! -e "$STOPPED" ]; do
        browser
        sleep 1
    done
    exit 0
fi

# Run from SSH or a desktop's autostart. If the kiosk session is already keeping a browser up,
# closing that one is a restart: the session opens it again.
stop_browser
if session_running; then
    exit 0
fi
sleep 0.5
# Detached from the terminal too: over SSH, a background job still holding the session's output
# keeps the connection open until the browser closes.
browser </dev/null >/dev/null 2>&1 &
disown
