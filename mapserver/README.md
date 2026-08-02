# Towers at Costa Verde — Availability Map

Interactive pan/zoom site map of the North & South towers with a clickable dot on
every unit **stack**. Click a dot to see live availability for that floorplan.

## Access

Runs as a LaunchAgent (`com.bryce.towersmap`) bound to `0.0.0.0:8899`, reachable
over Tailscale at `http://<your-tailscale-ip>:8899/` (find it with `tailscale ip -4`).

> The public GitHub Pages version lives in `../docs/` and reads a committed
> `availability.json`; this local server is the live, LAN/Tailscale-only variant.

## How the mapping works

Unit numbers are **FFSS**: first two digits = floor, last two = **stack** (the
block number printed on the site map). E.g. `#0703` = floor 07, stack 03 = Catalina.
Each dot = one (tower, stack); its popup lists every currently-available unit in that
stack (all the same floorplan), sorted by concession-adjusted net rent.

Green dot = has availability, grey = no current vacancy.

## UI

- **scroll / pinch** zoom, **drag** pan.
- **Min floor** dropdown filters units by floor.
- **Only available** hides empty stacks.
- **↻ Live refresh** runs the tracker live (read-only; no snapshot/ntfy) instead of
  the last hourly snapshot.
- **✎ Edit dots** → drag any dot, then **💾 Save layout** persists to `dots.json`.

## Data source

Reads the latest row-set from `../snapshots.csv` (kept current by the hourly
`com.bryce.apartmenttracker` LaunchAgent). Live refresh shells out to
`../apartment_tracker.py --json --no-log --no-notify`.

## Files

- `server.py` — stdlib HTTP server + availability API (no external deps).
- `static/index.html` — Leaflet frontend (Leaflet bundled locally).
- `static/map.png` — site map rendered from the RentCafe PDF at 3.5×.
- `static/sitemap.pdf` — original floorplan PDF.
- `dots.json` — 38 dot positions (fractional coords, top-left origin).

## Manage

```bash
launchctl unload ~/Library/LaunchAgents/com.bryce.towersmap.plist   # stop
launchctl load   ~/Library/LaunchAgents/com.bryce.towersmap.plist   # start
tail -f server.log                                                  # logs
```

If Tailscale devices can't reach it, macOS Application Firewall may be blocking
incoming connections for `python3` — allow it in System Settings → Network →
Firewall.
