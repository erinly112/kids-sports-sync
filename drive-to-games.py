#!/usr/bin/env python3
import warnings
warnings.filterwarnings("ignore")
"""
For each game event in the sports calendars, parse the coach's arrival time
from the event description, calculate drive time from home using OpenStreetMap
(no API key needed), and create a "🚗 Leave for [event]" block on Erin Shea's calendar.

Usage:
  python3 drive-to-games.py           # process next 60 days
  python3 drive-to-games.py --days 90
  python3 drive-to-games.py --dry-run # preview without creating events
"""

import json
import os
import re
import math
import time
import argparse
import datetime
import urllib.request
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Config ────────────────────────────────────────────────────────────────────

import sportsync_config as _sc
_cfg = _sc.load()

# Build CALENDAR_KIDS from config: driving=true → first kid listed; driving=false → None (skip)
CALENDAR_KIDS = {}
for _t in _cfg["teams"]:
    _name = _t["calendar_name"]
    if _t.get("driving", True):
        _kids = _t.get("kids", [])
        CALENDAR_KIDS[_name] = _kids[0] if _kids else None
    else:
        CALENDAR_KIDS[_name] = None

SOURCE_CALENDAR_NAMES = list(CALENDAR_KIDS.keys())

SCOPES           = ["https://www.googleapis.com/auth/calendar"]
CONFIG_DIR       = Path(os.environ["SCRIPT_CONFIG_DIR"]) if "SCRIPT_CONFIG_DIR" in os.environ else Path.home() / ".config" / "google-tasks-sync"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE       = CONFIG_DIR / "calendar-token.json"
DRIVE_CONFIG     = CONFIG_DIR / "drive-config.json"
SYNCED_FILE      = CONFIG_DIR / "drive-events-synced.json"
GEOCODE_CACHE    = CONFIG_DIR / "geocode-cache.json"

LOCAL_TZ = ZoneInfo("America/New_York")

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_credentials():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return creds

# ── Sync tracking ─────────────────────────────────────────────────────────────

def load_synced():
    if SYNCED_FILE.exists():
        return json.loads(SYNCED_FILE.read_text())
    return {}

def save_synced(synced):
    SYNCED_FILE.write_text(json.dumps(synced, indent=2))

# ── Geocoding (Nominatim / OpenStreetMap) ─────────────────────────────────────

def load_geocache():
    if GEOCODE_CACHE.exists():
        return json.loads(GEOCODE_CACHE.read_text())
    return {}

def save_geocache(cache):
    GEOCODE_CACHE.write_text(json.dumps(cache, indent=2))

def clean_address(address):
    """Return progressively simpler versions of an address to try."""
    a = address.strip()
    # Remove country suffix
    a = re.sub(r',?\s*united states\s*$', '', a, flags=re.IGNORECASE).strip()
    # Remove unit/suite
    a = re.sub(r'\b(unit|suite|ste|apt|#)\s*\w+', '', a, flags=re.IGNORECASE).strip().strip(',')
    # Remove "Ext" abbreviation
    a = re.sub(r'\bExt\b', '', a, flags=re.IGNORECASE).strip().strip(',')
    candidates = [a]
    # Also try just street + city + state (drop building name prefixes)
    parts = [p.strip() for p in a.split(',')]
    if len(parts) >= 3:
        candidates.append(', '.join(parts[-3:]))  # last 3 parts
    if len(parts) >= 2:
        candidates.append(', '.join(parts[-2:]))  # just city, state
    return candidates

def geocode(address, cache):
    """Return (lat, lon) for an address using Nominatim. Results are cached."""
    # Skip obviously unresolvable addresses
    if re.search(r'\btba\b|\btbd\b', address, re.IGNORECASE):
        return None

    key = address.strip().lower()
    if key in cache:
        return cache[key]

    for candidate in clean_address(address):
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({
            "q": candidate, "format": "json", "limit": 1
        })
        req = urllib.request.Request(url, headers={"User-Agent": "drive-to-games/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                results = json.loads(resp.read())
            if results:
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])
                cache[key] = (lat, lon)
                time.sleep(1)  # Nominatim rate limit: 1 req/sec
                return (lat, lon)
            time.sleep(1)
        except Exception as e:
            print(f"  Geocode error for '{candidate}': {e}")
    return None

# ── Drive time (OSRM) ─────────────────────────────────────────────────────────

