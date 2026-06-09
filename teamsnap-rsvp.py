#!/usr/bin/env python3
"""
Sync RSVP availability on TeamSnap based on Kids Activities (Lynch) calendar.

Outputs an HTML email with three sections:
  1. RSVP changes made this run (the diff)
  2. All upcoming confirmed events, summarized by kid
  3. What changed in Kids Activities calendar since last run

Usage:
  python3 teamsnap-rsvp.py               # dry run (preview only, no RSVP writes)
  python3 teamsnap-rsvp.py --apply       # actually set RSVPs + save calendar snapshot
  python3 teamsnap-rsvp.py --days 30     # look ahead N days (default 60)
"""

import argparse
import datetime
import json
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import requests

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import sportsync_config
import telemetry

# ── Config ────────────────────────────────────────────────────────────────────

_cfg = sportsync_config.load()

CONFIG_DIR    = Path(os.environ["SCRIPT_CONFIG_DIR"]) if "SCRIPT_CONFIG_DIR" in os.environ else Path.home() / ".config" / "google-tasks-sync"
CREDS_FILE    = CONFIG_DIR / "teamsnap-credentials.json"
TOKEN_FILE    = CONFIG_DIR / "teamsnap-token.json"
SNAPSHOT_FILE     = CONFIG_DIR / "rsvp-cal-snapshot.json"
RSVP_LOG_FILE     = CONFIG_DIR / "rsvp-changes.log"
SEASON_NUDGE_FILE = CONFIG_DIR / "season-nudge-sent.json"

API_BASE     = "https://api.teamsnap.com/v3"
AUTH_URL     = "https://auth.teamsnap.com/oauth/authorize"
TOKEN_URL    = "https://auth.teamsnap.com/oauth/token"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"

GCAL_SCOPES     = ["https://www.googleapis.com/auth/calendar"]
GCAL_CREDS_FILE = CONFIG_DIR / "credentials.json"
GCAL_TOKEN_FILE = CONFIG_DIR / "calendar-token.json"
TARGET_CALENDAR = _cfg["target_calendar"]

TEAM_CALENDAR_MAP = {
    t["teamsnap_team_id"]: t["calendar_name"]
    for t in sportsync_config.teams("teamsnap")
    if t.get("teamsnap_team_id")
}
TEAM_EMOJI         = {t["calendar_name"]: t["emoji"] for t in _cfg["teams"] if t.get("emoji")}
KIDS_LAST_NAME     = _cfg["family"]["last_name"]
KIDS_FIRST_NAMES   = sportsync_config.kid_names()
KID_COLOR          = sportsync_config.kid_colors()
RESPECT_MANUAL_YES = _cfg["family"].get("respect_manual_yes", False)

# ── TeamSnap Auth ─────────────────────────────────────────────────────────────

def get_ts_token():
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())["access_token"]

    creds  = json.loads(CREDS_FILE.read_text())
    params = {
        "client_id":     creds["client_id"],
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "scope":         "read write",
    }
    url = f"{AUTH_URL}?{urlencode(params)}"
    print("Opening browser for TeamSnap authorization...", file=sys.stderr)
    webbrowser.open(url)
    code = input("Paste the authorization code here: ").strip()

    resp = requests.post(TOKEN_URL, data={
        "client_id":     creds["client_id"],
        "client_secret": creds["client_secret"],
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  REDIRECT_URI,
    })
    resp.raise_for_status()
    token = resp.json()
    TOKEN_FILE.write_text(json.dumps(token, indent=2))
    return token["access_token"]

# ── TeamSnap API helpers ──────────────────────────────────────────────────────

def ts_get(token, url, params=None):
    if not url.startswith("http"):
        url = f"{API_BASE}{url}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
    resp.raise_for_status()
    return resp.json()

def ts_put(token, url, fields):
    body = {"template": {"data": [{"name": k, "value": v} for k, v in fields.items()]}}
    resp = requests.put(url, headers={"Authorization": f"Bearer {token}"}, json=body)
    resp.raise_for_status()
    return resp.json()

def parse_items(data):
    items = []
    for item in data.get("collection", {}).get("items", []):
        d = {f["name"]: f["value"] for f in item.get("data", [])}
        d["_href"]  = item.get("href", "")
        d["_links"] = {l["rel"]: l["href"] for l in item.get("links", [])}
        items.append(d)
    return items

