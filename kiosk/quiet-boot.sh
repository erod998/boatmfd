#!/bin/bash
# Hides the Pi's boot: no rainbow square, no raspberry logos, no scrolling text -- just a black
# screen that says "Starting up..." (plymouth/) until the kiosk's first page takes over with the
# same words, and then the dashboard.
#
#   ~/boatmfd/kiosk/quiet-boot.sh           then  sudo reboot
#   ~/boatmfd/kiosk/quiet-boot.sh --undo    puts the boot files back as they were, then  sudo reboot
#
# What it changes, each backed up first (<file>.boatmfd-<time>.bak):
#   config.txt   disable_splash=1                  no rainbow square from the firmware
#   cmdline.txt  console=tty1 -> console=tty3      kernel messages go to a console nobody looks at
#                quiet splash loglevel=3           ...and far fewer of them; the splash screen on
#                logo.nologo                       no raspberry logos
#                vt.global_cursor_default=0        no blinking cursor
#                plymouth.ignore-serial-consoles
#   the boot splash theme                          "boatmfd" (plymouth/), in place of the Pi's own
#   the desktop background                         black, for the second before the kiosk opens
# Safe to run again: it only adds what's missing.
set -eu
set -f          # the kernel command line is split into words below; never glob them
# plymouth-set-default-theme and update-initramfs live in /usr/sbin, which isn't on a normal
# user's PATH on Debian: without this the theme check fails and the initramfs is never rebuilt.
PATH="$PATH:/usr/sbin:/sbin"
DIR="$(cd "$(dirname "$0")" && pwd)"
BOOT=/boot/firmware
[ -f "$BOOT/cmdline.txt" ] || BOOT=/boot
CMDLINE="$BOOT/cmdline.txt"
CONFIG="$BOOT/config.txt"
STAMP="$(date +%Y%m%d-%H%M%S)"
THEMES=/usr/share/plymouth/themes

rebuild_initramfs() {
    # The splash runs from the initramfs on current Pi OS; rebuild it so the theme is in there.
    if command -v update-initramfs >/dev/null 2>&1; then
        echo "Rebuilding the initramfs (a minute or so)..."
        sudo update-initramfs -u
    fi
}

if [ "${1:-}" = "--undo" ]; then
    for f in "$CMDLINE" "$CONFIG"; do
        original="$(ls -t "$f".boatmfd-*.bak 2>/dev/null | tail -n 1 || true)"   # the oldest: from before any run
        if [ -n "$original" ]; then
            sudo cp "$original" "$f"
            echo "Restored $f from $original"
        fi
    done
    if command -v plymouth-set-default-theme >/dev/null 2>&1 && [ -d "$THEMES/pix" ]; then
        sudo plymouth-set-default-theme pix
        rebuild_initramfs
    fi
    echo "Done. sudo reboot to see the normal boot again."
    exit 0
fi

# ---- the splash screen
if ! command -v plymouth-set-default-theme >/dev/null 2>&1; then
    echo "Installing the boot splash (plymouth)..."
    sudo apt-get install -y plymouth
fi
sudo mkdir -p "$THEMES/boatmfd"
sudo cp "$DIR"/plymouth/boatmfd.plymouth "$DIR"/plymouth/boatmfd.script "$DIR"/plymouth/splash.png "$THEMES/boatmfd/"
sudo plymouth-set-default-theme boatmfd
echo "Boot splash: boatmfd (\"Starting up...\")"

# ---- cmdline.txt: one line, and it has to stay one line
sudo cp "$CMDLINE" "$CMDLINE.boatmfd-$STAMP.bak"
line="$(head -n 1 "$CMDLINE" | tr -d '\r')"
new=""
for word in $line; do
    case "$word" in
        console=tty1) word="console=tty3" ;;
        loglevel=*) continue ;;              # replaced by loglevel=3 below
    esac
    new="$new $word"
done
for want in quiet splash loglevel=3 logo.nologo vt.global_cursor_default=0 plymouth.ignore-serial-consoles; do
    case " $new " in
        *" $want "*) ;;
        *) new="$new $want" ;;
    esac
done
new="${new# }"
if [ -z "$new" ] || ! printf '%s' "$new" | grep -q 'root='; then
    echo "cmdline.txt doesn't look right after editing; leaving it as it was." >&2
    exit 1
fi
printf '%s\n' "$new" | sudo tee "$CMDLINE" >/dev/null
echo "cmdline.txt: $new"

# ---- config.txt: no rainbow square
if ! grep -q '^disable_splash=1' "$CONFIG"; then
    sudo cp "$CONFIG" "$CONFIG.boatmfd-$STAMP.bak"
    printf '\n[all]\n# boatmfd: no rainbow square at power-on\ndisable_splash=1\n' | sudo tee -a "$CONFIG" >/dev/null
    echo "config.txt: disable_splash=1"
fi

# ---- the desktop background, for the moment between the splash and the kiosk. The desktop's
# settings are per profile ("default" on Trixie, "LXDE-pi" on Bookworm) and per monitor
# (desktop-items-0, -1...); a user copy overrides the system's.
set +f
for sys_dir in /etc/xdg/pcmanfm/default /etc/xdg/pcmanfm/LXDE-pi; do
    [ -d "$sys_dir" ] || continue
    conf_dir="$HOME/.config/pcmanfm/$(basename "$sys_dir")"
    mkdir -p "$conf_dir"
    for sys_file in "$sys_dir"/desktop-items-*.conf; do
        [ -f "$sys_file" ] || continue
        f="$conf_dir/$(basename "$sys_file")"
        [ -f "$f" ] || cp "$sys_file" "$f"
        sed -i -e 's/^wallpaper_mode=.*/wallpaper_mode=color/' -e 's/^desktop_bg=.*/desktop_bg=#000000/' "$f"
        echo "Desktop background: black ($f)"
    done
done
set -f

rebuild_initramfs
echo "Done. sudo reboot to see it."
