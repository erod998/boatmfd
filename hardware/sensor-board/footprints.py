"""Builds the project footprint library, sensor-board.pretty. Run with KiCad's bundled Python.

The board is packed at courtyard spacing, so the silkscreen outlines on the small parts land on
their neighbours' pads. These are KiCad's own library footprints with the silkscreen moved to
the fabrication layer, saved under a _NoSilk name: nothing is lost (the outline is still on the
assembly drawing), and because the board uses these footprints as they are in a library, KiCad
does not report them as edited. Only parts that cannot be fitted wrongly are here -- resistors,
capacitors, and the SOT-23 clamp, whose three pads only fit one way.
"""
from pathlib import Path

import pcbnew

HERE = Path(__file__).parent
LIB = "sensor-board"
LIB_DIR = HERE / f"{LIB}.pretty"
KICAD_FP = Path(r"C:\Users\erod9\AppData\Local\Programs\KiCad\10.0\share\kicad\footprints")

SOURCES = [("Resistor_SMD", "R_0805_2012Metric"), ("Resistor_SMD", "R_1206_3216Metric"),
           ("Capacitor_SMD", "C_0805_2012Metric"), ("Capacitor_SMD", "C_1206_3216Metric"),
           ("Package_TO_SOT_SMD", "SOT-23")]


def build():
    # The KiCad format by name: guessing it from the path fails while the folder is still empty.
    io = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
    LIB_DIR.mkdir(exist_ok=True)
    for lib, name in SOURCES:
        fp = pcbnew.FootprintLoad(str(KICAD_FP / f"{lib}.pretty"), name)
        new = f"{name}_NoSilk"
        fp.SetFPID(pcbnew.LIB_ID(LIB, new))
        fp.SetLibDescription(fp.GetLibDescription() + " -- silkscreen moved to the fab layer for a dense board")
        for g in fp.GraphicalItems():
            if g.GetLayer() == pcbnew.F_SilkS:
                g.SetLayer(pcbnew.F_Fab)
        fp.Reference().SetLayer(pcbnew.F_Fab)
        io.FootprintSave(str(LIB_DIR), fp)
    (HERE / "fp-lib-table").write_text(
        '(fp_lib_table\n\t(version 7)\n'
        f'\t(lib (name "{LIB}") (type "KiCad") (uri "${{KIPRJMOD}}/{LIB}.pretty") (options "") '
        '(descr "KiCad library footprints with their silkscreen moved to the fab layer"))\n)\n',
        encoding="utf-8")
    return sorted(p.stem for p in LIB_DIR.glob("*.kicad_mod"))


if __name__ == "__main__":
    print("built", build())
