#!/usr/bin/env python3
"""Rebuild site/data/aircraft.json from the spreadsheet.

Run after editing the spreadsheet or adding photos:
    pip install openpyxl
    python3 scripts/build_data.py

Photos: save a file named after the registration into site/photos/, e.g.
    site/photos/VH-EPH.jpg   (.jpg .jpeg .png .webp all work)
Several shots per aircraft: VH-EPH.jpg, VH-EPH-2.jpg, VH-EPH-3.jpg ...
A value in the sheet's "Photo URL" column is used when there is no local file.
"""
import json, re, sys
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "data" / "2026_Fire_Season.xlsx"
OUT = ROOT / "site" / "data" / "aircraft.json"
PHOTOS = ROOT / "site" / "photos"

# Base name (lower case, no state) -> (lat, lon, display name, state).
# Coordinates are approximate (airport reference points or town centres);
# edit freely, or add new bases here when the spreadsheet gains them.
BASES = {
    "hume base": (-35.38, 149.23, "Hume Base", "ACT"),
    "armidale airport": (-30.5281, 151.6178, "Armidale Airport", "NSW"),
    "bairnsdale airport": (-37.8878, 147.5682, "Bairnsdale Airport", "VIC"),
    "ballarat airport": (-37.5122, 143.7914, "Ballarat Airport", "VIC"),
    "bankstown airport": (-33.9244, 150.9883, "Bankstown Airport", "NSW"),
    "bundaberg airport": (-24.9039, 152.3189, "Bundaberg Airport", "QLD"),
    "cambridge airport": (-42.8361, 147.5058, "Cambridge Airport", "TAS"),
    "camden airport": (-34.0403, 150.6872, "Camden Airport", "NSW"),
    "casino airport": (-28.8836, 153.0675, "Casino Airport", "NSW"),
    "casterson fire base": (-37.5833, 141.4167, "Casterson Fire Base", "VIC"),
    "cessnock airport": (-32.7875, 151.3419, "Cessnock Airport", "NSW"),
    "clermont": (-22.82, 147.64, "Clermont", "QLD"),
    "coffs harbour airport": (-30.3206, 153.1164, "Coffs Harbour Airport", "NSW"),
    "colac airport": (-38.2778, 143.5819, "Colac Airport", "VIC"),
    "cooma polo flats": (-36.22, 149.14, "Cooma Polo Flats", "NSW"),
    "cooranbong base": (-33.07, 151.45, "Cooranbong Base", "NSW"),
    "devonport base": (-41.1697, 146.4297, "Devonport Base", "TAS"),
    "dubbo airport": (-32.2167, 148.5747, "Dubbo Airport", "NSW"),
    "essendon airport": (-37.7281, 144.9019, "Essendon Airport", "VIC"),
    "hamilton airport": (-37.6489, 142.0653, "Hamilton Airport", "VIC"),
    "heyfield": (-37.9833, 146.7833, "Heyfield", "VIC"),
    "jandakot airport": (-32.0975, 115.8811, "Jandakot Airport", "WA"),
    "kempsey airport": (-31.0744, 152.7703, "Kempsey Airport", "NSW"),
    "latrobe valley airport": (-38.2072, 146.4703, "Latrobe Valley Airport", "VIC"),
    "leongatha airport": (-38.4917, 145.95, "Leongatha Airport", "VIC"),
    "mangalore airport": (-36.8886, 145.1831, "Mangalore Airport", "VIC"),
    "mansfield airport": (-37.0647, 146.0975, "Mansfield Airport", "VIC"),
    "moorabbin airport": (-37.9758, 145.1022, "Moorabbin Airport", "VIC"),
    "oberon base": (-33.7, 149.85, "Oberon Base", "NSW"),
    "off airport casino": (-28.86, 153.05, "Off Airport (Casino)", "NSW"),
    "orange airport": (-33.3817, 149.1328, "Orange Airport", "NSW"),
    "ovens": (-36.59, 146.75, "Ovens", "VIC"),
    "penrith base": (-33.75, 150.7, "Penrith Base", "NSW"),
    "port lincoln airport": (-34.6053, 135.8803, "Port Lincoln Airport", "SA"),
    "raaf gingin": (-31.4633, 115.8725, "RAAF Gingin", "WA"),
    "raaf richmond": (-33.6006, 150.7811, "RAAF Richmond", "NSW"),
    "salamander bay": (-32.723, 152.085, "Salamander Bay", "NSW"),
    "serpentine airport": (-32.375, 115.975, "Serpentine Airport", "WA"),
    "stawell airport": (-37.0719, 142.7414, "Stawell Airport", "VIC"),
    "tumut": (-35.3, 148.22, "Tumut", "NSW"),
    "warnervale airport": (-33.2033, 151.4531, "Warnervale Airport", "NSW"),
    "wollongong heliport": (-34.5611, 150.7886, "Wollongong Heliport", "NSW"),
}

