"""
teamsnap-rsvp Cloud Function — runs daily at 7am ET.

1. Downloads config/state from GCS (erin-lynch-scripts)
2. Runs sports_cal_sync.py  — syncs team calendars → Kid Activities
3. Runs driving_plan.py --apply  — creates/updates 🚗 driving reminders
4. Runs teamsnap_rsvp.py --apply  — sets TeamSnap RSVPs
5. Runs sportngin_rsvp.py --apply  — sets SportsEngine RSVPs
6. Uploads changed state back to GCS
7. Emails RSVP summary (+ any schedule changes) to notify_email
"""

import json
import os
import smtplib
import subprocess
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import functions_framework
from google.cloud import storage

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

BUCKET       = "erin-lynch-scripts"
LOCAL_CONFIG = Path("/tmp/script-config")

DOWNLOAD_FILES = [
    "config.json",
    "credentials.json",
    "calendar-token.json",
    "gmail-app-password.txt",
    "teamsnap-credentials.json",
    "teamsnap-token.json",
    "rsvp-cal-snapshot.json",
    "season-nudge-sent.json",
    "sportngin-credentials.json",
    "sports-sync-ids.json",
    "drive-config.json",
    "driving-plan-synced.json",
    "geocode-cache.json",
    "bays-fields.json",
]
UPLOAD_FILES = [
    "calendar-token.json",
    "teamsnap-token.json",
    "rsvp-cal-snapshot.json",
    "season-nudge-sent.json",
    "sports-sync-ids.json",
    "driving-plan-synced.json",
    "geocode-cache.json",
]


def sync_from_gcs():
    LOCAL_CONFIG.mkdir(parents=True, exist_ok=True)
    client = storage.Client()
    bkt    = client.bucket(BUCKET)
    for f in DOWNLOAD_FILES:
        try:
            bkt.blob(f"config/{f}").download_to_filename(str(LOCAL_CONFIG / f))
            print(f"  ↓ {f}")
        except Exception as e:
            print(f"  — {f} not in GCS ({e})")


def sync_to_gcs():
    client = storage.Client()
    bkt    = client.bucket(BUCKET)
    for f in UPLOAD_FILES:
        p = LOCAL_CONFIG / f
        if p.exists():
            bkt.blob(f"config/{f}").upload_from_filename(str(p))
            print(f"  ↑ {f}")


def send_email(subject, html_body, to):
    sender = json.loads((LOCAL_CONFIG / "config.json").read_text())["family"]["notify_email"]
    app_password = (LOCAL_CONFIG / "gmail-app-password.txt").read_text().strip()
    msg = MIMEMultipart("alternative")
    msg["From"]    = sender
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(sender, app_password)
        smtp.sendmail(sender, to, msg.as_string())
    print(f"Email sent to {to}")


def run_script(script, *args):
    env = {**os.environ, "SCRIPT_CONFIG_DIR": str(LOCAL_CONFIG)}
    r = subprocess.run(
        [sys.executable, script, *args],
        env=env, capture_output=True, text=True,
        cwd=str(Path(__file__).parent),
    )
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    return r.stdout


