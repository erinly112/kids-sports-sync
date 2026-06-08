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


@functions_framework.http
def teamsnap_rsvp(request):
    try:
        print("=== Downloading config from GCS ===")
        sync_from_gcs()

        config_path = LOCAL_CONFIG / "config.json"
        if not config_path.exists():
            raise RuntimeError("config.json not found after GCS download — check bucket permissions")
        to_email = json.loads(config_path.read_text())["family"]["notify_email"]

        print("\n=== Running sports-cal-sync ===")
        cal_output = run_script("sports_cal_sync.py")

        print("\n=== Running driving-plan ===")
        run_script("driving_plan.py", "--apply")

        print("\n=== Running teamsnap-rsvp ===")
        ts_output = run_script("teamsnap_rsvp.py", "--apply")

        print("\n=== Running sportngin-rsvp ===")
        sn_output = run_script("sportngin_rsvp.py", "--apply")

        print("\n=== Saving state to GCS ===")
        sync_to_gcs()

        # Build RSVP email body
        has_ts = bool(ts_output.strip())
        has_sn = bool(sn_output.strip())

        if not has_ts and not has_sn:
            print("No RSVP changes — skipping email")
            return "OK", 200

        if has_ts and has_sn:
            combined = ts_output.rstrip() + f"\n<pre style='font-family:sans-serif;margin-top:16px'>{sn_output}</pre>"
        elif has_ts:
            combined = ts_output
        else:
            combined = f"<pre style='font-family:sans-serif'>{sn_output}</pre>"

        # Append calendar changes (new/updated events and deletions) if any
        cal_lines = [
            l for l in cal_output.splitlines()
            if any(x in l for x in ["✓ Copied", "↻ Updated", "🗑", "✗"])
        ]
        if cal_lines:
            changes_html = "<br>".join(cal_lines)
            combined += f"\n<hr><p><strong>📅 Calendar changes:</strong><br><pre style='font-family:sans-serif'>{changes_html}</pre></p>"

        send_email("Team RSVPs updated", combined, to_email)
        return "OK", 200

    except Exception as e:
        import traceback
        msg = traceback.format_exc()
        print(f"ERROR: {msg}", file=sys.stderr)
        return f"Error: {e}\n\n{msg}", 500
