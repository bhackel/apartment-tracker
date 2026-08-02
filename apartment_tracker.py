#!/usr/bin/env python3
"""Scrape available units from Garden Communities' online-leasing portal.

As of July 2026 the public gardencommunitiesca.com floorplan pages stopped
carrying per-unit specials (they now only show a property-wide promo banner).
The real, per-unit data — rent, deposit, date available, and the actual move-in
special text — lives in the SecureCafe leasing portal, on the "Apartments" step
you reach by clicking Apply -> Change. That page is plain server-rendered HTML:
one <table> per floorplan, one row per available unit.

For each available apartment it pulls:
  - floorplan name, beds, sqft
  - unit number
  - date available
  - starting price (rent)
  - deposit
  - move-in special

One request per property returns every available unit for that property, so the
two properties below (Towers at Costa Verde, and LUX by Garden / The Jewel) are
covered in two requests. It prints one combined table and appends a timestamped
snapshot to snapshots.csv so price/availability/specials can be tracked over
time.

Usage:
  python apartment_tracker.py            # all properties, table + snapshot
  python apartment_tracker.py --json     # JSON output
  python apartment_tracker.py --no-log   # don't append to snapshots.csv
  python apartment_tracker.py --no-notify# don't send ntfy change alerts
  python apartment_tracker.py --browser  # render via playwright (Cloudflare)
  python apartment_tracker.py <URL> ...  # scrape specific Apartments-step URL(s)
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# curl_cffi impersonates a real Chrome TLS fingerprint, which gets past
# Cloudflare on machines where plain requests is challenged (e.g. macOS
# Python on LibreSSL). Fall back to requests if it isn't installed.
try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

# SecureCafe online-leasing "Apartments" step. Passing only the property id
# (no floorPlans filter) lists every available unit for the property, grouped
# into one <table> per floorplan.
PORTAL = (
    "https://gardencommunitiesca.securecafe.com/onlineleasing/{slug}/"
    "oleapplication.aspx?stepname=Apartments&myOlePropertyId={prop}"
)

# The public marketing site, which uses the same slug as the portal. Its
# floorplans page carries a per-floorplan "Specials Available" badge — a
# boolean, with no text — that stays accurate even when the portal drops its
# Specials column. Server-rendered, so plain requests is enough.
PUBLIC_FLOORPLANS = (
    "https://www.gardencommunitiesca.com/apartments/ca/san-diego/{slug}/floorplans"
)

# Properties to track. LUX by Garden includes "The Jewel at LUX" (its premium
# 2-bed collection) as ordinary floorplans, so tracking the whole property
# covers The Jewel automatically.
PROPERTIES = [
    # (display name, url slug, myOlePropertyId)
    ("Towers at Costa Verde", "towers-at-costa-verde", "2148144"),
    ("LUX by Garden / The Jewel", "lux-by-garden", "2152097"),
]

_HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_FILE = os.path.join(_HERE, "snapshots.csv")
STATE_FILE = os.path.join(_HERE, "state.json")

# ntfy topic to notify when a unit changes. Kept OUT of source (the topic is a
# shared secret — anyone with it can read/spam your alerts). Set it via the
# NTFY_URL env var (the LaunchAgent supplies it); notifications are skipped if unset.
NTFY_URL = os.environ.get("NTFY_URL", "")

# unit fields whose changes are worth a notification
WATCH = ["date_available", "starting_price", "deposit", "special"]

# The Cloudflare challenge on these portals is intermittent — a plain retry a
# few seconds later usually sails through. Retry before giving up on a property.
MAX_ATTEMPTS = 3
RETRY_DELAY = 6  # seconds between attempts

HEADERS = {
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

FIELDS = ["floorplan", "beds", "sqft", "unit", "date_available",
          "starting_price", "deposit", "special", "url"]


def property_urls() -> list[str]:
    return [PORTAL.format(slug=slug, prop=prop) for _, slug, prop in PROPERTIES]


def fetch_requests(url: str) -> str:
    if cffi_requests is not None:
        resp = cffi_requests.get(url, impersonate="chrome", timeout=30)
        return resp.text
    resp = requests.get(url, headers=HEADERS, timeout=30)
    return resp.text


def fetch_browser(url: str) -> str:
    """Render in a real browser to clear the Cloudflare challenge."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)
        html = page.content()
        browser.close()
    return html


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _money(value: str | None) -> str | None:
    """Normalize "$4,150" -> "$4,150.00" so it matches historical snapshots
    (the portal drops the cents, the old floorplan pages kept them)."""
    v = _clean(value)
    if not v:
        return None
    if re.fullmatch(r"\$[\d,]+", v):
        return v + ".00"
    return v