UNKNOWN = {"", "not confirmed", "tbc", "tba", "n/a"}

FAMILIES = [  # (regex on type, family label); first match wins
    (r"^(AS[ .\-]?)?350", "AS350 Squirrel"),
    (r"^AS[ .\-]?355", "AS355 Twin Squirrel"),
    (r"^AS[ .\-]?365", "AS365 Dauphin"),
    (r"^206", "Bell 206"),
    (r"^407", "Bell 407"),
    (r"^(AB)?412", "Bell 412"),
    (r"^212", "Bell 212"),
    (r"^214", "Bell 214"),
    (r"^(205|204|UH-1)", "Bell 204/205/UH-1"),
    (r"^(UH|EH)-60", "UH-60 Black Hawk"),
    (r"^(MBB-)?BK ?117", "BK117"),
    (r"^BO ?105", "BO105"),
    (r"^S ?61", "S-61"),
    (r"^S ?64", "S-64 Aircrane"),
    (r"^CH-47", "CH-47 Chinook"),
    (r"^EC ?130", "EC130"),
    (r"^EC ?135", "EC135"),
    (r"^(AS ?332|EC ?225)", "Super Puma"),
    (r"^(A|AW)119", "AW119"),
    (r"^42[79]", "Bell 427/429"),
    (r"^369", "MD 500"),
]


def norm_base(text):
    t = text.lower()
    t = re.sub(r"[,.]?\s*\b(nsw|vic|qld|tas|wa|sa|act|nt)\b\.?$", "", t.strip())
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def family(t):
    for rx, label in FAMILIES:
        if re.search(rx, t.strip(), re.I):
            return label
    return "Other"


def clean_type(t):
    t = t.strip()
    return t[:-2] if t.endswith(".0") else t  # '212.0' -> '212'


def photos_for(rego):
    found = []
    for ext in ("jpg", "jpeg", "png", "webp"):
        for p in sorted(PHOTOS.glob(f"{rego}.{ext}")) + sorted(PHOTOS.glob(f"{rego}-*.{ext}")):
            found.append(f"photos/{p.name}")
    return found


def main():
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb["Aircraft Data"]
    out, missing, seen = [], set(), set()
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not any(r[:7]):
            continue
        rego, callsign, typ, op, base, _coords, photo_url = [
            (str(x).strip() if x is not None else "") for x in r[:7]
        ]
        if not rego:
            continue
        rego = rego.upper()
        if (rego, callsign.upper()) in seen:  # exact duplicate row
            continue
        seen.add((rego, callsign.upper()))
        note = ""
        if op.lower() in UNKNOWN:
            op = ""
        elif op.startswith("*"):  # a note-to-self in the sheet
            note, op = op.strip("* "), ""
        entry = {
            "rego": rego,
            "callsign": callsign.upper(),
            "group": re.sub(r"[\s\d]+$", "", callsign.upper()),
            "type": clean_type(typ),
            "family": family(clean_type(typ)),
            "operator": op,
            "base": None,
            "photos": photos_for(rego) or ([photo_url] if photo_url else []),
        }
        if note:
            entry["note"] = note
        if base.lower() not in UNKNOWN:
            b = BASES.get(norm_base(base))
            if b:
                entry["base"] = {"name": b[2], "state": b[3], "lat": b[0], "lon": b[1]}
            else:
                missing.add(base)
                entry["baseText"] = base
        out.append(entry)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    based = sum(1 for e in out if e["base"])
    print(f"{len(out)} aircraft, {based} with a mapped base, "
          f"{sum(1 for e in out if e['photos'])} with photos -> {OUT.relative_to(ROOT)}")
    if missing:
        print("Bases with no coordinates (add them to BASES):", sorted(missing))
        sys.exit(1)


if __name__ == "__main__":
    main()
