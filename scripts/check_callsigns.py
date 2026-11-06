#!/usr/bin/env python3
"""Watch the NAFC call sign list and push a notification when it changes.

Run by GitHub Actions every couple of hours (see .github/workflows/watch-and-deploy.yml).
Needs only Python 3, no extra packages.

Environment:
  NTFY_TOPIC      ntfy.sh topic to notify (secret). Unset = print only.
  SITE_URL        optional, opened when you tap the notification.
  NAFC_HTML_FILE  optional, parse this local file instead of fetching (for testing).
  NTFY_DRY_RUN    optional, print the notification instead of sending it.
"""
import json, os, re, sys, urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://www.nafc.org.au/call-signs/"
STATE = ROOT / "site" / "data" / "nafc_callsigns.json"
FLEET = ROOT / "site" / "data" / "aircraft.json"
FIELDS = ["callsign", "registration", "category", "make", "model"]


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def parse_table(html):
    p = TableParser()
    p.feed(html)
    entries, cols = [], None
    for row in p.rows:
        low = [c.lower() for c in row]
        if "registration" in low and any("call" in c for c in low):
            cols = {}
            for name in FIELDS:
                for i, c in enumerate(low):
                    if (name == "callsign" and "call" in c) or c == name:
                        cols[name] = i
                        break
            continue
        if cols and len(row) > max(cols.values()):
            e = {k: row[i] for k, i in cols.items()}
            if e.get("callsign") and e.get("registration"):
                entries.append(e)
    return entries


REG_RX = re.compile(r"^(?:[A-Z0-9]{1,2}-[A-Z0-9]{2,5}|N\d{1,5}[A-Z]{0,2})$")   # VH-ABC, P2-MLJ, C-FXEC, ZK-ABC, N123AB
STATE_VERSION = 2   # bump when the parser changes, so the saved list is re-read quietly instead of alerting


