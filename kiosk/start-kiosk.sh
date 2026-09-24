#!/bin/bash
# Opens the dashboard full screen on the Pi's own display: Chromium in kiosk mode.
#
#   From the desktop's autostart (see install-autostart.sh), or over SSH:
#     ~/boatmfd/kiosk/start-kiosk.sh
#   Close it with Alt+F4, or over SSH:
#     ~/boatmfd/kiosk/start-kiosk.sh --stop
#
# It opens starting.html first, which says "Starting up" until the dashboard's server answers and
# then switches to it, so at boot the screen never shows a "can't connect" page while the server
# is still starting.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE="$HOME/.config/boatmfd-kiosk"      # its own profile: kept apart from normal browsing, and findable
DASHBOARD_URL="${BOAT_KIOSK_URL:-http://127.0.0.1:8090/}"

stop() { pkill -f -- "--user-data-dir=$PROFILE" 2>/dev/null; }

if [ "${1:-}" = "--stop" ]; then
    stop
    exit 0
fi

BROWSER="$(command -v chromium || command -v chromium-browser)" || {
    echo "Chromium isn't installed: sudo apt install chromium" >&2
    exit 1
}

# Over SSH there is no display in the environment: use the desktop session's (Wayland on current
# Pi OS, X11 on older).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
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

# One kiosk at a time.
stop
sleep 0.5

# The power is cut with a switch, never a clean shutdown, so Chromium always thinks it crashed
# and offers to "restore pages". Tell it last time ended normally.
prefs="$PROFILE/Default/Preferences"
if [ -f "$prefs" ]; then
    sed -i -e 's/"exited_cleanly":false/"exited_cleanly":true/' -e 's/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$prefs"
fi

nohup "$BROWSER" \
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
    "file://$DIR/starting.html#$DASHBOARD_URL" >"$XDG_RUNTIME_DIR/boatmfd-kiosk.log" 2>&1 &
disown
