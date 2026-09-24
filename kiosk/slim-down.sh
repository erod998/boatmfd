#!/bin/bash
# Turns off the parts of Raspberry Pi OS a boat dashboard doesn't use. Nothing is uninstalled:
# services are disabled (and a couple masked), and every change is listed in
# ~/.config/boatmfd-slim-down.list so --undo can put back exactly those.
#
#   ~/boatmfd/kiosk/slim-down.sh          then  sudo reboot
#   ~/boatmfd/kiosk/slim-down.sh --undo   then  sudo reboot
#
# Measured on the Pi 4, none of these use any real CPU: Chromium drawing the dashboard is nearly
# all of it. What this buys is a faster boot, and a Pi that does nothing on its own -- no updates,
# no indexing -- which matters when the power is cut with a switch.
#
#   cloud-init                   Raspberry Pi Imager's first-boot setup, re-run every boot: ~4.5 s
#   NetworkManager-wait-online   holds the boot until WiFi is up; nothing here needs that: ~6 s
#   xrdp, xrdp-sesman            remote desktop; the one that made the boot wait for WiFi.
#                                Using it? Remove its lines below before running this.
#   apt-daily, apt-daily-upgrade, packagekit
#                                automatic package updates: one interrupted by the power switch
#                                can leave the system unbootable. Update by hand over SSH.
#   cups, cups-browsed, cups.path   printing
#   bluetooth, hciuart           the Pi's own Bluetooth; the stereo does its own
#   rpcbind, nfs-blkmap          network file systems (NFS)
#   man-db.timer                 rebuilds the manual pages' index
#
# Kept: WiFi (NetworkManager), SSH, avahi (pi.local), time sync, audio (the alarm's sound), cron.
set -u
LIST="$HOME/.config/boatmfd-slim-down.list"
UNITS="NetworkManager-wait-online.service xrdp.service xrdp-sesman.service
apt-daily.timer apt-daily-upgrade.timer
cups.service cups-browsed.service cups.path cups.socket
bluetooth.service hciuart.service
rpcbind.service rpcbind.socket nfs-blkmap.service
man-db.timer"
MASK="packagekit.service"          # started on demand over D-Bus, so disabling isn't enough

if [ "${1:-}" = "--undo" ]; then
    [ -f "$LIST" ] || { echo "Nothing to undo ($LIST not found)."; exit 0; }
    while read -r action unit; do
        case "$action" in
            disabled) sudo systemctl enable "$unit" && echo "enabled $unit" ;;
            masked) sudo systemctl unmask "$unit" && echo "unmasked $unit" ;;
            cloud-init) sudo rm -f /etc/cloud/cloud-init.disabled && echo "cloud-init back on" ;;
        esac
    done < "$LIST"
    rm -f "$LIST"
    echo "Done. sudo reboot"
    exit 0
fi

mkdir -p "$(dirname "$LIST")"
touch "$LIST"
for unit in $UNITS; do
    # Only what's installed and on; each one recorded, so --undo turns back on only these.
    if [ "$(systemctl is-enabled "$unit" 2>/dev/null)" = "enabled" ]; then
        sudo systemctl disable --now "$unit" >/dev/null 2>&1 && echo "disabled $unit" | tee -a "$LIST"
    fi
done
for unit in $MASK; do
    if systemctl list-unit-files "$unit" --no-legend 2>/dev/null | grep -q . && \
       [ "$(systemctl is-enabled "$unit" 2>/dev/null)" != "masked" ]; then
        sudo systemctl mask --now "$unit" >/dev/null 2>&1 && echo "masked $unit" | tee -a "$LIST"
    fi
done
if [ -d /etc/cloud ] && [ ! -e /etc/cloud/cloud-init.disabled ]; then
    sudo touch /etc/cloud/cloud-init.disabled && echo "cloud-init off" && echo "cloud-init -" >> "$LIST"
fi
echo "Done. sudo reboot -- and undo any of it with: $0 --undo"
