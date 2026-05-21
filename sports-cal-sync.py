#!/usr/bin/env python3
"""
One-way sync: copy events from sports calendars → Kid Activities (Lynch)

Reads events from the source sports calendars and creates matching events
in Kid Activities (Lynch) if they don't already exist. Tracks synced event
IDs to avoid duplicates. Safe to run repeatedly.

Usage:
  python3 sports-cal-sync.py           # sync next 60 days
  python3 sports-cal-sync.py --days 90 # sync further ahead
  python3 sports-cal-sync.py --dry-run # preview without creating events
"""

import base64
import builtins
import email.mime.text
import json
import os
import argparse
import sys
import datetime
from pathlib import Path

_orig_print = builtins.print
def print(*args, **kwargs):
    if not sys.stdout.isatty():
        _orig_print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]", *args, **kwargs)
    else:
        _orig_print(*args, **kwargs)
builtins.print = print

import re as _re

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import sportsync_config

# ── Config ────────────────────────────────────────────────────────────────────

_cfg = sportsync_config.load()

SOURCE_CALENDAR_NAMES = [t["calendar_name"] for t in _cfg["teams"]]
TARGET_CALENDAR_NAME  = _cfg["target_calendar"]
NOTIFY_EMAIL          = _cfg["family"]["notify_email"]

CALENDAR_EMOJI = {t["calendar_name"]: t["emoji"] for t in _cfg["teams"] if t.get("emoji")}

# Build duration overrides and no-adjust set from config
DURATION_OVERRIDES = {}
NO_DURATION_ADJUST = set()
for _t in _cfg["teams"]:
    _name = _t["calendar_name"]
    if not _t.get("adjust_duration", True):
        NO_DURATION_ADJUST.add(_name)
    if _t.get("game_duration_minutes"):
        DURATION_OVERRIDES[(_name, "game")] = _t["game_duration_minutes"]
    if _t.get("practice_duration_minutes"):
        DURATION_OVERRIDES[(_name, "practice")] = _t["practice_duration_minutes"]

SCOPES           = ["https://www.googleapis.com/auth/calendar"]
CONFIG_DIR       = Path(os.environ["SCRIPT_CONFIG_DIR"]) if "SCRIPT_CONFIG_DIR" in os.environ else Path.home() / ".config" / "google-tasks-sync"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE       = CONFIG_DIR / "calendar-token.json"
SYNCED_FILE      = CONFIG_DIR / "sports-sync-ids.json"
BAYS_FIELDS_FILE = CONFIG_DIR / "bays-fields.json"

GMAIL_SCOPES     = ["https://www.googleapis.com/auth/gmail.send"]
GMAIL_TOKEN_FILE = CONFIG_DIR / "gmail-token.json"

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
    """Returns dict of { source_event_id: target_event_id }"""
    if SYNCED_FILE.exists():
        return json.loads(SYNCED_FILE.read_text())
    return {}

def save_synced(synced):
    SYNCED_FILE.write_text(json.dumps(synced, indent=2))

# ── Calendar lookup ───────────────────────────────────────────────────────────

def get_calendars_by_name(service):
    result = service.calendarList().list().execute()
    cals = {}
    for cal in result.get("items", []):
        name = cal.get("summary", "").strip()
        cals[name] = cal
    return cals

# ── Emoji prefixes ────────────────────────────────────────────────────────────

# CALENDAR_EMOJI is built from config above.

# Keyword → emoji applied to any event on the target calendar (first match wins)
KEYWORD_EMOJI = [
    ("rugby",        "🏉"),
    ("tennis",       "🎾"),
    ("trumpet",      "🎺"),
    ("trombone",     "🎺"),
    ("orthodontist", "🦷"),
]

ALL_KNOWN_EMOJIS = set(CALENDAR_EMOJI.values()) | {e for _, e in KEYWORD_EMOJI}

# ── Event helpers ─────────────────────────────────────────────────────────────

def get_events(service, cal_id, time_min, time_max):
    items      = []
    page_token = None
    while True:
        result = service.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=2500,
            pageToken=page_token,
        ).execute()
        items.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return items

MIN_PRACTICE_MINS = 60   # practices: at least 1 hour
MIN_GAME_MINS     = 90   # games: at least 90 minutes

# DURATION_OVERRIDES and NO_DURATION_ADJUST are built from config at top of file.

