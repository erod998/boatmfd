"""DigiKey BOMs for the sensor board and the LED board, priced.

DigiKey's BOM upload wants one orderable part number per line, and it can't match what the
boards' own BOMs carry in places: a generic part name (SS14, BSS138), an "or equivalent" note
(the GPIO stacking header), and the wiring plugs that pair with the board's pluggable
connectors, which are mentioned only in the notes. This reads each board's PCBWay BOM
(fab/pcbway/*-pcbway-bom.csv, which build.py writes), and writes a DigiKey BOM next to it:
  * the parts DigiKey can't match are replaced with its exact orderable part (CHOICES below);
  * each pluggable header's mating plug gets its own line (J7's too, if you want one: see
    NO_PLUG);
  * each line gets DigiKey's part number, its stock, and the price at that quantity.

The prices are DigiKey's, read from findchips.com, one page every 10 s as its robots.txt asks.
Pages are cached in .digikey-cache/, so running this again only fetches parts it hasn't seen.

    python digikey_bom.py            [QTY_BOARDS, default 1]
"""
import csv
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".digikey-cache")
BOARDS = ["sensor-board", "led-board"]
CRAWL_DELAY = 10.0

# The exact orderable part where the board's BOM names a generic part or an equivalent.
# (manufacturer, part number, what to search for)
CHOICES = {
    "2x20 female stacking header, 2.54 mm, extra-tall": ("Adafruit Industries LLC", "1979", None),
    "SS14": ("onsemi", "SS14", "SS14"),
    "BSS138": ("onsemi", "BSS138", "BSS138"),
    "RP2040": ("Raspberry Pi", "SC0914(13)", "SC0914(13)"),
    # DigiKey sells these as the automotive-grade twin, or not in this packaging: the same part.
    "NUP2105LT1G": ("onsemi", "SZNUP2105LT1G", "NUP2105LT1G"),
    # Yageo 1 % resistors DigiKey doesn't list: Panasonic's, same size, tolerance and rating.
    "RC0805FR-071KL": ("Panasonic", "ERJ-6ENF1001V", "ERJ-6ENF1001V"),
    "RC0805FR-07120RL": ("Panasonic", "ERJ-6ENF1200V", "ERJ-6ENF1200V"),
    "RC0805FR-075K1L": ("Panasonic", "ERJ-6ENF5101V", "ERJ-6ENF5101V"),
    "RC0402FR-0727RL": ("Panasonic", "ERJ-2RKF27R0X", "ERJ-2RKF27R0X"),
    # DigiKey sells the EL817 only by the 4500-piece reel. Lite-On's PC817-type part is the same
    # pinout (1 anode, 2 cathode, 3 emitter, 4 collector) and gull-wing package, with the same
    # CTR rank (C: 200-400 %).
    "EL817S1(C)(TU)-F": ("Lite-On", "LTV-817S-TA1-C", "LTV-817S"),
}
# Parts findchips can't price: the price to budget, and where it's from.
MANUAL = {
    "1979": (2.50, "Adafruit list price; DigiKey carries Adafruit 1979"),
}
# Distributors that sell for the manufacturer (not brokers), for when DigiKey has no small-quantity price.
AUTHORIZED = {"DigiKey", "Mouser Electronics", "Newark", "Arrow Electronics", "TME", "Avnet Americas",
              "Future Electronics", "Powell Electronics", "Microchip Technology Inc", "Texas Instruments", "RS"}
# Headers whose plug isn't needed: the sensor board's J7 (5 V in) isn't used on the boat, where
# the Pi 5 runs from its own USB-C.
NO_PLUG = {("sensor-board", "J7")}


def fetch(query):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, re.sub(r"[^A-Za-z0-9._-]", "_", query) + ".html")
    if os.path.exists(path):
        return open(path, encoding="utf-8", errors="replace").read()
    url = "https://www.findchips.com/search/" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (boat-dashboard BOM pricing; 1 request/10 s)"})
    time.sleep(CRAWL_DELAY)
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode("utf-8", errors="replace")
    open(path, "w", encoding="utf-8").write(page)
    return page


def offers(page):
    """Every listing on a findchips page: distributor, part number, stock, price breaks, and
    DigiKey's own part number where it's DigiKey's."""
    rows = []
    for m in re.finditer(r'<tr[^>]*data-distributor_name="([^"]+)"[^>]*>', page):
        attr = dict((k, html.unescape(v)) for k, v in re.findall(r'data-([a-z_]+)="([^"]*)"', m.group(0)))
        try:
            breaks = sorted((int(q), float(p)) for q, _, p in json.loads(attr.get("price", "[]")))
        except (ValueError, TypeError):
            breaks = []
        rows.append({"dist": m.group(1), "mpn": attr.get("mfrpartnumber", ""), "stock": int(attr.get("instock") or 0),
                     "dk": attr.get("distino", "") if m.group(1) == "DigiKey" else "", "breaks": breaks})
    return rows


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def price_at(breaks, qty):
    buy = max(qty, breaks[0][0])
    return buy, [p for q, p in breaks if q <= buy][-1]


