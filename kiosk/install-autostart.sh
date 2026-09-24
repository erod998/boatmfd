#!/bin/bash
# One-time setup: the Pi logs straight in at boot and shows the dashboard full screen.
# Run it on the Pi:
#   ~/boatmfd/kiosk/install-autostart.sh             a kiosk session: none of the Pi desktop, only the
#                                                    splash and the dashboard, kept open
#   ~/boatmfd/kiosk/install-autostart.sh --desktop   the normal Pi desktop, with the dashboard opened
#                                                    full screen over it at login
#   ~/boatmfd/kiosk/install-autostart.sh --undo      the plain Pi desktop, no dashboard
# Takes effect at the next login (sudo reboot).
#
# The kiosk session is a login session of its own (kiosk-session.sh, "boatmfd-kiosk"), which the
# Pi's auto-login is pointed at; the Pi desktop's session (rpd-labwc) stays installed, and
# --desktop points auto-login back at it.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
ENTRY="$HOME/.config/autostart/boatmfd-kiosk.desktop"
CONF="$HOME/.config/boatmfd-labwc"
SESSION_FILE=/usr/share/wayland-sessions/boatmfd-kiosk.desktop
LIGHTDM=/etc/lightdm/lightdm.conf
MODE="${1:-}"
chmod +x "$DIR/start-kiosk.sh" "$DIR/kiosk-session.sh"

login_session() {
    # Which session the Pi's auto-login starts. lightdm.conf is read after its conf.d, so the
    # setting is changed in lightdm.conf itself, backed up the first time.
    [ -f "$LIGHTDM" ] || { echo "No $LIGHTDM: set the auto-login session to $1 yourself."; return; }
    [ -f "$LIGHTDM.before-boatmfd" ] || sudo cp "$LIGHTDM" "$LIGHTDM.before-boatmfd"
    if grep -q '^autologin-session=' "$LIGHTDM"; then
        sudo sed -i "s/^autologin-session=.*/autologin-session=$1/" "$LIGHTDM"
    else
        sudo sed -i "/^\[Seat:\*\]/a autologin-session=$1" "$LIGHTDM"
    fi
    echo "Auto-login session: $1"
}

old_autostart() {
    # Rev 1 of this script wrote a labwc autostart into the desktop's own config; take it out.
    local f="$HOME/.config/labwc/autostart"
    if [ -f "$f" ] && grep -q "boatmfd kiosk session" "$f"; then rm -f "$f"; fi
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

# Boot straight in, logged in, with nobody at a login screen.
if ! grep -rqs "^autologin-user=$USER\$" /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d/; then
    if command -v raspi-config >/dev/null 2>&1; then
        sudo raspi-config nonint do_boot_behaviour B4
        echo "Set the Pi to log in as $USER by itself."
    else
        echo "raspi-config not found: set the Pi to log in automatically yourself."
    fi
fi

case "$MODE" in
    "")
        old_autostart
        rm -f "$ENTRY"
        mkdir -p "$CONF"
        cat > "$CONF/autostart" <<EOF
# boatmfd kiosk session (kiosk/install-autostart.sh, kiosk-session.sh): the splash as the
# background, the screen settings, and the dashboard. Nothing else.
/usr/bin/swaybg -c 000000 -i $DIR/plymouth/splash.png -m center &
/usr/bin/kanshi &
$DIR/start-kiosk.sh --session &
# Anything local to this Pi (not in the repo): an executable autostart.local next to this file.
[ -x "$CONF/autostart.local" ] && "$CONF/autostart.local" &
EOF
        # The keyboard layout the Pi was set up with.
        [ -f "$HOME/.config/labwc/environment" ] && cp "$HOME/.config/labwc/environment" "$CONF/environment"
        sudo tee "$SESSION_FILE" >/dev/null <<EOF
[Desktop Entry]
Name=Boat MFD kiosk
Comment=The boat dashboard, full screen, with none of the desktop (boatmfd kiosk/install-autostart.sh)
Exec=$DIR/kiosk-session.sh
Type=Application
DesktopNames=boatmfd
EOF
        login_session boatmfd-kiosk
        echo "The Pi now starts straight into the dashboard, with none of the desktop."
        ;;
    --desktop)
        old_autostart
        login_session rpd-labwc
        add_entry
        ;;
    --undo)
        old_autostart
        login_session rpd-labwc
        rm -f "$ENTRY"
        echo "The dashboard no longer opens at login."
        ;;
    *)
        echo "usage: $0 [--desktop | --undo]" >&2
        exit 2
        ;;
esac
echo "Takes effect at the next login: sudo reboot"