def enforce_min_duration(start, end, title, source_cal_name=""):
    """Return an adjusted end datetime if the event is shorter than the minimum."""
    if source_cal_name in NO_DURATION_ADJUST:
        return end
    if "dateTime" not in start or "dateTime" not in end:
        return end  # all-day events, leave alone

    title_lower = title.lower()
    is_practice = "practice" in title_lower or "prectice" in title_lower
    is_game     = any(x in title_lower for x in [" vs ", " at ", "game", "meet", "match"])

    if not is_practice and not is_game:
        return end

    event_type = "practice" if is_practice else "game"

    # Check for a per-calendar override first
    override = DURATION_OVERRIDES.get((source_cal_name, event_type))
    if override:
        target_mins = override
    else:
        target_mins = MIN_PRACTICE_MINS if is_practice else MIN_GAME_MINS

    start_dt = datetime.datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
    end_dt   = datetime.datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
    actual   = (end_dt - start_dt).total_seconds() / 60

    # For overrides, always set exact duration; for minimums, only adjust if too short
    if override or actual < target_mins:
        new_end_dt = start_dt + datetime.timedelta(minutes=target_mins)
        return {"dateTime": new_end_dt.isoformat(), "timeZone": end.get("timeZone", start.get("timeZone", "UTC"))}

    return end

def _load_bays_fields() -> dict:
    if BAYS_FIELDS_FILE.exists():
        return json.loads(BAYS_FIELDS_FILE.read_text())
    return {}

def _normalize(s: str) -> str:
    return _re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()

def resolve_location(raw: str, bays_fields: dict) -> str:
    """
    Look up a vague venue name against the BAYS field-address cache.
    Tries exact match first, then normalized/fuzzy substring match.
    Returns the street address if found, otherwise the original string.
    """
    if not raw or not bays_fields:
        return raw
    norm_raw = _normalize(raw)
    # Build normalized lookup on first call (keyed by normalized form → address)
    for key, addr in bays_fields.items():
        norm_key = _normalize(key)
        if norm_raw == norm_key:
            return addr
        # Substring: TeamSnap name is contained in BAYS name, or vice versa
        if norm_raw and norm_key and (norm_raw in norm_key or norm_key in norm_raw):
            return addr
    return raw

def build_copy(event, source_cal_name, bays_fields=None):
    """Build a copy of the event to insert into the target calendar."""
    title = event.get("summary", "(no title)")
    emoji = CALENDAR_EMOJI.get(source_cal_name)
    if emoji and not title.startswith(emoji):
        title = f"{emoji} {title}"
    start = event["start"]
    end   = enforce_min_duration(start, event["end"], title, source_cal_name)

    copy = {
        "summary": title,
        "start":   start,
        "end":     end,
    }
    if event.get("location"):
        resolved = resolve_location(event["location"], bays_fields or {})
        copy["location"] = resolved
    # Append source calendar name to description for traceability
    original_desc = event.get("description", "").strip()
    source_note   = f"[Synced from: {source_cal_name}]"
    copy["description"] = f"{original_desc}\n{source_note}".strip()
    return copy

def strip_emoji_prefix(title):
    """Remove a leading known emoji + space from a title, if present."""
    for emoji in ALL_KNOWN_EMOJIS:
        if title.startswith(emoji + " "):
            return title[len(emoji) + 1:]
    return title

def events_match(event, target_events):
    """Check if an identical event (same title + start) exists in target.
    Strips emoji prefixes from target titles so already-emojified copies are recognised."""
    title      = event.get("summary", "").strip().lower()
    start      = event.get("start", {})
    start_key  = start.get("dateTime") or start.get("date") or ""
    for t in target_events:
        t_title = strip_emoji_prefix(t.get("summary", "").strip()).lower()
        t_start = t.get("start", {})
        t_key   = t_start.get("dateTime") or t_start.get("date") or ""
        if t_title == title and t_key == start_key:
            return True
    return False

def normalize_dt(dt_dict):
    """Normalize a start/end dict to a UTC timestamp for comparison.
    Handles Z vs offset-based representations of the same moment."""
    if not dt_dict:
        return None
    s = dt_dict.get("dateTime") or dt_dict.get("date")
    if not s or "T" not in s:
        return s  # all-day event date string, compare as-is
    try:
        parsed = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return parsed.astimezone(datetime.timezone.utc).isoformat()
    except Exception:
        return s

def stamp_last_updated(event):
    """Add or refresh a 'Last synced' timestamp line in the event description."""
    desc  = event.get("description", "") or ""
    lines = [l for l in desc.splitlines() if not l.startswith("Last synced:")]
    lines.append(datetime.datetime.now().strftime("Last synced: %Y-%m-%d %I:%M %p"))
    event["description"] = "\n".join(lines).strip()
    return event

# ── Deletion email ────────────────────────────────────────────────────────────

