#!/usr/bin/env python3
"""
Weekly driving plan generator.

Reads upcoming events from Kids Activities (Lynch) and:
  1. Creates 🚗 driving events on Erin Shea Lynch calendar
  2. Prints text reminders for the Sunday weekly email

Rules applied:
  B7/8 Falcons      → 🚗 leave event to arrive 5 min early (Erin drives twins)
  Colin trumpet     → 🚗 block: leave home → sit at lesson → drive home (489 Belmont St)
  Thursday track    → 🚗 drive to + 🚗 drive home (Pgm B Distance or Outdoor WTC)
  B4 Bobcats        → skip (Tom drives Colin)
  Tennis events     → flag: text Koglers to coordinate driving
  Boys 3/4 practice → flag: text Derek to coordinate driving
  Thursday track    → also flag: text track carpool group

Usage:
  python3 driving-plan.py           # dry run (14-day window)
  python3 driving-plan.py --apply   # create/update calendar events
  python3 driving-plan.py --days 21
"""

import json
import math
import os
import re
import ssl
import time
import argparse
import datetime
import urllib.request
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

import certifi
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Config ────────────────────────────────────────────────────────────────────

SCOPES           = ["https://www.googleapis.com/auth/calendar"]
CONFIG_DIR       = Path(os.environ["SCRIPT_CONFIG_DIR"]) if "SCRIPT_CONFIG_DIR" in os.environ else Path.home() / ".config" / "google-tasks-sync"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE       = CONFIG_DIR / "calendar-token.json"
DRIVE_CONFIG     = CONFIG_DIR / "drive-config.json"
GEOCODE_CACHE    = CONFIG_DIR / "geocode-cache.json"
SYNCED_FILE      = CONFIG_DIR / "driving-plan-synced.json"

KIDS_CAL_NAME    = "Kid Activities (Lynch)"
TRUMPET_ADDRESS  = "489 Belmont St, Belmont, MA"

# Arrival time patterns (parsed from game event descriptions for warmup time)
ARRIVAL_PATTERNS = [
    r'arrival\s+time\s*[:\-]?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm))',
    r'(?:players?\s+)?(?:arrive|arrival|report|check[\s-]in|be there|show up|warmups?)\s+(?:by|at|@)?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
    r'(?:arrive|arrival|report|be there)\s*[:\-]\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
    r'(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s+(?:arrival|arrive|warmup|warm[\s-]up)',
]

# Override drive times (minutes) for specific locations — takes priority over haversine estimate.
# Key: substring to match against event location (case-insensitive).
DRIVE_TIME_OVERRIDES = {
    "72 maple":           10,   # B7/8 practice field
    "489 belmont":        10,   # Colin trumpet
    "19 athletic field":  30,   # track practice (rush hour + carpooling)
    "457 walnut":         30,   # alternate track practice location
}
LOCAL_TZ         = ZoneInfo("America/New_York")

# Source calendar → driving rule
SOURCE_RULES = {
    "B7/8 Falcons":                  "b78",
    "B4 Bobcats":                    "skip",
    "2026 Pgm B Distance":           "thursday_track",
    "2026 Outdoor WTC":              "thursday_track",
    "NEFC Metro North Boys 2016 Red": None,
    "Boys 3/4":                      None,
}

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
    return json.loads(SYNCED_FILE.read_text()) if SYNCED_FILE.exists() else {}

def save_synced(synced):
    SYNCED_FILE.write_text(json.dumps(synced, indent=2))

# ── Geocoding ─────────────────────────────────────────────────────────────────

def load_geocache():
    return json.loads(GEOCODE_CACHE.read_text()) if GEOCODE_CACHE.exists() else {}

def save_geocache(cache):
    GEOCODE_CACHE.write_text(json.dumps(cache, indent=2))

