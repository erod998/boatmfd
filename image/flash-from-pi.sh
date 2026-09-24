#!/bin/bash
# Writes the boatmfd image (image/README.md) onto a drive plugged into this Pi, then puts this Pi's
# SSH keys and WiFi networks on it, so the new system lets in the same computers and joins the same
# network -- they go from this Pi straight onto the drive, never into the image or the repo.
#
#   sudo image/flash-from-pi.sh IMAGE DEVICE
#
# IMAGE: a .img, .img.zst or .img.xz file, or its https:// address (streamed onto the drive, not
# saved). DEVICE: the whole drive, e.g. /dev/sda. Everything on it is erased; it asks first.
set -euo pipefail
die() { echo "flash-from-pi: $*" >&2; exit 1; }

[ $# -eq 2 ] || die "usage: sudo $0 IMAGE DEVICE"
IMAGE=$1
DEV=$2
[ "$(id -u)" -eq 0 ] || die "run it with sudo"
[ -b "$DEV" ] && [ "$(lsblk -dno TYPE "$DEV")" = disk ] || die "$DEV isn't a whole drive"
own="$(lsblk -no PKNAME "$(findmnt -no SOURCE /)")"
[ "/dev/$own" != "$DEV" ] || die "$DEV is the drive this Pi is running from"
USER_NAME="${SUDO_USER:-admin}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"

lsblk -o NAME,SIZE,MODEL,LABEL,MOUNTPOINTS "$DEV"
read -r -p "Erase $DEV and write the image onto it? Type yes: " answer
[ "$answer" = yes ] || die "nothing written"

for part in $(lsblk -lnpo NAME "$DEV" | tail -n +2); do
    umount "$part" 2>/dev/null || true
done

read_image() {
    case "$IMAGE" in
        https://*) curl -fL --retry 3 "$IMAGE" ;;
        *) cat "$IMAGE" ;;
    esac
}
decompress() {
    case "$IMAGE" in
        *.zst) zstd -dc ;;
        *.xz) xz -dc ;;
        *) cat ;;
    esac
}
read_image | decompress | dd of="$DEV" bs=4M iflag=fullblock oflag=direct status=progress
sync
partprobe "$DEV" 2>/dev/null || blockdev --rereadpt "$DEV"
udevadm settle

# The small FAT partition the new system reads settings from at boot (/usr/lib/boatmfd/apply-settings).
conf="$(lsblk -lnpo NAME,PARTLABEL "$DEV" | awk '$2 == "bootconfig" { print $1 }')"
[ -n "$conf" ] || die "written, but there's no bootconfig partition on $DEV"
mnt="$(mktemp -d)"
mount "$conf" "$mnt"
trap 'umount "$mnt" 2>/dev/null || true; rmdir "$mnt"' EXIT
mkdir -p "$mnt/boatmfd"

if [ -s "$USER_HOME/.ssh/authorized_keys" ]; then
    cp "$USER_HOME/.ssh/authorized_keys" "$mnt/boatmfd/authorized_keys"
    echo "SSH: the keys that can log in here as $USER_NAME ($(grep -c . "$USER_HOME/.ssh/authorized_keys"))"
else
    echo "SSH: $USER_NAME has no authorized_keys here -- the new system won't let anyone in over SSH"
fi

# WiFi, as iwd profiles: the file is named after the network, or "=" and its name in hex if it has
# characters iwd doesn't allow in a file name.
if command -v nmcli >/dev/null 2>&1; then
    nmcli -e no -t -f NAME,TYPE connection show | while IFS= read -r row; do
        name="${row%:*}"
        [ "${row##*:}" = 802-11-wireless ] || continue
        ssid="$(nmcli -e no -s -g 802-11-wireless.ssid connection show "$name")"
        psk="$(nmcli -e no -s -g 802-11-wireless-security.psk connection show "$name")"
        [ -n "$ssid" ] && [ -n "$psk" ] || continue
        if printf '%s' "$ssid" | grep -qE '^[A-Za-z0-9 _-]+$'; then
            file="$ssid.psk"
        else
            file="=$(printf '%s' "$ssid" | od -An -tx1 | tr -d ' \n').psk"
        fi
        printf '[Security]\nPassphrase=%s\n' "$psk" > "$mnt/boatmfd/$file"
        echo "WiFi: $ssid"
    done
fi

echo
echo "Done. To boot it: sudo poweroff, take out the SD card, and power on."
echo "Then: ssh admin@boatmfd.local (or its address on the router)."