# ── Google Calendar helpers ───────────────────────────────────────────────────

def get_gcal_service():
    creds = None
    if GCAL_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(GCAL_TOKEN_FILE, GCAL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GCAL_CREDS_FILE, GCAL_SCOPES)
            creds = flow.run_local_server(port=0)
        GCAL_TOKEN_FILE.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def load_cal_dates_by_source(service, time_min, time_max):
    """Return {source_cal_name: set of 'YYYY-MM-DD'} from Kids Activities (Lynch)."""
    cal_list = service.calendarList().list().execute()
    cal_id   = next(
        (c["id"] for c in cal_list.get("items", []) if c.get("summary", "").strip() == TARGET_CALENDAR),
        None,
    )
    if not cal_id:
        print(f"✗ Calendar '{TARGET_CALENDAR}' not found.", file=sys.stderr)
        sys.exit(1)

    events = service.events().list(
        calendarId=cal_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        maxResults=500,
    ).execute().get("items", [])

    by_source = {}
    for e in events:
        desc  = (e.get("description") or "").strip()
        start = e.get("start", {})
        date  = (start.get("dateTime") or start.get("date") or "")[:10]
        source = None
        for line in desc.splitlines():
            if line.startswith("[Synced from:") and line.endswith("]"):
                source = line[len("[Synced from:"):].rstrip("]").strip()
                break
        if source and date:
            by_source.setdefault(source, set()).add(date)

    return by_source

# ── Calendar snapshot (for diff) ──────────────────────────────────────────────

def load_snapshot():
    if SNAPSHOT_FILE.exists():
        return {k: set(v) for k, v in json.loads(SNAPSHOT_FILE.read_text()).items()}
    return {}

def save_snapshot(cal_by_src):
    SNAPSHOT_FILE.write_text(
        json.dumps({k: sorted(v) for k, v in cal_by_src.items()}, indent=2)
    )

