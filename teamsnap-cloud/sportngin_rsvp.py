#!/usr/bin/env python3
"""
sportngin-rsvp.py — sync RSVP availability on SportNgin based on Kids Activities (Lynch) calendar.

For each upcoming SportNgin event:
  - If the date is in Kids Activities (Lynch) for the matching source calendar → GOING (yes)
  - If not → NOT GOING (no)

No Playwright needed — uses direct cookie-based API auth.

Usage:
  python3 sportngin-rsvp.py               # dry run
  python3 sportngin-rsvp.py --apply       # set RSVPs
  python3 sportngin-rsvp.py --days 60
"""

import argparse
import datetime
import json
import os
import re
import sys
import warnings
from pathlib import Path

import requests
warnings.filterwarnings("ignore", category=FutureWarning)

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Config ────────────────────────────────────────────────────────────────────
import sportsync_config
import telemetry

CONFIG_DIR    = Path(os.environ["SCRIPT_CONFIG_DIR"]) if "SCRIPT_CONFIG_DIR" in os.environ else Path.home() / ".config" / "google-tasks-sync"
CREDS_FILE    = CONFIG_DIR / "sportngin-credentials.json"
GCAL_CREDS    = CONFIG_DIR / "credentials.json"
GCAL_TOKEN    = CONFIG_DIR / "calendar-token.json"
GCAL_SCOPES   = ["https://www.googleapis.com/auth/calendar"]
TARGET_CAL    = sportsync_config.load()["target_calendar"]
SN_BASE       = "https://api.sportngin.com"
SN_LOGIN      = "https://user.sportngin.com/users/sign_in"

RESPONSE_LABEL     = {"yes": "🟢 Going", "no": "🔴 Not Going", "maybe": "🟡 Maybe"}
RESPECT_MANUAL_YES = sportsync_config.load()["family"].get("respect_manual_yes", False)
# ─────────────────────────────────────────────────────────────────────────────


def sn_login(email, password):
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # Step 1: get authenticity_token
    r = session.get(SN_LOGIN)
    m = re.search(r'name="authenticity_token"[^>]+value="([^"]+)"', r.text) or \
        re.search(r'authenticity_token[^>]+value="([^"]+)"', r.text)
    token = m.group(1) if m else ""

    # Step 2: submit email
    r2 = session.post(SN_LOGIN, data={
        "authenticity_token": token,
        "user[login]": email,
        "commit": "Continue",
    }, allow_redirects=True)

    # Step 3: get fresh token and submit password
    m2 = re.search(r'name="authenticity_token"[^>]+value="([^"]+)"', r2.text) or \
         re.search(r'authenticity_token[^>]+value="([^"]+)"', r2.text)
    token2 = m2.group(1) if m2 else token
    login_m = re.search(r'name="user\[login\]"[^>]+value="([^"]+)"', r2.text)
    login_val = login_m.group(1) if login_m else email

    r3 = session.post(SN_LOGIN, data={
        "authenticity_token": token2,
        "user[login]": login_val,
        "user[password]": password,
        "commit": "Sign In",
    }, allow_redirects=True)

    if "sign_in" in r3.url and "my.sportsengine" not in r3.url:
        raise RuntimeError("SportNgin login failed — check credentials")

    # Establish API session
    session.post(f"{SN_BASE}/firebase/auth", json={})
    return session


def get_sn_events(session, team_id, days):
    now = datetime.datetime.now(datetime.timezone.utc)
    tz  = datetime.timezone(datetime.timedelta(hours=-4))  # EDT
    start = now.astimezone(tz).strftime("%Y-%m-%dT00:00:00-04:00")
    end   = (now + datetime.timedelta(days=days)).astimezone(tz).strftime("%Y-%m-%dT23:59:59-04:00")

    r = session.get(f"{SN_BASE}/v3/calendar/team/{team_id}", params={
        "start_date": start,
        "end_date":   end,
        "order_by":   "start_date",
        "page":       1,
        "per_page":   100,
        "show_event_attendees": 1,
    })
    r.raise_for_status()
    return r.json().get("result", [])


def set_rsvp(session, event_id, persona_id, team_id, response):
    r = session.post(f"{SN_BASE}/v3/rsvp", json={
        "event_id":   event_id,
        "persona_id": persona_id,
        "team_id":    team_id,
        "response":   response,
    })
    r.raise_for_status()
    return r.json()


