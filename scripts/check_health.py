#!/usr/bin/env python3
"""Tell the phone if the Cloudflare recorder stops working.

Runs in GitHub Actions next to the NAFC watcher. It asks the worker how recently it ran and when the
ADS-B feeds last answered, and sends one notification when things go wrong and one when they recover.

Environment: NTFY_TOPIC, SITE_URL (optional), WORKER_URL (optional, otherwise read from site/config.js),
             NTFY_DRY_RUN (print instead of sending).
"""
import json, os, re, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "health_state.json"
STALE_RUN = 15 * 60      # recorder should run every minute and writes a heartbeat every 5
STALE_FEED = 30 * 60     # feeds silent this long = feed problem


def worker_url():
    if os.environ.get("WORKER_URL"):
        return os.environ["WORKER_URL"]
    cfg = ROOT / "site" / "config.js"
    m = re.search(r'proxy:\s*"([^"]+)"', cfg.read_text()) if cfg.exists() else None
    return m.group(1) if m else ""


def notify(title, body, high=False):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("ntfy: NTFY_TOPIC secret is empty, so nothing can be sent.")
    if not topic or os.environ.get("NTFY_DRY_RUN"):
        print(f"--- notification (not sent) ---\n{title}\n{body}\n")
        return
    headers = {"Title": title.encode("ascii", "replace").decode(), "Priority": "high" if high else "default", "Tags": "warning" if high else "white_check_mark"}
    if os.environ.get("SITE_URL"):
        headers["Click"] = os.environ["SITE_URL"]
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"ntfy: sent (HTTP {r.status})")
    except Exception as e:
        print("ntfy: FAILED to send:", e)


def mins(s):
    return f"{round(s / 60)} min" if s < 5400 else f"{s / 3600:.1f} h"


def problems(h):
    """List of plain-English problems, empty when all is well."""
    now = h["now"]
    out, lr, lf = [], h.get("last_run"), h.get("last_feed_ok")
    if lr is None:
        return out                       # nothing recorded yet: just deployed, give it a few minutes
    if now - lr > STALE_RUN:
        out.append(f"The recorder has not run for {mins(now - lr)}. Check the worker's cron trigger and its Logs in Cloudflare.")
    elif lf is None:
        out.append("The recorder is running but no ADS-B feed has ever answered it.")
    elif now - lf > STALE_FEED:
        out.append(f"No ADS-B feed has answered for {mins(now - lf)}. The feeds may be blocking Cloudflare.")
    if h.get("last_error") and h.get("last_error_t") and now - h["last_error_t"] < 1800:
        out.append(f"Last error: {h['last_error']}")
    return out


def main():
    url = worker_url()
    if not url:
        print("No worker address (site/config.js proxy is empty): nothing to check."); return 0
    try:
        req = urllib.request.Request(url.rstrip("/") + "/?health", headers={"User-Agent": "bushfire-air-tracker-health"})
        with urllib.request.urlopen(req, timeout=30) as r:
            h = json.loads(r.read())
        probs = problems(h)
    except Exception as e:
        probs = [f"The worker could not be reached: {e}"]
        h = None

    prev = json.loads(STATE.read_text()) if STATE.exists() else {"bad": False}
    if h and not probs:
        lf = h.get("last_fix")
        print("recorder OK;", f"last position recorded {mins(h['now'] - lf)} ago" if lf else "no positions recorded yet")
    for p in probs:
        print("PROBLEM:", p)

    if probs and not prev.get("bad"):
        notify("Recorder problem", "\n".join(probs), high=True)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"bad": True, "since": int(time.time())}))
    elif not probs and prev.get("bad"):
        notify("Recorder is back", "The recorder and ADS-B feeds are working again.")
        STATE.write_text(json.dumps({"bad": False}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
