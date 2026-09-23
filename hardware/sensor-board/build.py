"""Builds everything in this folder from design.py, checks it, and writes the fabrication files.

Run with KiCad's bundled Python (the steps need pcbnew):
    & "C:\\Users\\erod9\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe" build.py

  1. footprints.py  -> sensor-board.pretty (the project footprint library)
  2. schematic.py   -> sensor-board.kicad_sch, then KiCad's ERC, which must be clean
  3. board.py       -> sensor-board.kicad_pcb (+ .kicad_pro, .kicad_dru), then KiCad's DRC with
                       the schematic-parity check, which must be clean at every severity
  4. fab/           -> Gerbers and drill files (one zip), BOM, pick-and-place, schematic and
                       assembly PDFs, renders

Any failed check stops the build before fab/ is written. The check reports are left in reports/.
"""
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import design

HERE = Path(__file__).parent
KICAD_CLI = Path(r"C:\Users\erod9\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe")
PROJECT = "sensor-board"
SCH, PCB = HERE / f"{PROJECT}.kicad_sch", HERE / f"{PROJECT}.kicad_pcb"
FAB, REPORTS = HERE / "fab", HERE / "reports"
GERBER_LAYERS = ["F.Cu", "B.Cu", "F.Paste", "B.Paste", "F.Silkscreen", "B.Silkscreen",
                 "F.Mask", "B.Mask", "Edge.Cuts"]


def run(*args):
    result = subprocess.run([str(a) for a in args], cwd=HERE, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"failed: {' '.join(str(a) for a in args)}\n{result.stdout}{result.stderr}")
    return result.stdout


def cli(*args):
    return run(KICAD_CLI, *args)


def script(name):
    print(f"  {name}: {run(sys.executable, name).strip().splitlines()[-1]}")


# ---------------------------------------------------------------- checks
def erc():
    out = REPORTS / "erc.json"
    cli("sch", "erc", "--severity-all", "--format", "json", "-o", out, SCH)
    found = [v for sheet in json.loads(out.read_text(encoding="utf-8"))["sheets"] for v in sheet["violations"]]
    report("ERC", found)


def drc():
    out = REPORTS / "drc.json"
    cli("pcb", "drc", "--schematic-parity", "--severity-all", "--format", "json", "-o", out, PCB)
    r = json.loads(out.read_text(encoding="utf-8"))
    report("DRC", r["violations"] + r["unconnected_items"] + r["schematic_parity"])


def report(name, found):
    if found:
        for v in found:
            where = "; ".join(i.get("description", "") for i in v.get("items", []))
            print(f"    {v['severity']}: {v['description']} ({where})")
        sys.exit(f"{name}: {len(found)} findings -- fix them before building the fabrication files")
    print(f"  {name}: clean")


# ---------------------------------------------------------------- fabrication files
def gerbers():
    """Gerbers and Excellon drill files in one zip, all from the board's bottom-left corner."""
    tmp = FAB / "gerbers"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    cli("pcb", "export", "gerbers", "--layers", ",".join(GERBER_LAYERS), "--subtract-soldermask",
        "--use-drill-file-origin", "-o", f"{tmp}/", PCB)
    cli("pcb", "export", "drill", "--format", "excellon", "--drill-origin", "plot", "--excellon-units", "mm",
        "--excellon-separate-th", "-o", f"{tmp}/", PCB)
    zpath = FAB / f"{PROJECT}-gerbers.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(tmp.iterdir()):
            z.write(f, f.name)
    shutil.rmtree(tmp)
    return zpath


def package(footprint):
    """A short package name for the BOM: "0805", "SOT-23", "TSSOP-10", "SOD-123"... Connectors,
    which are hand-soldered, keep their full footprint name."""
    lib, name = footprint.split(":")
    name = name.removesuffix("_NoSilk")
    if lib.startswith(("Connector", "PinSocket")):
        return name
    chip = re.match(r"[RC]_(\d{4})_", name)
    if chip:
        return chip.group(1)
    if name.startswith("D_"):
        name = name[2:]
    return name.split("_")[0]


def bom():
    """One line per distinct part, JLCPCB's column names first. From design.py, which the
    parity check has just proved matches both the schematic and the board."""
    groups = {}
    for p in design.PARTS:
        if p.symbol.startswith("Mechanical:"):
            continue
        key = (p.value, p.footprint, p.mpn)
        groups.setdefault(key, []).append(p)
    rows = []
    for (value, fp, mpn), parts in groups.items():
        parts.sort(key=lambda p: (re.sub(r"\d", "", p.ref), int(re.sub(r"\D", "", p.ref))))
        mount = "THT (hand solder)" if fp.split(":")[0].startswith(("Connector", "PinSocket")) else "SMD"
        notes = "; ".join(dict.fromkeys(p.note for p in parts if p.note))
        rows.append([value, ",".join(p.ref for p in parts), package(fp), mpn, len(parts), mount, notes])
    rows.sort(key=lambda r: (r[5] != "SMD", r[1]))
    path = FAB / f"{PROJECT}-bom.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "MPN", "Qty", "Mount", "Note"])
        w.writerows(rows)
    return path


def cpl():
    """Pick-and-place for the surface-mount parts, in JLCPCB's format."""
    cli("pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "--smd-only",
        "--use-drill-file-origin", "-o", REPORTS / "pos.csv", PCB)
    rows = list(csv.DictReader(io.StringIO((REPORTS / "pos.csv").read_text(encoding="utf-8"))))
    path = FAB / f"{PROJECT}-cpl.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in rows:
            w.writerow([r["Ref"], f"{float(r['PosX']):.3f}mm", f"{float(r['PosY']):.3f}mm",
                        "Top" if r["Side"] == "top" else "Bottom", f"{float(r['Rot']) % 360:.0f}"])
    return path, len(rows)


def pdfs():
    sch = FAB / f"{PROJECT}-schematic.pdf"
    cli("sch", "export", "pdf", "-o", sch, SCH)
    asm = FAB / f"{PROJECT}-assembly.pdf"
    cli("pcb", "export", "pdf", "--mode-single", "--layers", "F.Fab,F.Silkscreen,Edge.Cuts",
        "--sketch-pads-on-fab-layers", "--include-border-title", "--black-and-white", "-o", asm, PCB)
    layers = FAB / f"{PROJECT}-layers.pdf"
    cli("pcb", "export", "pdf", "--mode-multipage", "--layers", "F.Cu,B.Cu,F.Silkscreen,B.Fab",
        "--common-layers", "Edge.Cuts", "--include-border-title", "-o", layers, PCB)
    return sch, asm, layers


def renders():
    shots = {"top": ["--side", "top"], "bottom": ["--side", "bottom"],
             "angle": ["--side", "top", "--rotate", "-40,0,-30", "--perspective", "--zoom", "0.9"]}
    out = []
    for name, args in shots.items():
        path = FAB / f"{PROJECT}-{name}.png"
        cli("pcb", "render", *args, "--quality", "high", "--background", "opaque", "-w", "1600", "-h", "1400",
            "-o", path, PCB)
        out.append(path)
    return out


def main():
    FAB.mkdir(exist_ok=True)
    REPORTS.mkdir(exist_ok=True)
    print("design")
    script("footprints.py")
    script("schematic.py")
    erc()
    script("board.py")
    drc()
    print("fabrication files")
    print(f"  {gerbers().name}")
    print(f"  {bom().name}")
    path, n = cpl()
    print(f"  {path.name} ({n} parts)")
    for p in pdfs() + tuple(renders()):
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