def _extract_special(cell, fallback_text: str) -> str | None:
    """Pull the special text out of a portal Specials cell.

    The concession lives in a hidden tooltip as one or more <li> items. The
    portal intermittently emits the SAME concession as several identical <li>s
    (seen 2026-07 on Towers 2-month units), and a plain get_text() concatenates
    them with no separator -> "2 Months' Rent Free2 Months' Rent Free". Read the
    <li>s directly and dedupe (order-preserving); join distinct ones with "; ".
    Fall back to the concatenated cell text when there's no list markup.
    """
    if cell is not None:
        items = []
        for li in cell.find_all("li"):
            txt = _clean(li.get_text())
            if txt and txt not in items:
                items.append(txt)
        if items:
            return "; ".join(items)
    return fallback_text.replace("Apartment Specials", "").strip() or None


def parse_units(html: str, source_url: str) -> list[dict]:
    """Parse the SecureCafe Apartments step: one <table> per floorplan, each
    with a <caption> naming the floorplan and <tr class="AvailUnitRow"> rows."""
    soup = BeautifulSoup(html, "html.parser")
    units = []
    for table in soup.find_all("table"):
        rows = table.select("tr.AvailUnitRow")
        if not rows:
            continue

        # The portal renders no Specials column at all when it has no specials
        # text to show — which is NOT the same as the units having no specials.
        # See apply_known_specials().
        labels = {c.get("data-label") for tr in rows for c in tr.find_all(["th", "td"])}
        headers = {_clean(th.get_text()) for th in table.select("thead th")}
        has_specials_col = "Specials" in labels or "Specials" in headers

        floorplan = None
        beds = None
        cap = table.find("caption")
        if cap:
            m = re.search(r"Floor Plan:\s*(.*)", _clean(cap.get_text()))
            raw = _clean(m.group(1)) if m else _clean(cap.get_text())
            # caption looks like "North Tower Catalina - 1 Bedroom, 1 Bathroom"
            floorplan = raw.split(" - ")[0].strip()
            bm = re.search(r"(\d+)\s+Bedroom", raw)
            if bm:
                beds = f"{bm.group(1)} Bed(s)"
            elif re.search(r"studio", raw, re.I):
                beds = "Studio"

        for tr in rows:
            cells = {}
            special_cell = None
            for c in tr.find_all(["th", "td"]):
                label = c.get("data-label")
                if label:
                    cells[label] = _clean(c.get_text())
                    if label == "Specials":
                        special_cell = c

            unit = cells.get("Apartment", "").replace("#", "").strip()
            if not unit:
                continue

            special = _extract_special(special_cell, cells.get("Specials", ""))

            date_available = cells.get("Date Available") or None
            if date_available and date_available.lower() == "available":
                date_available = "Available Now"

            units.append(
                {
                    "floorplan": floorplan or source_url,
                    "beds": beds,
                    "sqft": cells.get("Sq.Ft."),
                    "unit": unit,
                    "date_available": date_available,
                    "starting_price": _money(cells.get("Rent")),
                    "deposit": _money(cells.get("Deposit")),
                    "special": special,
                    "special_known": has_specials_col,
                    "url": source_url,
                }
            )
    return units


def parse_specials_flags(html: str) -> dict[str, bool]:
    """Map floorplan name -> whether the public site flags it as having specials."""
    soup = BeautifulSoup(html, "html.parser")
    flags = {}
    for card in soup.select("div.fp-container div.card"):
        heading = card.find(["h2", "h3", "h4", "h5"])
        if not heading:
            continue
        name = _clean(heading.get_text())
        if name:
            flags[name] = bool(card.select_one('[data-selenium-id$="Special"]'))
    return flags