def compute_cal_diff(prev, curr):
    """Return {cal_source: {'added': [dates], 'removed': [dates]}}.
    Past events dropping out of the query window are not reported as removed."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    diff = {}
    for src in set(prev) | set(curr):
        added   = sorted(curr.get(src, set()) - prev.get(src, set()))
        removed = sorted(
            d for d in prev.get(src, set()) - curr.get(src, set())
            if d >= today  # only report future events as removed
        )
        if added or removed:
            diff[src] = {"added": added, "removed": removed}
    return diff

# ── Season nudge ─────────────────────────────────────────────────────────────

# (month, start_day, end_day, season_label)
SEASON_NUDGE_WINDOWS = [
    (9,  1,  7,  "Fall Season",         "fall"),
    (10, 11, 17, "Late Fall / Winter",   "late-fall"),
    (3,  24, 31, "Spring Season",        "spring"),
]

def _nudge_key(today: datetime.date) -> str | None:
    """Return a unique key like '2025-fall' if today is in a nudge window, else None."""
    for month, start, end, _label, slug in SEASON_NUDGE_WINDOWS:
        if today.month == month and start <= today.day <= end:
            return f"{today.year}-{slug}"
    return None

def _nudge_already_sent(key: str) -> bool:
    if not SEASON_NUDGE_FILE.exists():
        return False
    sent = json.loads(SEASON_NUDGE_FILE.read_text())
    return key in sent

def _mark_nudge_sent(key: str):
    sent = json.loads(SEASON_NUDGE_FILE.read_text()) if SEASON_NUDGE_FILE.exists() else {}
    sent[key] = datetime.date.today().isoformat()
    SEASON_NUDGE_FILE.write_text(json.dumps(sent, indent=2))

def _season_label_for_key(key: str) -> str:
    slug = key.split("-", 1)[1]
    for _m, _s, _e, label, s in SEASON_NUDGE_WINDOWS:
        if s == slug:
            return label
    return "New Season"

def build_season_nudge_html(season_label: str) -> str:
    kids      = KIDS_FIRST_NAMES
    kid_list  = ", ".join(kids) if kids else "your kids"
    teams     = [t["calendar_name"] for t in _cfg["teams"]]
    team_list = "".join(f"<li>{t}</li>" for t in teams)
    today_str = datetime.date.today().strftime("%B %-d, %Y")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ margin:0; padding:20px; background:#f0f2f5;
         font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif }}
  .card {{ background:#fff; border-radius:12px; padding:28px 32px; max-width:600px;
           margin:0 auto; box-shadow:0 2px 8px rgba(0,0,0,.07) }}
  h2 {{ color:#16213e; font-size:20px; margin:0 0 6px }}
  .sub {{ color:#888; font-size:13px; margin-bottom:20px }}
  p {{ color:#333; font-size:14px; line-height:1.6; margin:0 0 14px }}
  ul {{ color:#333; font-size:14px; line-height:1.8; padding-left:20px; margin:0 0 14px }}
  .checklist {{ background:#f8f9fb; border-radius:8px; padding:16px 20px; margin:18px 0 }}
  .checklist li {{ margin-bottom:6px }}
  .footer {{ margin-top:24px; padding-top:14px; border-top:1px solid #eee;
             text-align:center; font-size:11px; color:#bbb }}
</style></head>
<body><div class="card">
  <h2>🏅 {season_label} is Starting — Time to Update!</h2>
  <div class="sub">{today_str}</div>

  <p>Hey! It looks like a new sports season is kicking off. Your kids ({kid_list}) may have
  new teams, new calendars, or new scheduling rules. The RSVP automations keep running as-is,
  but any teams that don't have a matching Google Calendar won't get auto-RSVPs.</p>

  <p><strong>Currently configured teams:</strong></p>
  <ul>{team_list}</ul>

  <p>If anything has changed for {season_label.lower()}, just reply to this email with the
  following info for each new or updated team:</p>

  <div class="checklist">
    <ul>
      <li>📅 <strong>Google Calendar name</strong> — must match exactly (e.g. "Falcons U12 2025")</li>
      <li>👦 <strong>Which kid(s)</strong> are on this team</li>
      <li>📱 <strong>Platform</strong> — TeamSnap or PlayMetrics/SportsEngine</li>
      <li>🔗 <strong>Team ID</strong> — from the URL at app.teamsnap.com/team/XXXXXXXX or PlayMetrics</li>
      <li>🚗 <strong>Driving?</strong> — yes/no (no = another parent always drives, e.g. head coach)</li>
      <li>⚠️ <strong>Any special rules</strong> — e.g. "always mark going regardless of calendar",
          "skip B-team games", custom game duration, etc.</li>
    </ul>
  </div>

  <p>I'll update <code>config.json</code> and make sure the new teams are wired up before the
  first game. No rush — just reply when you have the info!</p>

  <p style="color:#888;font-size:13px">Not the right time? If your seasons start on different
  weeks than when this email landed, just say so and I'll adjust the nudge windows to match
  your schedule.</p>

  <div class="footer">Sent by your kids-sports-sync automations &mdash; reply to this email to update your team config</div>
</div></body></html>"""

def maybe_send_season_nudge():
    """Send a season-onboarding nudge email if today is in a nudge window and it hasn't been sent yet."""
    today = datetime.date.today()
    key   = _nudge_key(today)
    if not key or _nudge_already_sent(key):
        return
    label   = _season_label_for_key(key)
    subject = f"🏅 {label} is Starting — Update Your Sports Calendars?"
    html    = build_season_nudge_html(label)
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path.home() / "context/admin/rosie"))
        import rosie_mailer
        to_addr = _cfg["family"].get("notify_email", rosie_mailer.ERIN_EMAIL)
        rosie_mailer.send(subject, html, to=to_addr)
        _mark_nudge_sent(key)
        print(f"[season-nudge] Sent '{label}' onboarding email to {to_addr}", file=sys.stderr)
    except Exception as exc:
        print(f"[season-nudge] Failed to send nudge: {exc}", file=sys.stderr)


# ── HTML email builder ────────────────────────────────────────────────────────

def fmt_date(d):
    try:
        return datetime.datetime.strptime(d, "%Y-%m-%d").strftime("%a %b %-d")
    except ValueError:
        return d

