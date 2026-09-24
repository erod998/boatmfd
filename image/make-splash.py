"""Makes the kernel's boot logo for the image (rpi-image-gen's rpi-splash-screen layer) from the
kiosk's splash, kiosk/plymouth/splash.png: the same "Starting up..." centred on a black full-HD
frame, the spot where the display's background (swaybg) and the dashboard's own splash put it, so
the boot logo, the session and the page hand over without the text moving.

The logo has to be an uncompressed 24-bit TGA, at most 1920x1080, with fewer than 224 colours.

    python3 make-splash.py kiosk/plymouth/splash.png image/build/splash.tga [WIDTHxHEIGHT]

Needs Pillow (python3-pil). The workflow (.github/workflows/image.yml) runs it before each build.
"""
import sys
from pathlib import Path

from PIL import Image


def main(src, out, size="1920x1080"):
    width, height = (int(v) for v in size.split("x"))
    logo = Image.open(src).convert("RGB")
    frame = Image.new("RGB", (width, height), (0, 0, 0))
    frame.paste(logo, ((width - logo.width) // 2, (height - logo.height) // 2))
    # Anti-aliased text has a couple of hundred shades of grey; 64 look the same.
    frame = frame.quantize(colors=64, dither=Image.Dither.NONE).convert("RGB")
    colours = len(frame.getcolors(maxcolors=width * height))
    if colours >= 224:
        sys.exit(f"{colours} colours: the kernel takes fewer than 224")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    frame.save(out)  # Pillow writes TGA uncompressed unless asked for RLE
    print(f"{out}: {width}x{height}, {colours} colours")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(*sys.argv[1:])
