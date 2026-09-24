"""Writes an X cursor theme whose every cursor is one transparent pixel: the display's session uses it
(XCURSOR_THEME=boatmfd-none in boatmfd-kiosk.service), so the screen never shows a mouse pointer,
even with a mouse plugged in. labwc draws the pointer over the splash; Chromium asks for cursors by
name over the page, so every common name is here.

    python3 no-cursor.py ROOTFS
"""
import os
import struct
import sys

NAMES = """default left_ptr arrow top_left_arrow pointer hand1 hand2 text xterm wait watch progress
left_ptr_watch crosshair cross move fleur all-scroll grab grabbing openhand closedhand not-allowed
no-drop copy alias context-menu help question_arrow cell vertical-text zoom-in zoom-out col-resize
row-resize sb_h_double_arrow sb_v_double_arrow e-resize n-resize ne-resize nw-resize s-resize
se-resize sw-resize w-resize ew-resize ns-resize nesw-resize nwse-resize""".split()

IMAGE_TYPE = 0xFFFD0002
NOMINAL_SIZE = 24


def xcursor():
    """An Xcursor file with one 1x1 fully transparent image."""
    image = struct.pack("<9I", 36, IMAGE_TYPE, NOMINAL_SIZE, 1, 1, 1, 0, 0, 0) + b"\0\0\0\0"
    header = b"Xcur" + struct.pack("<3I", 16, 0x10000, 1)
    toc = struct.pack("<3I", IMAGE_TYPE, NOMINAL_SIZE, len(header) + 12)
    return header + toc + image


def main(root):
    theme = os.path.join(root, "usr/share/icons/boatmfd-none")
    cursors = os.path.join(theme, "cursors")
    os.makedirs(cursors, exist_ok=True)
    with open(os.path.join(theme, "index.theme"), "w") as f:
        f.write("[Icon Theme]\nName=boatmfd-none\nComment=No pointer (boatmfd)\n")
    with open(os.path.join(cursors, "default"), "wb") as f:
        f.write(xcursor())
    for name in NAMES:
        if name != "default":
            link = os.path.join(cursors, name)
            if os.path.lexists(link):
                os.remove(link)
            os.symlink("default", link)


if __name__ == "__main__":
    main(sys.argv[1])
