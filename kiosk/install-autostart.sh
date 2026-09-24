#!/bin/bash
# One-time setup: the Pi logs straight in at boot and shows the dashboard full screen.
# Run it on the Pi:
#   ~/boatmfd/kiosk/install-autostart.sh             a kiosk session: no desktop, no taskbar, just the
#                                                    dashboard, kept open (start-kiosk.sh --session)
#   ~/boatmfd/kiosk/install-autostart.sh --desktop   the normal Pi desktop, with the dashboard opened
#                                                    full screen over it at login
#   ~/boatmfd/kiosk/install-autostart.sh --undo      neither: the plain desktop, no dashboard
# Takes effect at the next login (sudo reboot).
#
# The kiosk session needs labwc, the Pi OS desktop's compositor since late 2024: its per-user
# autostart replaces the system one that starts the desktop and taskbar. Elsewhere (Wayfire, X11)
# the dashboard opens over the desktop instead, from the standard ~/.config/autostart.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
ENTRY="$HOME/.config/autostart/boatmfd-kiosk.desktop"
SESSION="$HOME/.config/labwc/autostart"
MODE="${1:-}"
chmod +x "$DIR/start-kiosk.sh"

remove_session() {
    # Only ever remove the file this script wrote.
    if [ -f "$SESSION" ] && grep -q "boatmfd kiosk session" "$SESSION"; then
        rm -f "$SESSION"
        echo "Removed $SESSION: the Pi desktop and taskbar start again."
    fi
}

add_entry() {
    mkdir -p "$(dirname "$ENTRY")"
    cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=Boat MFD kiosk
Comment=The boat dashboard, full screen
Exec=$DIR/start-kiosk.sh
X-GNOME-Autostart-enabled=true
EOF
    echo "Added $ENTRY: the dashboard opens full screen over the desktop at login."
}

case "$MODE" in
    --undo)
        remove_session
        rm -f "$ENTRY"
        echo "The dashboard no longer opens at login."
        exit 0
        ;;
    --desktop)
        remove_session
        add_entry
        ;;
    "")
        if [ -d /etc/xdg/labwc ] || pidof labwc >/dev/null; then
            if [ -f "$SESSION" ] && ! grep -q "boatmfd kiosk session" "$SESSION"; then
                cp "$SESSION" "$SESSION.before-boatmfd"
                echo "Kept your own $SESSION as $SESSION.before-boatmfd"
            fi
            mkdir -p "$(dirname "$SESSION")"
            cat > "$SESSION" <<EOF
# boatmfd kiosk session (kiosk/install-autostart.sh): instead of the desktop and taskbar, only the
# splash as the background, the screen settings and the dashboard. --desktop brings the desktop back.
/usr/bin/swaybg -c 000000 -i $DIR/plymouth/splash.png -m center &
/usr/bin/kanshi &
$DIR/start-kiosk.sh --session &
EOF
            rm -f "$ENTRY"   # the session starts it; the desktop's autostart isn't run any more
            echo "Wrote $SESSION: the Pi starts straight into the dashboard, no desktop."
        else
            echo "Not a labwc desktop: opening the dashboard over the desktop instead."
            add_entry
        fi
        ;;
    *)
        echo "usage: $0 [--desktop | --undo]" >&2
        exit 2
        ;;
esac

# Boot straight in, logged in, with nobody at a login screen.
if grep -rqs "^autologin-user=$USER\$" /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d/; then
    echo "The Pi already logs in as $USER by itself."
elif command -v raspi-config >/dev/null 2>&1; then
    sudo raspi-config nonint do_boot_behaviour B4
    echo "Set the Pi to boot to the desktop, logged in automatically."
else
    echo "raspi-config not found: set the Pi to log in automatically yourself."
fi

echo "Takes effect at the next login: sudo reboot"
