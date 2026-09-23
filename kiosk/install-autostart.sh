#!/bin/bash
# One-time setup: the Pi logs straight into its desktop at boot and opens the dashboard full
# screen (start-kiosk.sh). Run it on the Pi:  ~/boatmfd/kiosk/install-autostart.sh
# Undo:  ~/boatmfd/kiosk/install-autostart.sh --undo
#
# The kiosk starts from the desktop's standard autostart folder (~/.config/autostart), which every
# Raspberry Pi OS desktop runs -- labwc, Wayfire or X11 -- so nothing here depends on which one.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
ENTRY="$HOME/.config/autostart/boatmfd-kiosk.desktop"

if [ "${1:-}" = "--undo" ]; then
    rm -f "$ENTRY"
    echo "Removed $ENTRY: the dashboard no longer opens at login."
    exit 0
fi

chmod +x "$DIR/start-kiosk.sh"
mkdir -p "$(dirname "$ENTRY")"
cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=Boat MFD kiosk
Comment=The boat dashboard, full screen
Exec=$DIR/start-kiosk.sh
X-GNOME-Autostart-enabled=true
EOF
echo "Added $ENTRY: the dashboard opens full screen at every desktop login."

# Boot straight into the desktop, logged in, with nobody at a login screen.
if command -v raspi-config >/dev/null 2>&1; then
    sudo raspi-config nonint do_boot_behaviour B4
    echo "Set the Pi to boot to the desktop, logged in automatically."
else
    echo "raspi-config not found: set the Pi to boot to the desktop with automatic login yourself."
fi

echo "Reboot to try it (sudo reboot), or open it now: $DIR/start-kiosk.sh"