def geocode(address, cache):
    if re.search(r'\btba\b|\btbd\b', address, re.IGNORECASE):
        return None
    key = address.strip().lower()
    if key in cache:
        return cache[key]
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": address, "format": "json", "limit": 1}
    )
    req = urllib.request.Request(url, headers={"User-Agent": "driving-plan/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            results = json.loads(resp.read())
        if results:
            lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
            cache[key] = (lat, lon)
            time.sleep(1)
            return (lat, lon)
        time.sleep(1)
    except Exception as e:
        print(f"  Geocode error for '{address}': {e}")
    return None

def drive_minutes(origin, dest):
    lat1, lon1 = origin
    lat2, lon2 = dest
    R    = 3959
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a    = (math.sin(dlat / 2) ** 2 +
            math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
            math.sin(dlon / 2) ** 2)
    miles = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)) * 1.35
    return max(5, round((miles / 35) * 60))

def drive_minutes_for(address, origin, dest):
    """Return drive time, applying manual overrides before falling back to haversine."""
    addr_lower = address.lower()
    for key, mins in DRIVE_TIME_OVERRIDES.items():
        if key in addr_lower:
            return mins
    return drive_minutes(origin, dest)

# ── Arrival time parsing (for weekend games) ──────────────────────────────────

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

def is_game(title):
    return any(x in title.lower() for x in [" vs ", " at ", "game", "meet", "match"])

# ── Event helpers ─────────────────────────────────────────────────────────────

def parse_source(desc):
    """Extract source calendar name from '[Synced from: X]' in description."""
    for line in (desc or "").splitlines():
        if line.startswith("[Synced from:") and line.endswith("]"):
            return line[len("[Synced from:"):].rstrip("]").strip()
    return None

def parse_dt(event, field="start"):
    dt_str = event.get(field, {}).get("dateTime")
    if not dt_str:
        return None
    return datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(LOCAL_TZ)

def classify(event):
    """Return driving rule for a Kids Activities event, or None to skip."""
    title  = (event.get("summary") or "").lower()
    desc   = event.get("description") or ""
    source = parse_source(desc)

    # Keyword rules (manually-added events)
    if "tennis" in title:
        return "tennis"
    if "trumpet" in title and "colin" in title:
        return "trumpet_colin"

    # Source-based rules
    rule = SOURCE_RULES.get(source)
    if rule == "skip" or rule is None:
        return None

    if rule == "b78":
        return "b78"

    if rule == "thursday_track":
        start_dt = parse_dt(event)
        if start_dt and start_dt.weekday() == 3:  # Thursday
            return "thursday_track"
        return None

    # Boys 3/4 practice text reminder
    if source == "Boys 3/4" and "practice" in title:
        return "lacrosse_practice"

    return None

def make_event_body(summary, start_dt, end_dt, location=None, desc=None):
    tz = "America/New_York"
    body = {
        "summary": summary,
        "start":   {"dateTime": start_dt.isoformat(), "timeZone": tz},
        "end":     {"dateTime": end_dt.isoformat(),   "timeZone": tz},
    }
    if location:
        body["location"] = location
    if desc:
        body["description"] = desc
    return body