def best(rows, mpn, qty):
    """DigiKey's part number and its price for `qty` if it sells that few (findchips' DigiKey
    stock figures lag, so they're shown, not trusted); otherwise the cheapest authorized
    distributor with stock that sells that few."""
    exact = [r for r in rows if norm(r["mpn"]) == norm(mpn) and r["breaks"]]
    dk_all = [r for r in rows if r["dist"] == "DigiKey" and norm(r["mpn"]) == norm(mpn)]
    dk_pn = next((r["dk"] for r in dk_all if r["dk"].endswith(("CT-ND", "-1-ND"))), dk_all[0]["dk"] if dk_all else "")
    dk_stock = max((r["stock"] for r in dk_all), default=None)
    small = [r for r in exact if r["dist"] == "DigiKey" and r["breaks"][0][0] <= max(qty, 10)]
    if not small:
        small = [r for r in exact if r["dist"] in AUTHORIZED and r["breaks"][0][0] <= max(qty, 10) and r["stock"] >= qty]
    if not small:
        return {"dk": dk_pn, "dk_stock": dk_stock, "src": None}
    choice = []
    for r in small:
        buy, unit = price_at(r["breaks"], qty)
        choice.append((buy * unit, r, buy, unit))
    ext, r, buy, unit = min(choice, key=lambda c: c[0])
    return {"dk": dk_pn, "dk_stock": dk_stock, "src": r["dist"], "buy": buy, "unit": unit, "ext": ext}


def lines_for(board, boards_qty):
    """The DigiKey lines for one board: its parts, then the plugs for its headers."""
    path = os.path.join(HERE, board, "fab", "pcbway", f"{board}-pcbway-bom.csv")
    out = []
    plugs = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        mpn, mfr = r["*Mfg Part #"].strip(), r["Manufacturer"].strip()
        qty = int(r["*Qty"]) * boards_qty
        refs = r["*Designator"]
        search = mpn
        if mpn in CHOICES:
            mfr, mpn, search = CHOICES[mpn]
        out.append({"refs": refs, "qty": qty, "mfr": mfr, "mpn": mpn, "search": search,
                    "desc": f'{r["Description / Value"]} ({r["*Package/Footprint"][:40]})'})
        m = re.search(r"mating plug (\d+)", r.get("Your Instructions / Notes", ""))
        if m:
            wanted = [d for d in refs.split(",") if (board, d.strip()) not in NO_PLUG]
            if wanted:
                p = plugs.setdefault(m.group(1), {"refs": [], "qty": 0})
                p["refs"] += [d.strip() + " plug" for d in wanted]
                p["qty"] += len(wanted) * boards_qty
    for mpn, p in plugs.items():
        out.append({"refs": ",".join(p["refs"]), "qty": p["qty"], "mfr": "Phoenix Contact", "mpn": mpn, "search": mpn,
                    "desc": "mating plug, for the wiring (not on the board)"})
    # One line per part, for the quantity price and a shorter upload (the PCBWay BOM lists each
    # connector on its own line, for its label).
    merged = {}
    for ln in out:
        m = merged.get(ln["mpn"])
        if m:
            m["qty"] += ln["qty"]
            m["refs"] += "," + ln["refs"]
        else:
            merged[ln["mpn"]] = dict(ln)
    return list(merged.values())


def main():
    boards_qty = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    grand = 0.0
    for board in BOARDS:
        lines = lines_for(board, boards_qty)
        total, notes = 0.0, []
        for ln in lines:
            if ln["mpn"] in MANUAL:
                unit, why = MANUAL[ln["mpn"]]
                ln["pick"] = {"dk": "", "dk_stock": None, "src": why, "buy": ln["qty"], "unit": unit, "ext": unit * ln["qty"]}
            else:
                ln["pick"] = best(offers(fetch(ln["search"])), ln["mpn"], ln["qty"])
            p = ln["pick"]
            if p["src"] is None:
                notes.append(f"    NO AUTHORIZED STOCK: {ln['mfr']} {ln['mpn']} ({ln['refs']})")
                continue
            total += p["ext"]
            if p["src"] != "DigiKey":
                notes.append(f"    from {p['src']}, not DigiKey: {ln['mpn']} ({ln['refs']}) ${p['unit']:.2f}")
        path = os.path.join(HERE, board, "fab", f"{board}-digikey-bom.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Digi-Key Part Number", "Manufacturer", "Manufacturer Part Number", "Quantity",
                        "Customer Reference", "Description", "Price From", "Unit Price (USD)", "Extended Price (USD)"])
            for ln in lines:
                p = ln["pick"]
                priced = p["src"] is not None
                w.writerow([p["dk"], ln["mfr"], ln["mpn"], p["buy"] if priced else ln["qty"], ln["refs"], ln["desc"],
                            p["src"] or "NO AUTHORIZED STOCK", f'{p["unit"]:.4f}' if priced else "",
                            f'{p["ext"]:.2f}' if priced else ""])
        grand += total
        print(f"{board} x{boards_qty}: {len(lines)} lines, ${total:.2f} -> {os.path.relpath(path, HERE)}")
        if notes:
            print("\n".join(notes))
    print(f"both boards: ${grand:.2f}")


if __name__ == "__main__":
    main()
