# Apartment Tracker

Scrapes available units from [Garden Communities](https://www.gardencommunitiesca.com)
and prints a combined table. Optionally logs a timestamped snapshot to
`snapshots.csv` so you can track price/availability/specials over time.

For each available unit it pulls: floorplan name, beds, sqft, unit number,
date available, starting price, deposit, and any move-in special.

## Data source

As of July 2026 the public `gardencommunitiesca.com` floorplan pages stopped
carrying per-unit specials (they now show only a property-wide promo banner).
The real per-unit data — rent, deposit, date available, and the actual special
text — lives in the **SecureCafe online-leasing portal**, on the "Apartments"
step you reach by clicking **Apply → Change**:

```
https://gardencommunitiesca.securecafe.com/onlineleasing/<slug>/oleapplication.aspx?stepname=Apartments&myOlePropertyId=<id>
```

That page is plain server-rendered HTML — one `<table>` per floorplan, one row
per available unit — so **one request per property** returns everything. No JS
rendering needed.

### The portal's Specials column is unreliable; the public site is the check

On 2026-07-16 ~22:35 the portal stopped rendering the **Specials column** for
Towers, and for LUX about an hour later — the whole column absent, not blank
cells. The concessions were still live: the public site's per-floorplan badges
matched the portal's last-published specials **exactly, 12/12 floorplans,
including the two that had none** (North Tower Royale, South Tower Montecito).

So a missing column means "the portal isn't publishing", not "no specials", and
the two must not be conflated. `apply_known_specials()` resolves it against the
public floorplans page:

| portal column | public badge | result |
| --- | --- | --- |
| absent | present | carry last-known text forward |
| absent | absent | special genuinely ended |
| absent | site unreachable | carry forward, warn |
| present | (ignored) | trust the portal, empty cell = ended |

The public page (`PUBLIC_FLOORPLANS`, same slug as the portal) is
server-rendered, so plain `requests` works — but it only carries a **boolean**
badge per floorplan, never the text. The text ("2 Months' Rent Free at Move In")
comes only from the portal, which is why it has to be carried forward rather
than re-derived.

Note the portal's behaviour is not simply "column appears only when specials
exist": Volar (`volar0`, id `2019658`) serves the column fine while Towers, which
also has live specials, does not. The cause is unknown — hence the cross-check
rather than an inference from either source alone.

Beware the old URL shape: `gardencommunitiesca.com/<slug>/` now 404s, and a
plain fetch of it looks exactly like "no promo anywhere". The live path is
`/apartments/ca/<city>/<slug>/`.

## Setup

```
pip install requests beautifulsoup4 curl_cffi
```

`curl_cffi` is optional but recommended: it impersonates a real Chrome TLS
fingerprint, which is needed to get past Cloudflare on some machines (e.g.
macOS system Python). If it isn't installed the script falls back to plain
`requests`.

## Usage

```
python apartment_tracker.py             # all properties, table + snapshot
python apartment_tracker.py --json      # JSON output instead of a table
python apartment_tracker.py --no-log    # don't append to snapshots.csv
python apartment_tracker.py --no-notify # don't send ntfy change alerts
python apartment_tracker.py <URL> ...   # scrape specific Apartments-step URL(s)
```

## Tracked properties

Edit the `PROPERTIES` list near the top of `apartment_tracker.py` to add or
remove properties (name, url slug, `myOlePropertyId`). Every available unit for
each property is scraped — no per-floorplan curation. Currently tracked:

- **Towers at Costa Verde** (`2148144`) — all floorplans, both towers
- **LUX by Garden / The Jewel** (`2152097`) — all floorplans; The Jewel's
  premium 2-beds are ordinary floorplans here, so they're covered automatically

## Scheduled run (macOS, every hour)

A LaunchAgent runs the tracker every hour on the MacBook. Output goes to
`tracker.log`; history accumulates in `snapshots.csv`.

- Plist: `~/Library/LaunchAgents/com.bryce.apartmenttracker.plist`
- Runs `apartment_tracker.py` every 3600s (and once at load/login)

Manage it:

```
launchctl bootout   gui/$(id -u) ~/Library/LaunchAgents/com.bryce.apartmenttracker.plist   # stop
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bryce.apartmenttracker.plist   # start
launchctl kickstart gui/$(id -u)/com.bryce.apartmenttracker                                 # run now
launchctl print     gui/$(id -u)/com.bryce.apartmenttracker | grep -E 'state|runs'          # status
```

## Change notifications (ntfy)

After each run the script diffs the current units against the last run and, if
anything changed, sends a push notification to an [ntfy.sh](https://ntfy.sh)
topic. Subscribe to that topic in the ntfy app to receive alerts.

- Topic/URL: set `NTFY_URL` near the top of `apartment_tracker.py`
- Last-seen state is stored in `state.json`
- Watched fields: date available, starting price, deposit, special
- Alerts fire for: new unit listed, unit gone, or any watched field changing
- The first run (no `state.json` yet) baselines silently — no alert
- Disable per-run with `--no-notify`; ad-hoc `<URL>` runs never notify or touch
  `state.json`

## Notes

- **No login or cookies needed** — requests are stateless.
- LUX's portal table omits a Deposit column, so `deposit` is blank for LUX
  units; all other fields are present.
- The SecureCafe portals throw an **intermittent** Cloudflare challenge. The
  script retries each property up to 3 times (6s apart) before giving up, which
  clears the vast majority of these. Network timeouts/DNS errors are caught the
  same way instead of aborting the whole run.
- **Fail-safe on a blocked property:** if a property still can't be fetched
  after the retries, it's treated as *unknown*, not *empty* — its previously
  tracked units are carried forward unchanged, so a transient block never fires
  a flood of false "gone" alerts or wipes that property from `state.json`.
  (State records each unit's source property under a `_url` key to make this
  possible.) The property is simply skipped for that one run.
- If challenges become persistent (not just intermittent), re-run with
  `--browser` (requires `pip install playwright && playwright install chromium`).
- `snapshots.csv` gets one row per available unit per run, prefixed with a
  timestamp.
