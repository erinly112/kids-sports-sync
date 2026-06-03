"""
teamsnap-rsvp Cloud Function — runs daily at 7am ET.

1. Downloads config/state from GCS (erin-lynch-scripts)
2. Runs teamsnap-rsvp.py --apply (outputs HTML)
3. Runs sportngin-rsvp.py --apply (outputs plain text)
4. Uploads changed state back to GCS
5. Emails combined summary to familynch@gmail.com
"""

import base64
import os
import subprocess
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import functions_framework
from google.auth.transport.requests import Request
from google.cloud import storage
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BUCKET       = "erin-lynch-scripts"
LOCAL_CONFIG = Path("/tmp/script-config")
TO_EMAIL     = "familynch@gmail.com"

DOWNLOAD_FILES = [
    "config.json",
    "credentials.json",
    "calendar-token.json",
    "gmail-token.json",
    "teamsnap-credentials.json",
    "teamsnap-token.json",
    "rsvp-cal-snapshot.json",
    "season-nudge-sent.json",
    "sportngin-credentials.json",
]
UPLOAD_FILES = [
    "calendar-token.json",
    "gmail-token.json",
    "teamsnap-token.json",
    "rsvp-cal-snapshot.json",
    "season-nudge-sent.json",
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


def send_email(subject, html_body):
    token_file = LOCAL_CONFIG / "gmail-token.json"
    creds = Credentials.from_authorized_user_file(str(token_file))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())

    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    msg = MIMEMultipart("alternative")
    msg["To"]      = TO_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"Email sent to {TO_EMAIL}")


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
    print("=== Downloading config from GCS ===")
    sync_from_gcs()

    print("\n=== Running teamsnap-rsvp ===")
    ts_output = run_script("teamsnap_rsvp.py", "--apply")

    print("\n=== Running sportngin-rsvp ===")
    sn_output = run_script("sportngin_rsvp.py", "--apply")

    print("\n=== Saving state to GCS ===")
    sync_to_gcs()

    # Combine outputs — TeamSnap is HTML, SportNgin is plain text
    has_ts = bool(ts_output.strip())
    has_sn = bool(sn_output.strip())

    if not has_ts and not has_sn:
        print("No RSVP changes — skipping email")
        return "OK", 200

    # Wrap SportNgin plain text in minimal HTML and append to TeamSnap HTML
    if has_ts and has_sn:
        combined = ts_output.rstrip() + f"\n<pre style='font-family:sans-serif;margin-top:16px'>{sn_output}</pre>"
    elif has_ts:
        combined = ts_output
    else:
        combined = f"<pre style='font-family:sans-serif'>{sn_output}</pre>"

    send_email("Team RSVPs updated", combined)
    return "OK", 200
