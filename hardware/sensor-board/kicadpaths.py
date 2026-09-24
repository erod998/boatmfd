"""Where KiCad 10 is. The default is the Windows install the sensor board was built with; set
KICAD_DIR to the install's root anywhere else (on Linux, /usr: its libraries are in
/usr/share/kicad and kicad-cli in /usr/bin)."""
import os
from pathlib import Path

KICAD = Path(os.environ.get("KICAD_DIR", r"C:\Users\erod9\AppData\Local\Programs\KiCad\10.0"))
SHARE = KICAD / "share" / "kicad"
SYMBOLS = SHARE / "symbols"
FOOTPRINTS = SHARE / "footprints"
TEMPLATES = SHARE / "template"
CLI = KICAD / "bin" / ("kicad-cli.exe" if os.name == "nt" else "kicad-cli")