def send_deletion_email(deleted):
    """Email NOTIFY_EMAIL listing events that were auto-deleted from the target calendar."""
    try:
        creds = None
        if GMAIL_TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, GMAIL_SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                GMAIL_TOKEN_FILE.write_text(creds.to_json())
            else:
                print("  ⚠ Gmail token unavailable — skipping deletion alert email.")
                return
        gmail = build("gmail", "v1", credentials=creds)
        lines = [
            "The following events were automatically removed from Kid Activities (Lynch)",
            "because they no longer exist in their source sports calendar:\n",
        ]
        for d in deleted:
            lines.append(f"  • {d['summary']}  ({d['start']})")
        body = "\n".join(lines)
        msg = email.mime.text.MIMEText(body)
        msg["to"]      = NOTIFY_EMAIL
        msg["subject"] = f"Sports sync: {len(deleted)} event(s) auto-deleted from Kid Activities"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"  ✓ Deletion alert sent to {NOTIFY_EMAIL}")
    except Exception as exc:
        print(f"  ⚠ Could not send deletion email: {exc}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",    type=int, default=60)
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't create events")
    args = parser.parse_args()

    bays_fields = _load_bays_fields()
    if bays_fields:
        print(f"Loaded {len(bays_fields)} BAYS field address(es) from cache.")

    creds   = get_credentials()
    service = build("calendar", "v3", credentials=creds)

    now      = datetime.datetime.now(tz=datetime.timezone.utc)
    time_min = now.isoformat()
    time_max = (now + datetime.timedelta(days=args.days)).isoformat()

    all_cals = get_calendars_by_name(service)

    # Resolve target calendar
    if TARGET_CALENDAR_NAME not in all_cals:
        print(f"✗ Target calendar '{TARGET_CALENDAR_NAME}' not found.")
        return
    target_cal    = all_cals[TARGET_CALENDAR_NAME]
    target_cal_id = target_cal["id"]

    # Fetch existing target events (for duplicate checking)
    target_events = get_events(service, target_cal_id, time_min, time_max)

    synced           = load_synced()
    created          = 0
    updated          = 0
    skipped          = 0
    not_found        = []
    seen_source_ids  = set()   # all source event IDs present in the current window
    events_per_cal   = {}      # source_name → count of events seen (zero-event guard)

    for source_name in SOURCE_CALENDAR_NAMES:
        if source_name not in all_cals:
            not_found.append(source_name)
            continue

        source_cal_id = all_cals[source_name]["id"]
        source_events = get_events(service, source_cal_id, time_min, time_max)
        events_per_cal[source_name] = len(source_events)

        for event in source_events:
            event_id  = event["id"]
            seen_source_ids.add(event_id)
            summary   = event.get("summary", "(no title)")
            start     = event.get("start", {})
            start_str = start.get("dateTime", start.get("date", ""))[:16]

            # Already synced — check if source has changed and update if so
            if event_id in synced and synced[event_id] not in ("pre-existing", "skipped-no-location"):
                target_event_id = synced[event_id]
                try:
                    target = service.events().get(
                        calendarId=target_cal_id, eventId=target_event_id
                    ).execute()
                except Exception:
                    # Target event was deleted — re-create it
                    del synced[event_id]
                    target = None

                if target:
                    copy = build_copy(event, source_name, bays_fields)
                    time_changed     = (normalize_dt(target.get("start")) != normalize_dt(copy.get("start")) or
                                        normalize_dt(target.get("end"))   != normalize_dt(copy.get("end")))
                    location_changed = (target.get("location") or "").strip() != (copy.get("location") or "").strip()
                    title_changed    = target.get("summary") != copy.get("summary")
                    any_changed      = time_changed or location_changed or title_changed

                    if any_changed:
                        if not args.dry_run:
                            target.update(copy)
                            stamp_last_updated(target)
                            service.events().update(
                                calendarId=target_cal_id,
                                eventId=target_event_id,
                                body=target
                            ).execute()
                        if time_changed or location_changed:
                            what = []
                            if time_changed:     what.append("time")
                            if location_changed: what.append("location")
                            label = f"[dry run] would update" if args.dry_run else "↻ Updated"
                            print(f"  {label} ({', '.join(what)}): {summary} ({start_str}) ← {source_name}")
                            updated += 1
                        else:
                            skipped += 1  # title/emoji-only change — applied silently
                    else:
                        skipped += 1
                    continue

            # Not yet synced — skip if already exists in target
            if event_id in synced:
                if synced[event_id] == "pre-existing":
                    # Add emoji to pre-existing events that predate sync tracking
                    emoji = CALENDAR_EMOJI.get(source_name)
                    if emoji:
                        src_title    = event.get("summary", "")
                        src_start_key = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date") or ""
                        for t in target_events:
                            t_start_key = (t.get("start") or {}).get("dateTime") or (t.get("start") or {}).get("date") or ""
                            if t_start_key == src_start_key and t.get("summary", "").strip().lower() == src_title.strip().lower():
                                new_title  = f"{emoji} {src_title}"
                                t["summary"] = new_title
                                if args.dry_run:
                                    print(f"  [dry run] would add emoji: {new_title} ({src_start_key[:16]}) ← {source_name}")
                                else:
                                    stamp_last_updated(t)
                                    service.events().update(calendarId=target_cal_id, eventId=t["id"], body=t).execute()
                                    synced[event_id] = t["id"]
                                    print(f"  ↻ Updated: {new_title} ({src_start_key[:16]}) ← {source_name}")
                                updated += 1
                                break
                        else:
                            skipped += 1
                    else:
                        skipped += 1
                else:
                    skipped += 1
                continue

            if events_match(event, target_events):
                synced[event_id] = "pre-existing"
                skipped += 1
                continue

            if args.dry_run:
                print(f"  [dry run] would copy: {summary} ({start_str}) ← {source_name}")
                created += 1
                continue

            copy = build_copy(event, source_name, bays_fields)
            new_event = service.events().insert(calendarId=target_cal_id, body=copy).execute()
            synced[event_id] = new_event["id"]
            target_events.append(new_event)
            print(f"  ✓ Copied: {summary} ({start_str}) ← {source_name}")
            created += 1

    # ── Keyword emoji pass: update manually-added events on the target calendar ─
    kw_updated = 0
    for event in target_events:
        title = event.get("summary", "")
        if not title or title[0] in ALL_KNOWN_EMOJIS:
            continue  # already has an emoji prefix
        for keyword, emoji in KEYWORD_EMOJI:
            if keyword in title.lower():
                new_title = f"{emoji} {title}"
                event["summary"] = new_title
                if args.dry_run:
                    print(f"  [dry run] would add emoji: {new_title}")
                else:
                    stamp_last_updated(event)
                    service.events().update(calendarId=target_cal_id, eventId=event["id"], body=event).execute()
                    print(f"  ↻ Emoji: {new_title}")
                kw_updated += 1
                break

    # ── Deletion pass: remove target events whose source event no longer exists ──
    deleted  = []
    if not_found:
        # Skip deletion if any source calendar was unreachable — can't distinguish
        # "event deleted" from "calendar inaccessible"
        print(f"\n⚠ Skipping deletion check — {len(not_found)} source calendar(s) not found: {', '.join(not_found)}")
    else:
        skip_values = {"pre-existing", "skipped-no-location"}
        orphan_src_ids = [
            src_id for src_id, tgt_id in list(synced.items())
            if src_id not in seen_source_ids and tgt_id not in skip_values
        ]
        for src_id in orphan_src_ids:
            tgt_id = synced[src_id]
            try:
                tgt_event = service.events().get(calendarId=target_cal_id, eventId=tgt_id).execute()
            except Exception:
                del synced[src_id]
                continue

            # Zero-event guard: if the source calendar returned 0 events this run,
            # skip deletion — could be a transient API issue, not a real cancellation
            tgt_desc   = (tgt_event.get("description") or "")
            source_cal = None
            for line in tgt_desc.splitlines():
                if line.startswith("[Synced from:") and line.endswith("]"):
                    source_cal = line[len("[Synced from:"):].rstrip("]").strip()
                    break
            if source_cal and events_per_cal.get(source_cal, -1) == 0:
                print(f"  ⚠ Skipping deletion — {source_cal} returned 0 events this run (possible API issue)")
                continue

            tgt_start     = tgt_event.get("start", {})
            tgt_start_str = tgt_start.get("dateTime") or tgt_start.get("date") or ""
            # Only auto-delete future events; past ones just fall out of the window naturally
            if tgt_start_str >= time_min[:10]:
                summary = tgt_event.get("summary", "(no title)")
                label   = "[dry run] would delete" if args.dry_run else "🗑 Deleted"
                print(f"  {label}: {summary} ({tgt_start_str[:16]}) — no longer in source calendar")
                if not args.dry_run:
                    try:
                        service.events().delete(calendarId=target_cal_id, eventId=tgt_id).execute()
                    except Exception:
                        pass  # already deleted externally
                    del synced[src_id]
                deleted.append({"summary": summary, "start": tgt_start_str[:16]})
            else:
                del synced[src_id]

    save_synced(synced)

    if deleted and not args.dry_run:
        send_deletion_email(deleted)

    print(f"\n{'[dry run] ' if args.dry_run else ''}Done: {created} copied, {updated} updated, {skipped} unchanged, {kw_updated} keyword-emojified, {len(deleted)} deleted.")
    if not_found:
        print(f"  ✗ Calendars not found: {', '.join(not_found)}")

if __name__ == "__main__":
    main()
