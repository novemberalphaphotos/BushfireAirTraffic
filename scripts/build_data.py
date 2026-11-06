#!/usr/bin/env python3
"""Rebuild site/data/aircraft.json from the spreadsheet.

Run after editing the spreadsheet or adding photos:
    pip install openpyxl
    python3 scripts/build_data.py

Exact base positions: in the "Base Coordinates" column put a Google Maps link to the exact
spot (right-click the spot in Google Maps > Share > Copy link), or type "lat, lon" such as
-33.9244, 150.9883. If the cell is empty the built-in approximate list below is used.
The newest .xlsx in data/ is the one that is read.

Photos: save a file named after the registration into site/photos/, e.g.
    site/photos/VH-EPH.jpg   (.jpg .jpeg .png .webp all work)
Several shots per aircraft: VH-EPH.jpg, VH-EPH-2.jpg, VH-EPH-3.jpg ...
A value in the sheet's "Photo URL" column is used when there is no local file.
"""
import json, time, re, sys, urllib.parse, urllib.request
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "maps_cache.json"
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


def newest_xlsx():
    files = sorted(DATA.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = [f for f in files if not f.name.startswith("~$")]
    if not files:
        sys.exit("No .xlsx file found in data/")
    return files[0]


def in_australia(lat, lon):
    return -45 <= lat <= -9 and 112 <= lon <= 155


COORD_PATTERNS = [
    r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)",                                   # the dropped pin itself
    r"[?&](?:q|ll|query|destination)=(-?\d+\.\d+)(?:,|%2C)(-?\d+\.\d+)",
    r"@(-?\d+\.\d+),(-?\d+\.\d+)",                                       # map centre
    r"center=(-?\d+\.\d+)(?:,|%2C)(-?\d+\.\d+)",
    r"/(?:place|search|dir|maps)/[^@?]*?/?(-?\d{1,2}\.\d+)(?:,|%2C)\+?(-?\d{2,3}\.\d+)",   # coordinates written into the path
    r"!1d(-?\d{2,3}\.\d+)!2d(-?\d{1,2}\.\d+)",                           # place data: longitude first
    r"\[null,null,(-?\d+\.\d+),(-?\d+\.\d+)\]",                          # coordinates inside the page data
]


def coords_from_text(text):
    for rx in COORD_PATTERNS:
        for m in re.finditer(rx, text):
            a, b = float(m[1]), float(m[2])
            if in_australia(a, b):
                return a, b
            if in_australia(b, a):      # longitude was written first
                return b, a
    return None


def resolve_map_link(url):
    """Follow a Google Maps (short) link and pull the pin coordinates out of it."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; bushfire-air-tracker)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        final, body = r.geturl(), r.read(600000).decode("utf-8", "ignore")
    got = coords_from_text(urllib.parse.unquote(final)) or coords_from_text(final) or coords_from_text(body)
    if not got:
        print(f"  opened {url} but found no coordinates. It ended at: {final[:220]}")
    return got


def main():
    xlsx = newest_xlsx()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    wb = openpyxl.load_workbook(xlsx)
    ws = wb["Aircraft Data"]
    out, missing, seen, unresolved = [], set(), set(), []
    exact = 0
    fails = 0
    for row in ws.iter_rows(min_row=2, max_col=7):
        r = [c.value for c in row]
        if not any(r):
            continue
        rego, callsign, typ, op, base, coord_text, photo_url = [
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
            pin = None
            link = row[5].hyperlink.target if row[5].hyperlink and row[5].hyperlink.target else None
            m = re.match(r"^\s*(-?\d+\.\d+)\s*[, ]\s*(-?\d+\.\d+)\s*$", coord_text)
            if m and in_australia(float(m[1]), float(m[2])):
                pin = (float(m[1]), float(m[2]))
            elif link and "goo" in link:
                if link not in cache:
                    if fails >= 8:            # the network is blocked or Google is refusing: stop trying this run
                        cache[link] = None
                    else:
                        try:
                            try:
                                cache[link] = resolve_map_link(link)
                            except Exception:
                                time.sleep(2)               # one polite retry
                                cache[link] = resolve_map_link(link)
                            time.sleep(0.4)
                        except Exception as e:
                            fails += 1
                            print(f"  could not open {link}: {e}")
                            cache[link] = None
                if cache[link]:
                    pin = tuple(cache[link])
                else:
                    unresolved.append((rego, link))
                    cache.pop(link, None)   # try again next time
            if pin:
                exact += 1
                name = b[2] if b else re.sub(r",?\s*\b(NSW|VIC|QLD|TAS|WA|SA|ACT|NT)\b\.?$", "", base).strip()
                state = b[3] if b else (re.search(r"\b(NSW|VIC|QLD|TAS|WA|SA|ACT|NT)\b\.?$", base) or [None, ""])[1]
                entry["base"] = {"name": name, "state": state, "lat": pin[0], "lon": pin[1], "exact": True}
            elif b:
                entry["base"] = {"name": b[2], "state": b[3], "lat": b[0], "lon": b[1]}
            else:
                missing.add(base)
                entry["baseText"] = base
        out.append(entry)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    CACHE.write_text(json.dumps({k: v for k, v in cache.items() if v}, indent=1))
    based = sum(1 for e in out if e["base"])
    print(f"{xlsx.name}: {len(out)} aircraft, {based} with a mapped base ({exact} from exact pins), "
          f"{sum(1 for e in out if e['photos'])} with photos -> {OUT.relative_to(ROOT)}")
    if unresolved:
        print(f"{len(unresolved)} map links could not be read; those aircraft use the approximate base position.")
    if missing:
        print("Bases with no coordinates (add a map link in the sheet, or add them to BASES):", sorted(missing))
        print("Those aircraft are listed without a map position; the site still publishes.")


if __name__ == "__main__":
    main()
