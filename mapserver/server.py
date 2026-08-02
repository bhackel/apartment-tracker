#!/usr/bin/env python3
"""
Towers at Costa Verde — interactive availability map server.

Serves a pan/zoom site map (North + South towers) with a dot on every unit
stack. Clicking a dot shows the live availability for that floorplan/stack,
pulled from the apartment tracker's snapshots.csv (or a fresh live run).

Unit numbering is FFSS: first two digits = floor, last two = stack (the block
number printed on the site map). So #0703 = floor 07, stack 03 (Catalina).

Run:  python3 server.py [--port 8899] [--host 0.0.0.0]
Then browse over Tailscale to http://<tailscale-ip>:8899/
"""
import csv
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKER_DIR = os.path.dirname(HERE)
SNAPSHOTS = os.path.join(TRACKER_DIR, "snapshots.csv")
TRACKER_PY = os.path.join(TRACKER_DIR, "apartment_tracker.py")
STATIC = os.path.join(HERE, "static")
DOTS_FILE = os.path.join(HERE, "dots.json")

# Stack number -> floorplan, read straight off the site map. Identical for both
# towers. Used so every stack shows its floorplan name even with zero vacancy.
STACK_FLOORPLAN = {
    "01": "Royale", "02": "Royale", "03": "Catalina", "04": "Pacifica",
    "05": "Catalina", "06": "Montecito", "07": "Cabrillo", "08": "Pacifica",
    "09": "Catalina", "10": "Catalina", "11": "Regency", "12": "Catalina",
    "13": "Catalina", "14": "Pacifica", "15": "Catalina", "16": "Montecito",
    "17": "Royale", "18": "Meridian", "19": "Royale",
}


def beds_of(s):
    import re
    m = re.match(r"\s*(\d+)", s or "")
    return int(m.group(1)) if m else 0


def money(s):
    s = (s or "").replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def concession_label(sp):
    sp = (sp or "").strip()
    if "1 1/2" in sp:
        return "1.5 mo free"
    if "2 Month" in sp:
        return "2 mo free"
    if "First Month" in sp or "1 Month" in sp:
        return "1 mo free"
    if "Off" in sp:
        return sp.replace(" at Move In", "")
    return "none"


def net_rent(price, sp):
    p = money(price)
    lab = concession_label(sp)
    factor = {"2 mo free": 10 / 12, "1.5 mo free": 10.5 / 12, "1 mo free": 11 / 12}.get(lab, 1.0)
    return round(p * factor)


def tower_of(url):
    return "North" if "north" in (url or "").lower() else ("North" if "towers-at-costa-verde" in (url or "") else "South")


def parse_tower_floorplan(fp):
    """'North Tower Catalina' -> ('North','Catalina'); handles 'with Den Option'."""
    fp = fp or ""
    tower = "North" if fp.startswith("North Tower") else ("South" if fp.startswith("South Tower") else "?")
    base = fp.replace("North Tower", "").replace("South Tower", "").strip()
    return tower, base


def rows_from_snapshot():
    """Latest logged snapshot rows (Towers only)."""
    if not os.path.exists(SNAPSHOTS):
        return [], None
    rows = list(csv.DictReader(open(SNAPSHOTS)))
    if not rows:
        return [], None
    last_ts = max(r["timestamp"] for r in rows)
    cur = [r for r in rows if r["timestamp"] == last_ts and "towers-at-costa-verde" in (r["url"] or "")]
    return cur, last_ts


def rows_from_live():
    """Fresh read-only tracker run (no snapshot logged, no ntfy)."""
    py = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
    out = subprocess.check_output(
        [py, TRACKER_PY, "--json", "--no-log", "--no-notify"],
        cwd=TRACKER_DIR, stderr=subprocess.DEVNULL, timeout=180,
    )
    data = json.loads(out)
    rows = [u for u in data if "towers-at-costa-verde" in (u.get("url") or "")]
    return rows, "live"


def group_units(rows):
    """Group Towers unit rows into (tower, stack) blocks. Accepts either
    snapshots.csv DictReader rows or apartment_tracker --json unit dicts; both
    carry floorplan/unit/beds/sqft/starting_price/special/date_available."""
    blocks = {}
    # seed every stack for both towers so empties still render
    for tw in ("North", "South"):
        for stack, fp in STACK_FLOORPLAN.items():
            blocks[f"{tw[0]}-{stack}"] = {
                "tower": tw, "stack": stack, "floorplan": fp, "units": [],
            }
    for r in rows:
        tower, base = parse_tower_floorplan(r["floorplan"])
        unit = r["unit"]
        stack = unit[-2:]
        floor = unit[:-2].lstrip("0") or "0"
        key = f"{tower[0]}-{stack}"
        blk = blocks.get(key)
        if blk is None:
            continue
        blk["units"].append({
            "unit": unit,
            "floor": floor,
            "beds": beds_of(r.get("beds", "")),
            "sqft": r.get("sqft", ""),
            "price": r["starting_price"],
            "concession": concession_label(r["special"]),
            "raw_special": (r["special"] or "").strip(),
            "net": net_rent(r["starting_price"], r["special"]),
            "date_available": r["date_available"],
        })
    for blk in blocks.values():
        blk["units"].sort(key=lambda u: u["net"])
        blk["count"] = len(blk["units"])
        blk["min_net"] = min((u["net"] for u in blk["units"]), default=None)
    return blocks


def build_availability(live=False):
    rows, ts = rows_from_live() if live else rows_from_snapshot()
    return {"timestamp": ts, "source": "live" if live else "snapshot", "blocks": group_units(rows)}


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "application/javascript",
    ".css": "text/css", ".png": "image/png", ".jpg": "image/jpeg",
    ".json": "application/json", ".pdf": "application/pdf",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path == "/":
            return self._serve_static("index.html")
        if path == "/api/availability":
            live = parse_qs(u.query).get("live", ["0"])[0] == "1"
            try:
                return self._send(200, build_availability(live=live))
            except Exception as e:
                return self._send(500, {"error": str(e)})
        if path == "/api/dots":
            if os.path.isfile(DOTS_FILE):
                with open(DOTS_FILE, "rb") as f:
                    return self._send(200, f.read(), "application/json")
            return self._send(404, {"error": "no dots"})
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/dots":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            try:
                data = json.loads(body)
                with open(DOTS_FILE, "w") as f:
                    json.dump(data, f, indent=2)
                return self._send(200, {"ok": True})
            except Exception as e:
                return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    def _serve_static(self, rel):
        rel = rel.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, rel))
        if os.path.commonpath([full, STATIC]) != STATIC:
            return self._send(403, {"error": "forbidden"})
        if not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            self._send(200, f.read(), CONTENT_TYPES.get(ext, "application/octet-stream"))


def main():
    args = sys.argv[1:]
    port = 8899
    host = "0.0.0.0"
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    if "--host" in args:
        host = args[args.index("--host") + 1]
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Towers map server on http://{host}:{port}/  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