def build_email_html(changes, all_rsvps, cal_diff, skipped, cal_lines, kid_colors, is_first_run):
    import datetime as _dt
    today     = _dt.date.today()
    today_str = today.strftime("%A, %B %-d, %Y")

    parts = []
    p = parts.append

    p(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:20px;background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
<div style="max-width:580px;margin:0 auto">
<div style="background:#16213e;border-radius:12px 12px 0 0;padding:20px 24px">
  <div style="color:#fff;font-size:19px;font-weight:700">Team RSVPs</div>
  <div style="color:#8899bb;font-size:13px;margin-top:3px">{today_str}</div>
</div>
<div style="background:#fff;border-radius:0 0 12px 12px;padding:24px;box-shadow:0 2px 12px rgba(0,0,0,.08)">
""")

    def kid_badge(name):
        color = kid_colors.get(name, "#555")
        return f'<span style="display:inline-block;background:{color};color:#fff;border-radius:10px;padding:1px 7px;font-size:11px;font-weight:600;margin-right:5px">{name}</span>'

    def going_badge(going):
        if going:
            return '<span style="background:#e8f5e9;color:#2e7d32;border-radius:10px;padding:2px 9px;font-size:12px;font-weight:600">✓ Going</span>'
        return '<span style="background:#f5f5f5;color:#999;border-radius:10px;padding:2px 9px;font-size:12px;font-weight:600">– Not going</span>'

    def section_header(title):
        return f'<div style="font-size:15px;font-weight:700;color:#16213e;margin-bottom:12px">{title}</div>'

    # ── First-run banner ────────────────────────────────────────────────────
    if is_first_run:
        p('<div style="background:#e8f4fd;border-left:4px solid #2196f3;border-radius:4px;padding:12px 16px;margin-bottom:24px;font-size:13px;color:#0d47a1">'
          '👋 <strong>First run!</strong> This email looks busier than usual — future emails will only show what actually changed.'
          '</div>')

    # ── Section 1: RSVP Changes ──────────────────────────────────────────────
    p('<div style="margin-bottom:28px">')
    p(section_header("⚡ RSVP Changes"))
    if not changes:
        p('<div style="color:#888;font-size:14px">No RSVP changes this run.</div>')
    else:
        # Group by date
        by_date = {}
        for r in sorted(changes, key=lambda x: x["date"]):
            by_date.setdefault(r["date"], []).append(r)
        p('<table style="width:100%;border-collapse:collapse">')
        for date, rows in by_date.items():
            date_label = rows[0]["date_label"]
            p(f'<tr><td colspan="3" style="padding:10px 0 4px;font-size:13px;font-weight:600;color:#555;border-top:1px solid #eee">{date_label}</td></tr>')
            for r in rows:
                p(f"""<tr>
  <td style="padding:4px 8px;font-size:13px;color:#333;width:100%">
    {kid_badge(r['kid'])}{r['event']}
    <span style="color:#aaa;font-size:11px;margin-left:5px">{r['team']}</span>
  </td>
  <td style="padding:4px 0;text-align:right;white-space:nowrap">{going_badge(r['going'])}</td>
</tr>""")
        p('</table>')
    p('</div>')

    # ── Section 2: Week Ahead (grouped by day) ───────────────────────────────
    p('<div style="margin-bottom:28px">')
    p(section_header("📋 Week Ahead"))

    week_end = (today + _dt.timedelta(days=7)).isoformat()
    today_iso = today.isoformat()
    week_rsvps = [r for r in all_rsvps if today_iso <= r["date"] <= week_end]

    if not week_rsvps:
        p('<div style="color:#888;font-size:14px">No events in the next 7 days.</div>')
    else:
        by_date = {}
        for r in sorted(week_rsvps, key=lambda x: (x["date"], x["kid"], x["team"])):
            by_date.setdefault(r["date"], []).append(r)
        p('<table style="width:100%;border-collapse:collapse">')
        changed_keys = {(r["kid"], r["event"]) for r in changes}
        for date, rows in by_date.items():
            date_label = rows[0]["date_label"]
            p(f'<tr><td colspan="3" style="padding:10px 0 4px;font-size:13px;font-weight:600;color:#555;border-top:1px solid #eee">{date_label}</td></tr>')
            for r in rows:
                dot = ' <span style="font-size:9px;color:#e67e22;vertical-align:middle">●</span>' if (r["kid"], r["event"]) in changed_keys else ""
                p(f"""<tr>
  <td style="padding:4px 8px;font-size:13px;color:#333;width:100%">
    {kid_badge(r['kid'])}{r['event']}{dot}
    <span style="color:#aaa;font-size:11px;margin-left:5px">{r['team']}</span>
  </td>
  <td style="padding:4px 0;text-align:right;white-space:nowrap">{going_badge(r['going'])}</td>
</tr>""")
        p('</table>')
        p('<div style="color:#aaa;font-size:11px;margin-top:8px">● = changed this run</div>')
    p('</div>')

    # ── Section 3: Calendar Changes ──────────────────────────────────────────
    if cal_diff or cal_lines:
        p('<div style="margin-bottom:28px">')
        p(section_header("🗓 Calendar Changes"))
        if cal_diff:
            p('<table style="width:100%;border-collapse:collapse">')
            for cal_source, diff in sorted(cal_diff.items()):
                p(f'<tr><td colspan="2" style="padding:10px 0 5px;font-size:13px;font-weight:600;color:#555;border-top:1px solid #eee">{cal_source}</td></tr>')
                for d in diff.get("added", []):
                    try:
                        dl = _dt.datetime.strptime(d, "%Y-%m-%d").strftime("%a %b %-d")
                    except Exception:
                        dl = d
                    p(f'<tr><td style="padding:4px 0;width:24px;font-size:14px">➕</td><td style="padding:4px 8px;font-size:13px;color:#2e7d32">{dl} added to calendar</td></tr>')
                for d in diff.get("removed", []):
                    try:
                        dl = _dt.datetime.strptime(d, "%Y-%m-%d").strftime("%a %b %-d")
                    except Exception:
                        dl = d
                    p(f'<tr><td style="padding:4px 0;width:24px;font-size:14px">➖</td><td style="padding:4px 8px;font-size:13px;color:#c62828">{dl} removed from calendar</td></tr>')
            p('</table>')
        if cal_lines:
            joined = "\n".join(cal_lines)
            p(f'<pre style="font-family:sans-serif;font-size:12px;color:#555;margin-top:8px">{joined}</pre>')
        p('</div>')

    # ── Section 4: Skipped ───────────────────────────────────────────────────
    if skipped:
        p('<div style="margin-bottom:28px">')
        p(section_header("⚠️ Skipped — No Availability Record"))
        p('<table style="width:100%;border-collapse:collapse">')
        for r in skipped:
            p(f"""<tr>
  <td style="padding:4px 8px;font-size:13px;color:#333">
    {kid_badge(r['kid'])}{r['event']}
    <span style="color:#aaa;font-size:11px;margin-left:5px">{r['team']}</span>
  </td>
  <td style="padding:4px 0;font-size:13px;color:#888;white-space:nowrap">{r['date_label']}</td>
</tr>""")
        p('</table>')
        p('</div>')

    # ── Footer ───────────────────────────────────────────────────────────────
    p('<div style="margin-top:28px;padding-top:14px;border-top:1px solid #eee;text-align:center;font-size:11px;color:#bbb">'
      'To stop these emails, set <code style="font-size:11px">"email_enabled": false</code> in config.json'
      '</div>')

    p('\n</div>\n</div>\n</body></html>')
    return "\n".join(parts)


@functions_framework.http
def teamsnap_rsvp(request):
    try:
        print("=== Downloading config from GCS ===")
        sync_from_gcs()

        config_path = LOCAL_CONFIG / "config.json"
        if not config_path.exists():
            raise RuntimeError("config.json not found after GCS download — check bucket permissions")
        cfg      = json.loads(config_path.read_text())
        to_email = cfg["family"]["notify_email"]

        print("\n=== Running sports-cal-sync ===")
        cal_output = run_script("sports_cal_sync.py")

        print("\n=== Running driving-plan ===")
        run_script("driving_plan.py", "--apply")

        print("\n=== Running teamsnap-rsvp ===")
        ts_raw = run_script("teamsnap_rsvp.py", "--apply")

        print("\n=== Running sportngin-rsvp ===")
        sn_raw = run_script("sportngin_rsvp.py", "--apply")

        print("\n=== Saving state to GCS ===")
        sync_to_gcs()

        # Parse JSON output from both scripts
        try:
            ts_data = json.loads(ts_raw) if ts_raw.strip() else {}
        except Exception:
            ts_data = {}
        try:
            sn_data = json.loads(sn_raw) if sn_raw.strip() else {}
        except Exception:
            sn_data = {}

        # Merge data from both sources
        all_changes  = ts_data.get("changes",  []) + sn_data.get("changes",  [])
        all_rsvps    = ts_data.get("all_rsvps", []) + sn_data.get("all_rsvps", [])
        cal_diff     = ts_data.get("cal_diff",  {})
        skipped      = ts_data.get("skipped",   [])
        is_first_run = ts_data.get("is_first_run", False)

        if not all_rsvps and not all_changes:
            print("No RSVP data — skipping email")
            return "OK", 200

        # Calendar change lines from sports_cal_sync stdout
        cal_lines = [
            l for l in cal_output.splitlines()
            if any(x in l for x in ["✓ Copied", "↻ Updated", "🗑", "✗"])
        ]

        kid_colors = {k["name"]: k["color"] for k in cfg.get("kids", [])}
        html = build_email_html(all_changes, all_rsvps, cal_diff, skipped,
                                cal_lines, kid_colors, is_first_run)
        send_email("Team RSVPs updated", html, to_email)
        return "OK", 200

    except Exception as e:
        import traceback
        msg = traceback.format_exc()
        print(f"ERROR: {msg}", file=sys.stderr)
        return f"Error: {e}\n\n{msg}", 500
