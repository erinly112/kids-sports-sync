"""
sports-sync Cloud Function — runs every Sunday at 5pm ET.

1. Downloads config/state from GCS (erin-lynch-scripts)
2. Runs driving-plan.py --apply
3. Runs sports-cal-sync.py
4. Uploads changed state back to GCS
5. Emails summary to notify_email from config.json
"""

import base64
import json
import os
import subprocess
import sys
import time
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

DOWNLOAD_FILES = [
    "config.json",
    "credentials.json",
    "calendar-token.json",
    "gmail-token.json",
    "sports-sync-ids.json",
    "driving-plan-synced.json",
    "geocode-cache.json",
    "drive-config.json",
    "bays-fields.json",
]
UPLOAD_FILES = [
    "calendar-token.json",
    "gmail-token.json",
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


def run_script(script_name, *args):
    """Run a bundled script, return its stdout."""
    env = {**os.environ, "SCRIPT_CONFIG_DIR": str(LOCAL_CONFIG)}
    result = subprocess.run(
        [sys.executable, script_name, *args],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent),
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.stdout


def send_email(subject, body, to):
    token_file = LOCAL_CONFIG / "gmail-token.json"
    creds = Credentials.from_authorized_user_file(str(token_file))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())

    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    msg = MIMEMultipart("alternative")
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"Email sent to {to}")


@functions_framework.http
def sports_sync(request):
    print("=== Downloading config from GCS ===")
    sync_from_gcs()
    to_email = json.loads((LOCAL_CONFIG / "config.json").read_text())["family"]["notify_email"]

    print("\n=== Running driving-plan ===")
    driving_output = run_script("driving_plan.py", "--apply")

    print("\n=== Running sports-cal-sync ===")
    sports_output = run_script("sports_cal_sync.py")

    print("\n=== Saving state to GCS ===")
    sync_to_gcs()

    # Build email: extract "Texts to send" section from driving output
    texts, in_texts = [], False
    for line in driving_output.splitlines():
        if "Texts to send this week:" in line:
            in_texts = True
        elif in_texts and line.strip():
            texts.append(line)

    sync_lines = [
        l for l in sports_output.splitlines()
        if any(x in l for x in ["✓ Copied", "↻ Updated", "[dry run]", "Done:", "✗"])
    ]

    body  = "=== Texts to send this week ===\n"
    body += "\n".join(texts) if texts else "None this week"
    body += "\n\n=== Schedule changes ===\n"
    body += "\n".join(sync_lines) if sync_lines else "No schedule changes"

    send_email("Kids calendar synced — driving plan for the week", body, to_email)
    return "OK", 200
