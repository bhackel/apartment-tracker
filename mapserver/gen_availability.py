#!/usr/bin/env python3
"""
Generate a static availability.json for the GitHub Pages map.

Runs the apartment scraper live (server-side; no browser, no ntfy, no snapshot
log) and writes the same {timestamp, source, blocks} shape the local server's
/api/availability returns — so the Pages frontend is identical to the local one.

Usage: python gen_availability.py [--out ../docs/availability.json] [--snapshot]
  --snapshot  use the latest row of snapshots.csv instead of scraping live.
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)   # server.py
sys.path.insert(0, ROOT)   # apartment_tracker.py

import server  # noqa: E402


def main():
    args = sys.argv[1:]
    out = args[args.index("--out") + 1] if "--out" in args else os.path.join(ROOT, "docs", "availability.json")

    if "--snapshot" in args:
        rows, ts = server.rows_from_snapshot()
        source = "snapshot"
    else:
        import apartment_tracker as at  # needs `requests`
        units, failed = at.scrape_all(at.property_urls(), use_browser=False)
        at.apply_known_specials(units)
        rows = [u for u in units if "towers-at-costa-verde" in (u.get("url") or "")]
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        source = "live"
        if not rows:
            print("!! scrape returned no Towers units; refusing to overwrite", file=sys.stderr)
            return 1

    doc = {"timestamp": ts, "source": source, "generated_at":
           datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "blocks": server.group_units(rows)}
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    total = sum(b["count"] for b in doc["blocks"].values())
    print(f"wrote {out}: {total} units, source={source}, ts={ts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
