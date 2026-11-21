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
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URLS = ["https://nafc.org.au/call-signs/", "https://www.nafc.org.au/call-signs/"]   # NAFC moved to a new site; try both
STATE = ROOT / "site" / "data" / "nafc_callsigns.json"
STATUS = ROOT / "site" / "data" / "nafc_status.json"   # remembers if we are already in a failing state (so errors alert once, not every run)
FLEET = ROOT / "site" / "data" / "aircraft.json"
HISTORY = ROOT / "site" / "data" / "nafc_history.json"   # shown on the site as "Callsign changes"
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


def parse_rows_by_shape(html):
    """Fallback when table headers were renamed/removed: any row whose 2nd cell looks like a registration."""
    p = TableParser()
    p.feed(html)
    out = []
    for row in p.rows:
        if len(row) >= 2 and REG_RX.match(row[1].strip().upper()) and re.search(r"\d", row[0]):
            row = row + ["", "", ""]
            out.append({"callsign": row[0], "registration": row[1], "category": row[2], "make": row[3], "model": row[4]})
    return out


def parse(html):
    entries = parse_table(html) or parse_rows_by_shape(html) or parse_text(html)
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


def render_with_browser(url):
    """The new NAFC site loads its tables with JavaScript, so a plain download is an empty shell.
    Open the page in headless Chromium (installed by the workflow) and return the finished HTML."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                            locale="en-AU")
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                pg.wait_for_selector("table tbody tr", timeout=45000)
            except Exception:
                pass                      # report whatever did render
            pg.wait_for_timeout(1500)     # let the remaining tables finish loading
            html = pg.content()
            print(f"NAFC page rendered in browser: {len(html)} bytes")
            return html
        finally:
            b.close()


def fetch():
    local = os.environ.get("NAFC_HTML_FILE")
    if local:
        return Path(local).read_text(encoding="utf-8")
    last_err, best = None, ""
    for url in URLS:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-AU,en;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8", "replace")
                print(f"NAFC page {url}: HTTP {r.status}, final {r.geturl()}, {len(body)} bytes")
        except Exception as e:
            last_err = f"{url}: {e}"
            print("NAFC fetch failed:", last_err)
            continue
        if len(parse(body)) >= 20:
            return body
        best = best or body
    # plain download gave no data: the page is probably JavaScript-driven, so render it in a browser
    try:
        for url in URLS:
            html = render_with_browser(url)
            if len(parse(html)) >= 20:
                return html
            best = html if len(html) > len(best) else best
    except Exception as e:
        print("browser render failed:", type(e).__name__, str(e)[:200])
        last_err = f"browser render: {type(e).__name__}"
    if best:
        return best          # fetched, but nothing parsable: the caller reports what the page looked like
    raise RuntimeError(last_err or "no response")


def key(e):
    return (e["callsign"], e["registration"])


def diff(old, new):
    """Return one dict per registration whose call signs changed: {reg, old: [...], new: [...]}."""
    o_reg, n_reg = {}, {}
    for e in old:
        o_reg.setdefault(e["registration"], set()).add(e["callsign"])
    for e in new:
        n_reg.setdefault(e["registration"], set()).add(e["callsign"])
    out = []
    for reg in sorted(set(o_reg) | set(n_reg)):
        a, b = o_reg.get(reg, set()), n_reg.get(reg, set())
        if a != b:
            out.append({"reg": reg, "old": sorted(a), "new": sorted(b)})
    return out


def describe_change(c):
    if c["old"] and c["new"]:
        return f"{c['reg']}: {', '.join(c['old'])} -> {', '.join(c['new'])}"
    if c["new"]:
        return f"{c['reg']}: added as {', '.join(c['new'])}"
    return f"{c['reg']}: removed (was {', '.join(c['old'])})"


def save_history(changes, mine):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else {"since": now, "entries": []}
    for c in changes:
        hist["entries"].insert(0, {"t": now, "reg": c["reg"], "old": c["old"], "new": c["new"], "fleet": c["reg"] in mine})
    hist["entries"] = hist["entries"][:500]
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist, indent=1))


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


def problem(detail, run_msg):
    """Alert about a problem ONCE; stay quiet while it continues; say so when it clears."""
    st = json.loads(STATUS.read_text()) if STATUS.exists() else {}
    if not st.get("failing"):
        notify("NAFC watcher error", detail[:900] + " (You will not be alerted again until it recovers.)")
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps({"failing": True, "detail": detail[:300]}, indent=1))
    else:
        print("still failing, already alerted:", run_msg)


def recovered():
    if STATUS.exists() and json.loads(STATUS.read_text()).get("failing"):
        notify("NAFC watcher recovered", "The call sign list can be read again.")
        STATUS.write_text(json.dumps({"failing": False}, indent=1))


def main():
    if os.environ.get("NTFY_TEST"):
        notify("NAFC watcher test", "If you can read this, notifications from GitHub reach your phone.")
    html = ""
    try:
        html = fetch()
        new = parse(html)
    except Exception as e:
        problem(f"Could not read the call sign list: {e}", str(e))
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
        problem(f"Parsed only {len(new)} rows (had {len(old or [])}). Nothing was updated. Page: {info}", info)
        print("parse looks wrong:", len(new), "rows.", info); return 1
    recovered()

    new = sorted({key(e): e for e in new}.values(), key=key)
    if old is None:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"version": STATE_VERSION, "entries": new}, indent=1))
        if not HISTORY.exists():
            save_history([], set())
        notify("NAFC watcher started", f"Baseline saved: {len(new)} call signs. You will be told when any change.")
        print("baseline saved"); return 0

    old = sorted({key(e): e for e in old}.values(), key=key)
    changes = diff(old, new)
    if not changes and old == new:
        if not HISTORY.exists():
            save_history([], set())     # so the site shows the history button from the first run
        print("no change"); return 0

    STATE.write_text(json.dumps({"version": STATE_VERSION, "entries": new}, indent=1))
    mine = {e["rego"] for e in json.loads(FLEET.read_text())} if FLEET.exists() else set()
    save_history(changes, mine)
    tracked = [describe_change(c) for c in changes if c["reg"] in mine]
    other = [describe_change(c) for c in changes if c["reg"] not in mine]
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