def upsert_event(service, cal_id, existing_id, body, dry_run, label):
    if dry_run:
        start = datetime.datetime.fromisoformat(body["start"]["dateTime"])
        print(f"    [dry run] {label}: {body['summary']} at {start.strftime('%-I:%M%p').lower()}")
        return None

    if existing_id:
        try:
            existing = service.events().get(calendarId=cal_id, eventId=existing_id).execute()
            existing.update(body)
            service.events().update(calendarId=cal_id, eventId=existing_id, body=existing).execute()
            start = datetime.datetime.fromisoformat(body["start"]["dateTime"])
            print(f"    ↻ Updated: {body['summary']} at {start.strftime('%-I:%M%p').lower()}")
            return existing_id
        except Exception:
            pass  # deleted, fall through to create

    new = service.events().insert(calendarId=cal_id, body=body).execute()
    start = datetime.datetime.fromisoformat(body["start"]["dateTime"])
    print(f"    ✓ Created: {body['summary']} at {start.strftime('%-I:%M%p').lower()}")
    return new["id"]

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Create/update calendar events")
    parser.add_argument("--days",  type=int, default=14)
    args = parser.parse_args()

    cfg          = json.loads(DRIVE_CONFIG.read_text())
    home_address = cfg["home_address"]
    erin_cal     = cfg["driver_calendar"]   # "Erin Shea"

    geocache    = load_geocache()
    home_coords = geocode(home_address, geocache)
    save_geocache(geocache)
    if not home_coords:
        print(f"✗ Could not geocode home: {home_address}")
        return

    # Pre-geocode trumpet address
    trumpet_coords = geocode(TRUMPET_ADDRESS, geocache)
    save_geocache(geocache)

    creds   = get_credentials()
    service = build("calendar", "v3", credentials=creds)

    now      = datetime.datetime.now(tz=datetime.timezone.utc)
    time_min = now.isoformat()
    time_max = (now + datetime.timedelta(days=args.days)).isoformat()

    # Resolve calendars
    all_cals = {c.get("summary","").strip(): c
                for c in service.calendarList().list().execute()["items"]}

    if KIDS_CAL_NAME not in all_cals:
        print(f"✗ Calendar '{KIDS_CAL_NAME}' not found.")
        return
    if erin_cal not in all_cals:
        print(f"✗ Calendar '{erin_cal}' not found.")
        return

    kids_cal_id = all_cals[KIDS_CAL_NAME]["id"]
    erin_cal_id = all_cals[erin_cal]["id"]

    events = service.events().list(
        calendarId=kids_cal_id,
        timeMin=time_min, timeMax=time_max,
        singleEvents=True, orderBy="startTime", maxResults=500,
    ).execute().get("items", [])

    synced = load_synced()

    # Collect text reminders
    text_reminders = []
    # Deduplicate Thursday track by date (Pgm B + Outdoor WTC same day → one driving block)
    thursday_track_dates_done = set()

    created = updated = skipped = 0

    for event in events:
        ev_id    = event["id"]
        title    = event.get("summary", "(no title)")
        location = (event.get("location") or "").strip()
        start_dt = parse_dt(event, "start")
        end_dt   = parse_dt(event, "end")
        rule     = classify(event)

        if not rule or not start_dt:
            continue

        date_str = start_dt.strftime("%a %b %-d")

        # ── Text-only reminders ───────────────────────────────────────────────
        if rule == "tennis":
            text_reminders.append(f"  🎾 {date_str}: Text Koglers to coordinate driving for twins tennis")
            continue

        if rule == "lacrosse_practice":
            text_reminders.append(f"  🥍 {date_str}: Text Derek to coordinate driving for Boys 3/4 practice")
            continue

        if rule == "thursday_track":
            date_key = start_dt.date().isoformat()
            if date_key not in thursday_track_dates_done:
                text_reminders.append(f"  👟 {date_str}: Text track carpool group for Thursday practice")

        # ── Calendar events ───────────────────────────────────────────────────
        prev = synced.get(ev_id, {})

        if rule == "b78":
            # For games: use warmup arrival time from description; for practices: arrive 5 min early
            if not location:
                print(f"  ⚠ No location for B7/8 event: {title} ({date_str}) — skipping")
                continue
            dest_coords = geocode(location, geocache)
            save_geocache(geocache)
            if not dest_coords:
                print(f"  ⚠ Could not geocode '{location}' — skipping")
                continue
            drive_mins = drive_minutes_for(location, home_coords, dest_coords)

            desc = event.get("description") or ""
            if is_game(title):
                arrive_dt = parse_arrival_time(desc, start_dt)
                if arrive_dt:
                    arrive_note = f"warmup arrival: {arrive_dt.strftime('%-I:%M%p').lower()}"
                else:
                    arrive_dt   = start_dt - datetime.timedelta(minutes=15)
                    arrive_note = "15 min before game start (no arrival time in description)"
            else:
                arrive_dt   = start_dt - datetime.timedelta(minutes=5)
                arrive_note = "5 min before practice start"
            leave_dt = arrive_dt - datetime.timedelta(minutes=drive_mins)

            print(f"  B7/8 {date_str}: {title}")
            body = make_event_body(
                f"🚗 Leave for {title}",
                leave_dt, arrive_dt, location,
                f"Drive ~{drive_mins} min — {arrive_note}.\n[Auto-generated by driving-plan.py]"
            )
            new_id = upsert_event(service, erin_cal_id, prev.get("leave"), body, not args.apply, date_str)
            if new_id:
                synced[ev_id] = {"leave": new_id}
                created += 1
            elif args.apply:
                skipped += 1
            else:
                created += 1

        elif rule == "trumpet_colin":
            if not trumpet_coords:
                print(f"  ⚠ Could not geocode trumpet address — skipping")
                continue
            drive_there = drive_minutes_for(TRUMPET_ADDRESS, home_coords, trumpet_coords)
            drive_back  = drive_minutes_for(TRUMPET_ADDRESS, trumpet_coords, home_coords)
            leave_dt    = start_dt - datetime.timedelta(minutes=drive_there)
            return_dt   = end_dt   + datetime.timedelta(minutes=drive_back) if end_dt else None

            print(f"  Trumpet {date_str}: {title}")
            if not return_dt:
                print(f"    ⚠ No end time for trumpet event, skipping return leg")
                continue
            body = make_event_body(
                f"🚗 Colin trumpet",
                leave_dt, return_dt, TRUMPET_ADDRESS,
                f"Drive ~{drive_there} min there, ~{drive_back} min back.\n"
                f"Lesson: {start_dt.strftime('%-I:%M%p').lower()} – {end_dt.strftime('%-I:%M%p').lower()}\n"
                f"[Auto-generated by driving-plan.py]"
            )
            new_id = upsert_event(service, erin_cal_id, prev.get("block"), body, not args.apply, date_str)
            if new_id:
                synced[ev_id] = {"block": new_id}
                created += 1
            elif args.apply:
                skipped += 1
            else:
                created += 1

        elif rule == "thursday_track":
            date_key = start_dt.date().isoformat()
            if date_key in thursday_track_dates_done:
                skipped += 1
                continue
            thursday_track_dates_done.add(date_key)

            if not location:
                print(f"  ⚠ No location for track event: {title} ({date_str}) — skipping")
                continue
            dest_coords = geocode(location, geocache)
            save_geocache(geocache)
            if not dest_coords:
                print(f"  ⚠ Could not geocode '{location}' — skipping")
                continue
            drive_there = drive_minutes_for(location, home_coords, dest_coords)
            drive_back  = drive_minutes_for(location, dest_coords, home_coords)
            leave_dt    = start_dt - datetime.timedelta(minutes=drive_there)
            return_dt   = end_dt   + datetime.timedelta(minutes=drive_back) if end_dt else None

            print(f"  Thursday track {date_str}: {title}")
            body_there = make_event_body(
                "🚗 Drive to track practice",
                leave_dt, start_dt, location,
                f"Drive ~{drive_there} min. Delete if someone else is driving there.\n[Auto-generated by driving-plan.py]"
            )
            body_back = make_event_body(
                "🚗 Drive home from track practice",
                end_dt, return_dt, location,
                f"Drive ~{drive_back} min. Delete if someone else is driving back.\n[Auto-generated by driving-plan.py]"
            ) if return_dt else None

            ids = prev if isinstance(prev, dict) else {}
            id_there = upsert_event(service, erin_cal_id, ids.get("there"), body_there, not args.apply, date_str)
            id_back  = upsert_event(service, erin_cal_id, ids.get("back"),  body_back,  not args.apply, date_str) if body_back else None
            if id_there or id_back:
                synced[ev_id] = {"there": id_there, "back": id_back}
                created += 1
            elif args.apply:
                skipped += 1
            else:
                created += 1

    save_synced(synced)
    save_geocache(geocache)

    # ── Summary output (for email) ────────────────────────────────────────────
    print(f"\n{'Applied' if args.apply else 'Dry run'}: {created} driving events, {skipped} unchanged.\n")

    if text_reminders:
        print("Texts to send this week:")
        for r in text_reminders:
            print(r)

    return text_reminders


if __name__ == "__main__":
    main()
