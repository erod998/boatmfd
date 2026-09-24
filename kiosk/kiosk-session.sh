#!/bin/sh
# The kiosk's own login session (install-autostart.sh points the Pi's auto-login at it): labwc with
# boatmfd's config directory only -- the splash, the screen settings and the dashboard -- so no part
# of the Pi desktop ever starts.
#
# The Pi's own session (/usr/bin/labwc-pi) runs labwc in merge mode (-m), which runs every autostart
# file it finds, the desktop's and taskbar's included: a per-user autostart can add to that but
# never replace it. With -C, labwc reads its configuration from the one directory given.
if command -v raspi-config >/dev/null 2>&1 && raspi-config nonint is_pi; then
    export WLR_DRM_FORCE_LIBLIFTOFF=1   # as labwc-pi sets it on a Pi
fi
exec /usr/bin/labwc -C "$HOME/.config/boatmfd-labwc"