def parse_text(html):
    """Fallback for pages that are not a <table>: one cell per line, registration identifies the row."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</(td|th|tr|p|div|li|span|dd|dt)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.split("\n")]
    lines = [l for l in lines if l]
    entries = []
    for i, l in enumerate(lines):
        if REG_RX.match(l.upper()) and i >= 1 and re.search(r"\d", lines[i - 1]) and re.match(r"^[A-Za-z]", lines[i - 1]):
            rest = lines[i + 1:i + 4] + ["", "", ""]
            entries.append({"callsign": lines[i - 1], "registration": l, "category": rest[0], "make": rest[1], "model": rest[2]})
    return entries


def parse(html):
    entries = parse_table(html) or parse_text(html)
    out = []
    for e in entries:
        e["callsign"] = re.sub(r"\s+", " ", e["callsign"]).strip().upper()
        e["registration"] = e["registration"].strip().upper()
        if e["callsign"] and REG_RX.match(e["registration"]):
            out.append(e)
    return out


def describe(html):
    return (f"{len(html)} bytes, {len(re.findall(r'(?i)<table', html))} tables, {len(re.findall(r'(?i)<tr', html))} rows; "
            f"starts: {re.sub(chr(10), ' ', html[:200])!r}")


def fetch():
    local = os.environ.get("NAFC_HTML_FILE")
    if local:
        return Path(local).read_text(encoding="utf-8")
    req = urllib.request.Request(URL, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-AU,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        print("NAFC page: HTTP", r.status)
        return r.read().decode("utf-8", "replace")


def key(e):
    return (e["callsign"], e["registration"])


def diff(old, new):
    """Return human-readable change lines as (registration, text)."""
    o_reg, n_reg, o_cs, n_cs = {}, {}, {}, {}
    for e in old:
        o_reg.setdefault(e["registration"], set()).add(e["callsign"]); o_cs.setdefault(e["callsign"], set()).add(e["registration"])
    for e in new:
        n_reg.setdefault(e["registration"], set()).add(e["callsign"]); n_cs.setdefault(e["callsign"], set()).add(e["registration"])
    out = []
    for reg in sorted(set(o_reg) | set(n_reg)):
        a, b = o_reg.get(reg, set()), n_reg.get(reg, set())
        if a == b:
            continue
        if a and b:
            out.append((reg, f"{reg}: {', '.join(sorted(a))} -> {', '.join(sorted(b))}"))
        elif b:
            out.append((reg, f"{reg}: added as {', '.join(sorted(b))}"))
        else:
            out.append((reg, f"{reg}: removed (was {', '.join(sorted(a))})"))
    return out


def notify(title, body, high=False):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("ntfy: NTFY_TOPIC secret is empty, so nothing can be sent. Check the secret name and value.")
    if not topic or os.environ.get("NTFY_DRY_RUN"):
        print(f"--- notification (not sent) ---\n{title}\n{body}\n")
        return
    headers = {"Title": title.encode("ascii", "replace").decode(), "Priority": "high" if high else "default", "Tags": "helicopter"}
    if os.environ.get("SITE_URL"):
        headers["Click"] = os.environ["SITE_URL"]
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"ntfy: sent to topic '{topic[:4]}...' (HTTP {r.status})")
    except Exception as e:
        print("ntfy: FAILED to send:", e)


def main():
    if os.environ.get("NTFY_TEST"):
        notify("NAFC watcher test", "If you can read this, notifications from GitHub reach your phone.")
    html = ""
    try:
        html = fetch()
        new = parse(html)
    except Exception as e:
        notify("NAFC watcher error", f"Could not read the call sign list: {e}")
        print("fetch failed:", e); return 1
    old = None
    if STATE.exists():
        saved = json.loads(STATE.read_text())
        if saved.get("version") == STATE_VERSION:
            old = saved["entries"]
        else:
            print("saved list was made by an older version of this script: re-reading it as a new baseline")

    if len(new) < 20 or (old and len(new) < 0.5 * len(old)):
        info = describe(html)
        notify("NAFC watcher error", f"Parsed only {len(new)} rows (had {len(old or [])}). Nothing was updated. Page: {info}"[:900])
        print("parse looks wrong:", len(new), "rows.", info); return 1

    new = sorted({key(e): e for e in new}.values(), key=key)
    if old is None:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"version": STATE_VERSION, "entries": new}, indent=1))
        notify("NAFC watcher started", f"Baseline saved: {len(new)} call signs. You will be told when any change.")
        print("baseline saved"); return 0

    old = sorted({key(e): e for e in old}.values(), key=key)
    changes = diff(old, new)
    if not changes and old == new:
        print("no change"); return 0

    STATE.write_text(json.dumps({"version": STATE_VERSION, "entries": new}, indent=1))
    mine = {e["rego"] for e in json.loads(FLEET.read_text())} if FLEET.exists() else set()
    tracked = [t for r, t in changes if r in mine]
    other = [t for r, t in changes if r not in mine]
    lines = [f"YOUR FLEET ({len(tracked)})"] + tracked[:25] if tracked else []
    if len(tracked) > 25:
        lines.append(f"+{len(tracked) - 25} more")
    if other:
        lines += ([""] if lines else []) + [f"OTHER AIRCRAFT ({len(other)})"] + other[:10]
        if len(other) > 10:
            lines.append(f"+{len(other) - 10} more")
    if not changes:  # same registrations, other fields changed (category, make, model)
        lines = ["Details changed on the NAFC list (category, make or model). No call sign changes."]
    title = f"NAFC call signs: {len(tracked)} change{'s' if len(tracked) != 1 else ''} in your fleet" if tracked else f"NAFC call signs: {len(other)} change{'s' if len(other) != 1 else ''} (none in your fleet)"
    notify(title, "\n".join(lines)[:3800], high=bool(tracked))
    print(f"{len(changes)} changes"); return 0


if __name__ == "__main__":
    sys.exit(main())
