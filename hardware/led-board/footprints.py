"""Builds the project footprint library, led-board.pretty. Run with KiCad's bundled Python.

The MOSFET rows and the RP2040's decoupling are packed tight, so the silkscreen outlines on the
small parts would land on their neighbours' pads. These are KiCad's own library footprints with
the silkscreen moved to the fabrication layer, saved under a _NoSilk name: nothing is lost (the
outline is still on the assembly drawing), and because the board uses these footprints as they
are in a library, KiCad does not report them as edited. Only parts that cannot be fitted wrongly are here -- resistors,
capacitors, and the SOT-23 clamps, whose three pads only fit one way. (The sensor board's list,
and the 0402 size for the RP2040's.)
"""
from pathlib import Path

import pcbnew

from kicadpaths import FOOTPRINTS as KICAD_FP

HERE = Path(__file__).parent
LIB = "led-board"
LIB_DIR = HERE / f"{LIB}.pretty"

SOURCES = [("Resistor_SMD", "R_0402_1005Metric"), ("Resistor_SMD", "R_0805_2012Metric"),
           ("Resistor_SMD", "R_1206_3216Metric"), ("Capacitor_SMD", "C_0402_1005Metric"),
           ("Capacitor_SMD", "C_0805_2012Metric"), ("Capacitor_SMD", "C_1206_3216Metric"),
           ("Package_TO_SOT_SMD", "SOT-23")]


# The RP2040's package, its exposed pad's four vias drilled 0.3 mm with 0.6 mm pads: KiCad's are
# 0.2 mm, under PCBWay's standard minimum. Moved in from 1.35 to 1.0 mm off the centre, so the
# bigger pads stay inside the 3.2 mm exposed pad and take no more room underneath than it does.
QFN = ("Package_DFN_QFN", "QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm_ThermalVias")
QFN_VIA_DRILL, QFN_VIA_PAD, QFN_VIA_AT = 0.3, 0.6, 1.0


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
    fp = pcbnew.FootprintLoad(str(KICAD_FP / f"{QFN[0]}.pretty"), QFN[1])
    fp.SetFPID(pcbnew.LIB_ID(LIB, QFN[1] + "_Drill0.3"))
    fp.SetLibDescription(fp.GetLibDescription() + " -- exposed-pad vias drilled 0.3 mm")
    for pad in fp.Pads():
        if pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH:
            pad.SetDrillSize(pcbnew.VECTOR2I(pcbnew.FromMM(QFN_VIA_DRILL), pcbnew.FromMM(QFN_VIA_DRILL)))
            size = pcbnew.VECTOR2I(pcbnew.FromMM(QFN_VIA_PAD), pcbnew.FromMM(QFN_VIA_PAD))
            try:
                pad.SetSize(pcbnew.F_Cu, size)      # KiCad 9+: the padstack's all-layers size
            except TypeError:
                pad.SetSize(size)
            at = pad.GetFPRelativePosition()
            sx, sy = (1 if at.x > 0 else -1), (1 if at.y > 0 else -1)
            pad.SetFPRelativePosition(pcbnew.VECTOR2I(sx * pcbnew.FromMM(QFN_VIA_AT), sy * pcbnew.FromMM(QFN_VIA_AT)))
    io.FootprintSave(str(LIB_DIR), fp)
    (HERE / "fp-lib-table").write_text(
        '(fp_lib_table\n\t(version 7)\n'
        f'\t(lib (name "{LIB}") (type "KiCad") (uri "${{KIPRJMOD}}/{LIB}.pretty") (options "") '
        '(descr "KiCad library footprints with their silkscreen moved to the fab layer"))\n)\n',
        encoding="utf-8")
    return sorted(p.stem for p in LIB_DIR.glob("*.kicad_mod"))


if __name__ == "__main__":
    print("built", build())