def fetch_specials_flags() -> dict[str, bool]:
    """Per-floorplan specials flags from the public site, across all properties.

    Floorplan names are unique across the tracked properties ("North Tower
    Catalina" vs LUX's "2B-4"), so one merged map is unambiguous. Returns {} if
    the public site can't be reached — callers must treat that as "unknown"
    rather than "no specials".
    """
    flags = {}
    for _name, slug, _prop in PROPERTIES:
        url = PUBLIC_FLOORPLANS.format(slug=slug)
        try:
            flags.update(parse_specials_flags(fetch_requests(url)))
        except Exception as e:  # noqa: BLE001 - never let this break a run
            print(f"!! could not fetch specials flags from {url}: {e}", file=sys.stderr)
    return flags


def _is_challenge(html: str) -> bool:
    """True if the HTML is a Cloudflare interstitial rather than the real page."""
    return (
        "Just a moment" in html
        and "challenge-platform" in html
        and "AvailUnitRow" not in html
    )


def scrape_all(urls: list[str], use_browser: bool) -> tuple[list[dict], list[str]]:
    """Fetch and parse every property URL.

    Returns (units, failed_urls). A URL lands in failed_urls if, after retries,
    it still returns a Cloudflare challenge or keeps erroring. Callers treat a
    failed property as "unknown", NOT as "zero units" — so a transient block
    can't wipe that property's tracked state or fire false "gone" alerts.
    """
    fetch = fetch_browser if use_browser else fetch_requests
    all_units: list[dict] = []
    failed: list[str] = []
    for url in urls:
        html = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                html = fetch(url)
            except Exception as e:  # network timeout, DNS, TLS, etc.
                html = ""
                print(
                    f"!! fetch error for {url} "
                    f"(attempt {attempt}/{MAX_ATTEMPTS}): {e}",
                    file=sys.stderr,
                )
            if html and not _is_challenge(html):
                break
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY)

        if not html or _is_challenge(html):
            hint = "" if use_browser else " — try --browser"
            print(
                f"!! could not fetch {url} after {MAX_ATTEMPTS} attempts "
                f"(Cloudflare challenge or network error){hint}; "
                f"preserving this property's previous state",
                file=sys.stderr,
            )
            failed.append(url)
            continue
        all_units.extend(parse_units(html, url))
    return all_units, failed


def log_snapshot(units: list[dict]) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    new_file = not os.path.exists(SNAPSHOT_FILE)
    with open(SNAPSHOT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["timestamp"] + FIELDS)
        for u in units:
            writer.writerow([ts] + [u.get(k, "") for k in FIELDS])


def print_table(units: list[dict]) -> None:
    if not units:
        print("No available units found.")
        return
    rows = [
        (u["floorplan"], u["unit"], u["beds"] or "", u["sqft"] or "",
         u["date_available"] or "", u["starting_price"] or "",
         u["deposit"] or "", u["special"] or "—")
        for u in units
    ]
    head = ("Floorplan", "Unit", "Beds", "SqFt", "Available", "Starting", "Deposit", "Special")
    widths = [max(len(str(r[i])) for r in rows + [head]) for i in range(len(head))]
    line = "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(head))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)))
    print(f"\n{len(units)} available unit(s) across {len({u['floorplan'] for u in units})} floorplan(s).")


def _key(u: dict) -> str:
    return f"{u['floorplan']} #{u['unit']}"


def _load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def apply_known_specials(units: list[dict]) -> None:
    """Recover specials for units whose portal table had no Specials column.

    On 2026-07-16 the portal stopped rendering the Specials column for both
    properties while the concessions were still live — the public site's
    per-floorplan badges still matched the portal's last-published specials
    exactly, floorplan for floorplan, including the two that had none. Reading
    the absent column as "no special" therefore fires a mass false alert and
    overwrites real values, so instead:

      - public site still flags the floorplan  -> carry the last-known text
        forward (the text itself is only ever published by the portal)
      - public site no longer flags it         -> the special genuinely ended
      - public site unreachable                -> carry forward, and say so

    A special that ends while the portal IS publishing the column shows up as
    an empty cell, not a missing one, and is reported normally.
    """
    stale = [u for u in units if not u.get("special_known")]
    if not stale:
        return

    flags = fetch_specials_flags()
    old_state = _load_state()
    carried = ended = 0
    for u in stale:
        has_special = flags.get(u["floorplan"])
        if has_special is False:
            u["special"] = None
            ended += 1
        else:
            u["special"] = old_state.get(_key(u), {}).get("special")
            carried += 1

    detail = "public site unreachable, assuming unchanged" if not flags else (
        f"{carried} carried forward, {ended} confirmed ended"
    )
    print(
        f"!! portal published no Specials column for {len(stale)} unit(s); {detail}",
        file=sys.stderr,
    )


