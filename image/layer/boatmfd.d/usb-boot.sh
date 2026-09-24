#!/bin/sh
# Test builds only (boatmfd.usb_boot=y): lets rpi-image-gen's A/B system boot from a USB drive.
#   usb-boot.sh ROOTFS
#
# The A/B system finds its own partitions through the boot device the bootloader reports: its
# scripts know boot mode 1 (SD card, mmcblk0) and 6 (NVMe, nvme0n1), and its udev rules only look
# at those. This adds boot modes 4 and 5 (USB mass storage, on the Pi 4's USB 3 and USB 2 ports) as
# sda, which is right while the boot drive is the only USB drive plugged in -- fine on the test
# bench, not for the boat, whose Pi 5 boots from SD or NVMe and keeps USB for a charts drive.
#
# The initramfs, where the root is found, is built after this and copies these files in.
set -eu
root=$1
bin="$root/usr/bin"
rules="$root/etc/udev/rules.d"

die() { echo "usb-boot: $*" >&2; exit 1; }
need() { grep -qF -- "$2" "$1" || die "$1 no longer has: $2 (rpi-image-gen changed; update usb-boot.sh)"; }

# The boot device for boot modes 4 and 5, next to NVMe's.
for f in "$bin/rpi-bootdev-tag" "$bin/rpi-slot-label" "$bin/rpi-slot-static"; do
    need "$f" "6) BOOT_DEV=nvme0n1"
    sed -i '/^ *6) BOOT_DEV=nvme0n1/a\   4|5) BOOT_DEV=sda; PART_SEP= ;;  # USB mass storage (boatmfd usb-boot.sh)' "$f"
    [ "$(grep -c 'BOOT_DEV=sda' "$f")" = 1 ] || die "couldn't add USB to $f"
done

# Partition names: sda2, not the mmcblk0p2/nvme0n1p2 these two assume.
for f in "$bin/rpi-slot-label" "$bin/rpi-slot-static"; do
    need "$f" '"/dev/${BOOT_DEV}p${BOOT_PARTN}"'
    sed -i 's|"/dev/${BOOT_DEV}p${BOOT_PARTN}"|"/dev/${BOOT_DEV}${PART_SEP-p}${BOOT_PARTN}"|' "$f"
    grep -qF '${PART_SEP-p}' "$f" || die "couldn't fix partition names in $f"
done

# The udev rules that tag the boot drive's partitions and name the active and other slots.
need "$rules/99-rpi-00-bootdev.rules" 'KERNEL=="nvme0n1p*"'
cat >> "$rules/99-rpi-00-bootdev.rules" <<'EOF'

# USB mass storage (boatmfd usb-boot.sh)
SUBSYSTEM=="block", KERNEL=="sd[a-z][0-9]*", ACTION=="add|change", \
  IMPORT{program}="/usr/bin/rpi-bootdev-tag -u -d $env{DEVNAME}"
EOF
need "$rules/99-rpi-01-abslot.rules" 'KERNEL=="nvme0n1p*", ENV{RPI_ONBOOTDEV}=="1", ENV{ID_PART_ENTRY_NAME}=="?*"'
cat >> "$rules/99-rpi-01-abslot.rules" <<'EOF'

# USB mass storage, GPT labelled (boatmfd usb-boot.sh)
SUBSYSTEM=="block", KERNEL=="sd[a-z][0-9]*", ENV{RPI_ONBOOTDEV}=="1", ENV{ID_PART_ENTRY_NAME}=="?*", ACTION=="add|change", \
  IMPORT{program}="/usr/bin/rpi-slot-label -u -L $env{ID_PART_ENTRY_NAME} -d $env{DEVNAME}", \
  SYMLINK+="$env{SLOT}"
EOF
echo "usb-boot: the A/B system can boot from a USB drive (sda)"