def build_html(changes, all_rsvps, cal_diff, skipped, applied, is_first_run=False, ts_dates=None):
    today       = datetime.date.today()
    today_str   = today.strftime("%A, %B %-d, %Y")
    today_short = today.strftime("%b %-d")
    dry         = ' <span style="color:#aaa;font-size:13px;font-weight:400">(dry run)</span>' if not applied else ""

    parts = []
    p = parts.append

    p(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:20px;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
<div style="max-width:580px;margin:0 auto;background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 2px 8px rgba(0,0,0,.07)">
""")

    # ── Header ──────────────────────────────────────────────────────────────────
    p(f'<div style="margin-bottom:24px">'
      f'<span style="color:#e67e22;font-size:22px;font-weight:800">Sports RSVP</span>'
      f'<span style="color:#16213e;font-size:22px;font-weight:400"> — {today_str}</span>{dry}'
      f'</div>')

    if is_first_run:
        p('<div style="background:#e8f4fd;border-left:4px solid #2196f3;border-radius:4px;padding:12px 16px;margin-bottom:24px;font-size:13px;color:#0d47a1">'
          '👋 <strong>First run!</strong> Future daily emails will only show what actually changed.'
          '</div>')

    # ── Section 1: RSVP Changes (grouped by date) ───────────────────────────────
    p('<div style="margin-bottom:28px">')
    p('<div style="font-size:15px;font-weight:700;color:#16213e;margin-bottom:2px">⚡ RSVP Changes</div>')
    p('<div style="font-size:12px;color:#aaa;margin-bottom:12px">since last digest</div>')

    # Flatten and sort by date
    changes_flat = []
    for cal_source, rows in changes.items():
        emoji = TEAM_EMOJI.get(cal_source, "🏅")
        for tup in rows:
            kid_name, date_label, ev_name, going = tup[0], tup[1], tup[2], tup[3]
            ev_date = tup[4] if len(tup) > 4 else "9999-99-99"
            changes_flat.append((ev_date, date_label, emoji, kid_name, ev_name, going))
    changes_flat.sort(key=lambda x: x[0])

    if not changes_flat:
        p('<div style="color:#888;font-size:14px">No RSVP changes this run.</div>')
    else:
        cur_date = None
        for ev_date, date_label, emoji, kid_name, ev_name, going in changes_flat:
            if date_label != cur_date:
                p(f'<div style="font-size:13px;font-weight:700;color:#333;margin:{"0" if cur_date is None else "10px"} 0 4px">{date_label}</div>')
                cur_date = date_label
            kid_color = KID_COLOR.get(kid_name, "#555")
            badge_bg  = "#e8f5e9" if going else "#f5f5f5"
            badge_fg  = "#2e7d32" if going else "#999"
            badge_txt = "✓ Going" if going else "– Not going"
            p(f'<div style="display:flex;align-items:center;padding:4px 0;font-size:13px">'
              f'<span style="width:22px">{emoji}</span>'
              f'<span style="display:inline-block;background:{kid_color};color:#fff;border-radius:10px;padding:1px 7px;font-size:11px;font-weight:600;margin-right:6px">{kid_name}</span>'
              f'<span style="flex:1;color:#333">{ev_name}</span>'
              f'<span style="background:{badge_bg};color:{badge_fg};border-radius:10px;padding:2px 9px;font-size:12px;font-weight:600;white-space:nowrap">{badge_txt}</span>'
              f'</div>')
    p('</div>')

    # ── Section 2: Week Ahead (grouped by date) ──────────────────────────────────
    p('<div style="margin-bottom:28px">')
    p('<div style="font-size:15px;font-weight:700;color:#16213e;margin-bottom:12px">📋 Week Ahead</div>')

    week_cutoff = (today + datetime.timedelta(days=7)).strftime("%Y-%m-%d")

    # Build changes lookup: (cal_source, kid_name, ev_name) → going
    changes_lookup = {
        (cal_source, tup[0], tup[2]): tup[3]
        for cal_source, rows in changes.items()
        for tup in rows
    }

    # Flatten all events within the week window
    events_flat = []
    for cal_source, rows in all_rsvps.items():
        emoji = TEAM_EMOJI.get(cal_source, "🏅")
        for tup in rows:
            kid_name, date_label, ev_name, going = tup[0], tup[1], tup[2], tup[3]
            ev_date = tup[4] if len(tup) > 4 else "9999-99-99"
            if ev_date > week_cutoff:
                continue
            key     = (cal_source, kid_name, ev_name)
            changed = key in changes_lookup
            if changed:
                going = changes_lookup[key]
            events_flat.append((ev_date, date_label, emoji, kid_name, ev_name, going, changed))

    events_flat.sort(key=lambda x: (x[0], x[3], x[4]))

    if not events_flat:
        p('<div style="color:#888;font-size:14px">No upcoming events in the next 7 days.</div>')
    else:
        cur_date = None
        for ev_date, date_label, emoji, kid_name, ev_name, going, changed in events_flat:
            if date_label != cur_date:
                p(f'<div style="font-size:13px;font-weight:700;color:#333;margin:{"0" if cur_date is None else "10px"} 0 4px">{date_label}</div>')
                cur_date = date_label
            kid_color   = KID_COLOR.get(kid_name, "#555")
            badge_bg    = "#e8f5e9" if going else "#f5f5f5"
            badge_fg    = "#2e7d32" if going else "#999"
            badge_txt   = "✓ Going" if going else "– Not going"
            updated_tag = (f' <span style="color:#e67e22;font-size:11px">updated {today_short}</span>'
                           if changed else "")
            p(f'<div style="display:flex;align-items:center;padding:4px 0;font-size:13px">'
              f'<span style="width:22px">{emoji}</span>'
              f'<span style="display:inline-block;background:{kid_color};color:#fff;border-radius:10px;padding:1px 7px;font-size:11px;font-weight:600;margin-right:6px">{kid_name}</span>'
              f'<span style="flex:1;color:#333">{ev_name}{updated_tag}</span>'
              f'<span style="background:{badge_bg};color:{badge_fg};border-radius:10px;padding:2px 9px;font-size:12px;font-weight:600;white-space:nowrap">{badge_txt}</span>'
              f'</div>')
    p('</div>')

    # ── Section 3: Skipped (no availability record) ──────────────────────────────
    if skipped:
        p('<div style="margin-bottom:28px">')
        p('<div style="font-size:15px;font-weight:700;color:#b45309;margin-bottom:8px">⚠️ Skipped — No Availability Record</div>')
        p('<div style="color:#888;font-size:13px;margin-bottom:8px">TeamSnap returned no availability record for these events.</div>')
        for cal_source, rows in sorted(skipped.items()):
            emoji = TEAM_EMOJI.get(cal_source, "🏅")
            for kid_name, date_label, ev_name in rows:
                kid_color = KID_COLOR.get(kid_name, "#555")
                p(f'<div style="display:flex;align-items:center;padding:4px 0;font-size:13px">'
                  f'<span style="width:22px">{emoji}</span>'
                  f'<span style="display:inline-block;background:{kid_color};color:#fff;border-radius:10px;padding:1px 7px;font-size:11px;font-weight:600;margin-right:6px">{kid_name}</span>'
                  f'<span style="color:#888">{date_label} — {ev_name}</span>'
                  f'</div>')
        p('</div>')

    # ── Footer ──────────────────────────────────────────────────────────────────
    p('<div style="margin-top:16px;padding-top:14px;border-top:1px solid #eee;text-align:center;font-size:11px;color:#bbb">'
      '⏳ <strong>Sports RSVP</strong> by Rosie &nbsp;·&nbsp; '
      'to stop these emails set <code style="font-size:11px">"email_enabled": false</code> in config.json'
      '</div>')

    p('\n</div>\n</body></html>')
    return "\n".join(parts)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Set RSVPs + save snapshot (default: dry run)")
    parser.add_argument("--days",  type=int, default=60)
    parser.add_argument("--debug", action="store_true", help="Print per-team diagnostic info to stderr")
    args = parser.parse_args()

    token = get_ts_token()

    now      = datetime.datetime.now(tz=datetime.timezone.utc)
    time_min = now.isoformat()
    time_max = (now + datetime.timedelta(days=args.days)).isoformat()

    # Load Google Calendar + compute diff vs last snapshot
    gcal          = get_gcal_service()
    cal_by_src    = load_cal_dates_by_source(gcal, time_min, time_max)
    is_first_run  = not SNAPSHOT_FILE.exists()
    prev_snapshot = load_snapshot()
    cal_diff      = compute_cal_diff(prev_snapshot, cal_by_src)

    if args.apply:
        save_snapshot(cal_by_src)

    # changes:   {cal_source: [(kid_name, date_label, ev_name, going)]}
    # all_rsvps: {cal_source: [(kid_name, date_label, ev_name, going)]}  — full picture
    # skipped:   {cal_source: [(kid_name, date_label, ev_name)]}  — no av record found
    changes          = {}
    all_rsvps        = {}
    skipped          = {}
    ts_dates_by_source = {}  # cal_source → set of YYYY-MM-DD dates still on TeamSnap

    for team_id, cal_source in TEAM_CALENDAR_MAP.items():
        members_data = ts_get(token, f"{API_BASE}/members/search?team_id={team_id}")
        members      = parse_items(members_data)
        kid_members  = [
            m for m in members
            if m.get("last_name") == KIDS_LAST_NAME
            and m.get("first_name") in KIDS_FIRST_NAMES
        ]

        if args.debug:
            all_names = [(m.get("first_name"), m.get("last_name")) for m in members]
            print(f"[debug] {cal_source} (team {team_id}): {len(members)} members total, "
                  f"{len(kid_members)} Lynch kids found", file=sys.stderr)
            if not kid_members:
                print(f"[debug]   → all members: {all_names}", file=sys.stderr)

        if not kid_members:
            continue

        kid_ids   = {m["id"] for m in kid_members}
        kid_names = {m["id"]: m.get("first_name") for m in kid_members}

        events_data   = ts_get(token, f"{API_BASE}/events/search?team_id={team_id}")
        events        = parse_items(events_data)
        future_events = [
            e for e in events
            if (e.get("start_date") or "")[:10] >= now.strftime("%Y-%m-%d")
        ]
        ts_dates_by_source[cal_source] = {(e.get("start_date") or "")[:10] for e in future_events}

        if args.debug:
            print(f"[debug]   → {len(future_events)} future events, "
                  f"cal_dates for '{cal_source}': {sorted(cal_by_src.get(cal_source, set()))}", file=sys.stderr)

        if not future_events:
            continue

        av_data  = ts_get(token, f"{API_BASE}/availabilities/search?team_id={team_id}")
        avails   = parse_items(av_data)
        av_index = {(str(a["event_id"]), str(a["member_id"])): a for a in avails}

        if args.debug:
            print(f"[debug]   → {len(avails)} availability records fetched", file=sys.stderr)

        cal_dates = cal_by_src.get(cal_source, set())

        for event in future_events:
            ev_id   = event["id"]
            ev_date = (event.get("start_date") or "")[:10]
            ev_name = event.get("name") or event.get("formatted_title") or "(no name)"
            going   = ev_date in cal_dates

            try:
                date_label = datetime.datetime.strptime(ev_date, "%Y-%m-%d").strftime("%a %b %-d")
            except ValueError:
                date_label = ev_date

            for kid_id in kid_ids:
                kid_name = kid_names[kid_id]
                av = av_index.get((str(ev_id), str(kid_id)))
                if not av:
                    skipped.setdefault(cal_source, []).append(
                        (kid_name, date_label, ev_name)
                    )
                    continue

                current_code = av.get("status_code")
                target_code  = 1 if going else 0
                if args.debug:
                    print(f"[debug]     {kid_name} / {ev_name} ({ev_date}): "
                          f"going={going}, current={current_code}, target={target_code}", file=sys.stderr)

                # Respect a manual "yes" set outside this script
                if RESPECT_MANUAL_YES and target_code == 0 and current_code == 1:
                    all_rsvps.setdefault(cal_source, []).append(
                        (kid_name, date_label, ev_name, True, ev_date)  # honour manual going
                    )
                    continue

                # Always record for the full status summary
                all_rsvps.setdefault(cal_source, []).append(
                    (kid_name, date_label, ev_name, going, ev_date)
                )

                if current_code == target_code:
                    continue

                if args.apply:
                    ts_put(token, av["_href"], {"status_code": target_code})
                    old_label = "going" if current_code == 1 else "not going"
                    new_label = "going" if target_code  == 1 else "not going"
                    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with RSVP_LOG_FILE.open("a") as f:
                        f.write(f"{ts} | {cal_source} | {kid_name} | {ev_name} | {ev_date} | {old_label} → {new_label}\n")

                changes.setdefault(cal_source, []).append(
                    (kid_name, date_label, ev_name, going, ev_date)
                )

    maybe_send_season_nudge()

    if _cfg.get("email_enabled", True):
        print(build_html(changes, all_rsvps, cal_diff, skipped, args.apply, is_first_run, ts_dates=ts_dates_by_source))

    if args.apply:
        telemetry.ping(CONFIG_DIR, _cfg, "teamsnap-rsvp", {
            "rsvps_set":    sum(len(v) for v in changes.values()),
            "cal_changes":  sum(len(v.get("added", [])) + len(v.get("removed", []))
                                for v in cal_diff.values()),
        })


if __name__ == "__main__":
    main()