def detect_changes(units: list[dict], failed_urls: list[str]) -> tuple[list[str], dict]:
    """Diff freshly-fetched units against the saved state.

    Returns (list of human-readable change lines, new state dict).
    On the very first run (no state file) it baselines silently.

    ``failed_urls`` are properties that couldn't be fetched this run. Their
    previously-known units are carried forward unchanged and excluded from
    "gone" detection, so a transient Cloudflare block or network error never
    produces false "gone" alerts or drops the property from the tracked state.
    """
    # "_url" tags each unit with the property it came from so a later failed
    # run can tell which stored units to leave alone. It isn't in WATCH, so it
    # never counts as a field change.
    fresh_state = {
        _key(u): {**{k: u.get(k) for k in WATCH}, "_url": u.get("url")}
        for u in units
    }

    if not os.path.exists(STATE_FILE):
        return [], fresh_state  # baseline, don't notify

    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            old_state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return [], fresh_state

    failed = set(failed_urls)

    changes = []
    for key, cur in fresh_state.items():
        if key not in old_state:
            price = cur.get("starting_price") or "?"
            avail = cur.get("date_available") or "?"
            special = cur.get("special")
            line = f"NEW {key}: {price}, avail {avail}"
            if special:
                line += f" ({special})"
            changes.append(line)
        else:
            prev = old_state[key]
            for field in WATCH:
                if cur.get(field) != prev.get(field):
                    label = field.replace("_", " ")
                    changes.append(
                        f"{key}: {label} {prev.get(field) or '-'} -> {cur.get(field) or '-'}"
                    )

    # Start from the freshly-scraped state, then carry forward any unit that
    # belongs to a property we failed to fetch this run.
    new_state = dict(fresh_state)
    for key, prev in old_state.items():
        if key in fresh_state:
            continue
        if prev.get("_url") in failed:
            new_state[key] = prev  # property unreachable — keep, don't report
        else:
            changes.append(f"GONE {key}: no longer listed")

    return changes, new_state


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def send_ntfy(title: str, message: str) -> None:
    if not NTFY_URL:
        return  # no topic configured (e.g. public CI run) — skip silently
    body = message.encode("utf-8")
    headers = {"Title": title, "Tags": "house"}
    try:
        requests.post(NTFY_URL, data=body, headers=headers, timeout=15)
    except requests.RequestException as e:
        print(f"!! ntfy notify failed: {e}", file=sys.stderr)


def main() -> int:
    args = sys.argv[1:]
    use_browser = "--browser" in args
    as_json = "--json" in args
    no_log = "--no-log" in args
    no_notify = "--no-notify" in args
    custom_urls = [a for a in args if a.startswith("http")]
    urls = custom_urls or property_urls()

    units, failed_urls = scrape_all(urls, use_browser)
    apply_known_specials(units)

    if as_json:
        print(json.dumps(units, indent=2))
    else:
        print_table(units)

    # Detect changes and notify (only against the default property set, so
    # ad-hoc URL runs don't clobber the tracked state).
    if units and not no_notify and not custom_urls:
        changes, new_state = detect_changes(units, failed_urls)
        if changes:
            msg = "\n".join(changes)
            if not as_json:
                print("\nChanges detected:\n" + msg)
            send_ntfy(f"Apartment update ({len(changes)})", msg)
        save_state(new_state)

    if units and not no_log:
        log_snapshot(units)
        if not as_json:
            print(f"Snapshot appended to {SNAPSHOT_FILE}")

    return 0 if units else 1


if __name__ == "__main__":
    raise SystemExit(main())