def get_drive_minutes(origin_coords, dest_coords):
    """
    Estimate drive time using straight-line distance + road factor + average speed.
    Uses haversine formula for distance, then applies:
      - 1.35x road multiplier (roads are longer than straight line)
      - 35 mph average speed (suburban MA)
    Reliable, no external API needed, accurate enough for local youth sports.
    """
    lat1, lon1 = origin_coords
    lat2, lon2 = dest_coords

    # Haversine distance in miles
    R = 3959
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    straight_miles = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    road_miles = straight_miles * 1.35   # road distance factor
    avg_speed  = 35                       # mph, suburban MA
    minutes    = (road_miles / avg_speed) * 60
    return max(5, round(minutes))

# ── Arrival time parsing ──────────────────────────────────────────────────────

ARRIVAL_PATTERNS = [
    # "Arrival Time: 2:00 PM" or "Arrival Time: 2pm"
    r'arrival\s+time\s*[:\-]?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm))',
    # "arrive by 2pm", "warmups at 2:30pm", "be there by 2"
    r'(?:players?\s+)?(?:arrive|arrival|report|check[\s-]in|be there|show up|warmups?)\s+(?:by|at|@)?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
    # "arrive: 2pm", "report: 2:30"
    r'(?:arrive|arrival|report|be there)\s*[:\-]\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
    # "2:30pm arrival", "2pm warmup"
    r'(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s+(?:arrival|arrive|warmup|warm[\s-]up)',
]

DEFAULT_ARRIVAL_BEFORE_MINS = 30  # when no arrival time specified, arrive 30 min before start

def parse_arrival_time(description, event_start_dt):
    if not description:
        return None
    for pattern in ARRIVAL_PATTERNS:
        match = re.search(pattern, description, re.IGNORECASE)
        if match:
            parsed = parse_time_string(match.group(1).strip(), event_start_dt)
            if parsed:
                return parsed
    return None

def parse_time_string(time_str, reference_dt):
    time_str = time_str.strip().lower().replace(' ', '')
    for fmt in ["%I:%M%p", "%I%p", "%H:%M"]:
        try:
            t = datetime.datetime.strptime(time_str, fmt)
            result = reference_dt.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if result > reference_dt + datetime.timedelta(hours=3):
                result -= datetime.timedelta(hours=12)
            return result
        except ValueError:
            continue
    return None

# ── Is game ───────────────────────────────────────────────────────────────────

