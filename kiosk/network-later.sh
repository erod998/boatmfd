#!/bin/bash
# The dashboard first, the network after: NetworkManager no longer starts during boot, holding up
# everything behind it, but the moment the display is up.
#
#   ~/boatmfd/kiosk/network-later.sh          then  sudo reboot
#   ~/boatmfd/kiosk/network-later.sh --undo   then  sudo reboot
#
# Why: on the Pi, NetworkManager takes ~16 s to report ready (it runs netplan's generator over and
# over), and the login screen, the kiosk session and the dashboard's server all wait for it --
# through network.target -- though none of them needs the network to put the dashboard on screen.
# A service that isn't started during boot isn't waited for, so it's started from here instead:
#
#   boatmfd-network.service   after graphical.target (the display is up): starts NetworkManager
#   boatmfd-network.timer     60 s after boot, whatever happened to the display -- so WiFi, and
#                             SSH over it, always come back even if the screen doesn't
set -eu
PATH="$PATH:/usr/sbin:/sbin"
UNIT=/etc/systemd/system/boatmfd-network.service
TIMER=/etc/systemd/system/boatmfd-network.timer

if [ "${1:-}" = "--undo" ]; then
    sudo systemctl disable boatmfd-network.timer boatmfd-network.service 2>/dev/null || true
    sudo rm -f "$UNIT" "$TIMER"
    sudo systemctl daemon-reload
    sudo systemctl enable NetworkManager.service
    echo "NetworkManager starts during boot again. sudo reboot"
    exit 0
fi

sudo tee "$UNIT" >/dev/null <<'EOF'
[Unit]
Description=Start networking once the dashboard is on screen (boatmfd kiosk/network-later.sh)
After=graphical.target

[Service]
Type=oneshot
ExecStart=/bin/systemctl start --no-block NetworkManager.service

[Install]
WantedBy=graphical.target
EOF

sudo tee "$TIMER" >/dev/null <<'EOF'
[Unit]
Description=Start networking 60 s after boot whatever the display does (boatmfd kiosk/network-later.sh)

[Timer]
OnBootSec=60
Unit=boatmfd-network.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable boatmfd-network.service boatmfd-network.timer
# Not started during boot from now on. It keeps running now: this takes effect at the next boot.
sudo systemctl disable NetworkManager.service
echo "Done: at the next boot the dashboard comes up first, then the network. sudo reboot"
echo "Undo: $0 --undo"