def get_gcal_service():
    creds = None
    if GCAL_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(GCAL_TOKEN, GCAL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GCAL_CREDS, GCAL_SCOPES)
            creds = flow.run_local_server(port=0)
        GCAL_TOKEN.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def load_cal_dates(service, source_name, time_min, time_max):
    """Return set of 'YYYY-MM-DD' dates that have events synced from source_name."""
    cal_list = service.calendarList().list().execute()
    cal_id   = next(
        (c["id"] for c in cal_list.get("items", [])
         if c.get("summary", "").strip() == TARGET_CAL),
        None
    )
    if not cal_id:
        print(f"✗ Calendar '{TARGET_CAL}' not found.", file=sys.stderr)
        return set()

    events = service.events().list(
        calendarId=cal_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        maxResults=500,
    ).execute().get("items", [])

    dates = set()
    for e in events:
        desc = (e.get("description") or "").strip()
        start = e.get("start", {})
        date  = (start.get("dateTime") or start.get("date") or "")[:10]
        for line in desc.splitlines():
            if line.startswith("[Synced from:") and line.endswith("]"):
                src = line[len("[Synced from:"):].rstrip("]").strip()
                if src == source_name and date:
                    dates.add(date)
    return dates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--days",  type=int, default=60)
    args = parser.parse_args()

    sn_teams = sportsync_config.teams("sportngin")
    if not sn_teams:
        print("No sportngin teams in config.json — nothing to do.")
        return

    creds = json.loads(CREDS_FILE.read_text())
    print(f"Logging in to SportNgin as {creds['email']}...", file=sys.stderr)
    sn = sn_login(creds["email"], creds["password"])

    now      = datetime.datetime.now(datetime.timezone.utc)
    time_min = now.isoformat()
    time_max = (now + datetime.timedelta(days=args.days)).isoformat()

    gcal            = get_gcal_service()
    any_changes     = False
    total_rsvps     = 0
    all_changes     = []   # [{kid, date, date_label, team, team_emoji, event, going}]
    all_rsvps_list  = []   # same shape — full upcoming status

    for team_cfg in sn_teams:
        cal_source  = team_cfg["calendar_name"]
        team_id     = team_cfg["sportngin_team_id"]
        persona_id  = team_cfg["sportngin_persona_id"]
        sport_emoji = team_cfg.get("emoji", "🏅")
        kids        = team_cfg.get("kids", [])
        kid         = kids[0] if kids else "?"

        cal_dates = load_cal_dates(gcal, cal_source, time_min, time_max)
        print(f"Kids Activities has {len(cal_dates)} '{cal_source}' date(s)", file=sys.stderr)

        events = get_sn_events(sn, team_id, args.days)
        print(f"SportNgin: {len(events)} upcoming events for {cal_source}", file=sys.stderr)

        team_changes = []
        for ev in events:
            ev_id    = ev["id"]
            title    = ev.get("title", "(no title)")
            start_dt = ev.get("start_date_time", "")
            ev_date  = start_dt[:10]
            if not ev_date:
                continue

            going  = ev_date in cal_dates
            target = "yes" if going else "no"

            attendees   = ev.get("event_attendees", [])
            my_attendee = next((a for a in attendees if str(a.get("persona_id")) == str(persona_id)), None)
            current     = my_attendee.get("response", "") if my_attendee else ""

            if RESPECT_MANUAL_YES and target == "no" and current == "yes":
                going = True  # honour manual yes

            try:
                date_label = datetime.datetime.fromisoformat(start_dt.replace("Z", "+00:00")).strftime("%a %b %-d")
            except Exception:
                date_label = ev_date

            all_rsvps_list.append({
                "kid": kid, "date": ev_date, "date_label": date_label,
                "team": cal_source, "team_emoji": sport_emoji,
                "event": title, "going": going,
            })

            if RESPECT_MANUAL_YES and target == "no" and current == "yes":
                continue
            if current == target:
                continue

            if args.apply:
                set_rsvp(sn, ev_id, persona_id, team_id, target)

            team_changes.append((kid, date_label, title, target, ev_date))
            all_changes.append({
                "kid": kid, "date": ev_date, "date_label": date_label,
                "team": cal_source, "team_emoji": sport_emoji,
                "event": title, "going": going,
                "prev_going": current == "yes",
            })

        if team_changes:
            any_changes  = True
            total_rsvps += len(team_changes)
            if not os.environ.get("SCRIPT_CONFIG_DIR"):
                print(f"{sport_emoji} {cal_source}")
                for kid_name, date_label, title, response, _ in team_changes:
                    status = "🟢" if response == "yes" else "🔴"
                    print(f"  {status} {kid_name} — {date_label} · {title}")
                print()

    if not os.environ.get("SCRIPT_CONFIG_DIR"):
        if not any_changes:
            print("No RSVP changes." if args.apply else "No RSVP changes needed.")

    if os.environ.get("SCRIPT_CONFIG_DIR"):
        import json as _json
        print(_json.dumps({"changes": all_changes, "all_rsvps": all_rsvps_list}))

    if args.apply:
        _cfg = sportsync_config.load()
        telemetry.ping(CONFIG_DIR, _cfg, "sportngin-rsvp", {
            "rsvps_set": total_rsvps,
        })


if __name__ == "__main__":
    main()