def is_game(event):
    title = event.get("summary", "").lower()
    return any(x in title for x in [" vs ", " at ", "game", "meet", "match"])

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",    type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # home_address and driver_calendar come from config.json; fall back to drive-config.json
    if _cfg.get("home_address") and _cfg.get("driver_calendar"):
        home_address    = _cfg["home_address"]
        target_cal_name = _cfg["driver_calendar"]
    else:
        _dc             = json.loads(DRIVE_CONFIG.read_text())
        home_address    = _dc["home_address"]
        target_cal_name = _dc["driver_calendar"]

    geocache = load_geocache()

    # Geocode home address once
    home_coords = geocode(home_address, geocache)
    if not home_coords:
        print(f"✗ Could not geocode home address: {home_address}")
        return
    save_geocache(geocache)

    creds   = get_credentials()
    service = build("calendar", "v3", credentials=creds)

    now      = datetime.datetime.now(tz=datetime.timezone.utc)
    time_min = now.isoformat()
    time_max = (now + datetime.timedelta(days=args.days)).isoformat()

    all_cals_list = service.calendarList().list().execute()["items"]
    all_cals      = {c.get("summary","").strip(): c for c in all_cals_list}

    if target_cal_name not in all_cals:
        print(f"✗ Target calendar '{target_cal_name}' not found.")
        return
    target_id = all_cals[target_cal_name]["id"]

    synced  = load_synced()
    created = 0
    updated = 0
    skipped = 0

    for source_name in SOURCE_CALENDAR_NAMES:
        kid = CALENDAR_KIDS.get(source_name)
        if kid is None:
            continue  # Tom drives, skip

        if source_name not in all_cals:
            print(f"  ✗ Calendar not found: {source_name}")
            continue

        source_id = all_cals[source_name]["id"]
        events    = service.events().list(
            calendarId=source_id,
            timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy="startTime", maxResults=250,
        ).execute().get("items", [])

        for event in events:
            if not is_game(event):
                continue

            event_id = event["id"]
            summary  = event.get("summary", "(no title)")
            location = event.get("location", "").strip()
            desc     = event.get("description", "")
            start    = event.get("start", {})

            if "dateTime" not in start:
                continue

            start_dt = datetime.datetime.fromisoformat(
                start["dateTime"].replace("Z", "+00:00")
            ).astimezone(LOCAL_TZ)

            # Coach arrival time — default to 30 min before game start
            arrival_dt = parse_arrival_time(desc, start_dt)
            if arrival_dt is None:
                arrival_dt   = start_dt - datetime.timedelta(minutes=DEFAULT_ARRIVAL_BEFORE_MINS)
                arrival_note = f"30 min before game start (no arrival time in description)"
            else:
                arrival_note = f"coach arrival time: {arrival_dt.strftime('%-I:%M%p').lower()}"

            # Already synced — check for changes and update if needed
            if event_id in synced and synced[event_id] not in (
                "pre-existing", "skipped-no-location", "skipped-geocode-failed",
                "skipped-routing-failed", "skipped-bad-routing"
            ):
                target_event_id = synced[event_id]
                # Will be rebuilt below — continue to geocode/route then update
                pass
            elif event_id in synced:
                skipped += 1
                continue

            # Drive time
            if not location or re.search(r'\btba\b|\btbd\b', location, re.IGNORECASE):
                print(f"  ⚠ Location TBA: {summary} ({start_dt.strftime('%b %-d')}) — skipping")
                synced[event_id] = "skipped-no-location"
                continue

            dest_coords = geocode(location, geocache)
            save_geocache(geocache)

            if not dest_coords:
                print(f"  ⚠ Could not geocode '{location}' for: {summary} — skipping")
                synced[event_id] = "skipped-geocode-failed"
                continue

            drive_mins = get_drive_minutes(home_coords, dest_coords)
            if not drive_mins or drive_mins > 90:
                print(f"  ⚠ Drive time implausible ({drive_mins} min) for: {summary} — check location in event")
                synced[event_id] = "skipped-bad-routing"
                continue


            leave_dt      = arrival_dt - datetime.timedelta(minutes=drive_mins)
            leave_summary = f"🚗 Leave for {summary} ({kid})"
            date_str      = start_dt.strftime("%a %b %-d")
            tz_str        = "America/New_York"

            body = {
                "summary":  leave_summary,
                "location": location,
                "description": (
                    f"Kid: {kid}\n"
                    f"Drive to: {summary}\n"
                    f"Destination: {location}\n"
                    f"Drive time: ~{drive_mins} min\n"
                    f"Target arrival: {arrival_dt.strftime('%-I:%M%p').lower()} ({arrival_note})\n"
                    f"[Auto-generated by drive-to-games.py]"
                ),
                "start": {"dateTime": leave_dt.isoformat(), "timeZone": tz_str},
                "end":   {"dateTime": arrival_dt.isoformat(), "timeZone": tz_str},
            }

            if args.dry_run:
                print(f"  [dry run] {date_str}: {leave_summary}")
                print(f"    Leave {leave_dt.strftime('%-I:%M%p').lower()} → "
                      f"arrive {arrival_dt.strftime('%-I:%M%p').lower()} "
                      f"(~{drive_mins} min, {arrival_note})")
                created += 1
                continue

            # Update existing or create new
            if event_id in synced and synced[event_id] not in (
                "pre-existing", "skipped-no-location", "skipped-geocode-failed",
                "skipped-routing-failed", "skipped-bad-routing"
            ):
                try:
                    existing = service.events().get(
                        calendarId=target_id, eventId=synced[event_id]
                    ).execute()
                    existing.update(body)
                    service.events().update(
                        calendarId=target_id, eventId=synced[event_id], body=existing
                    ).execute()
                    print(f"  ↻ Updated: {date_str}: {leave_summary} — "
                          f"leave {leave_dt.strftime('%-I:%M%p').lower()}, "
                          f"arrive {arrival_dt.strftime('%-I:%M%p').lower()} (~{drive_mins} min)")
                    updated += 1
                    continue
                except Exception:
                    pass  # event was deleted, fall through to create

            new_event = service.events().insert(calendarId=target_id, body=body).execute()
            synced[event_id] = new_event["id"]
            print(f"  ✓ {date_str}: {leave_summary} — "
                  f"leave {leave_dt.strftime('%-I:%M%p').lower()}, "
                  f"arrive {arrival_dt.strftime('%-I:%M%p').lower()} (~{drive_mins} min)")
            created += 1

    save_synced(synced)
    print(f"\n{'[dry run] ' if args.dry_run else ''}Done: {created} created, {updated} updated, {skipped} unchanged.")

if __name__ == "__main__":
    main()
