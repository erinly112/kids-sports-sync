"""
rosie_mailer shim for cloud deployment.
Uses gmail-token.json from SCRIPT_CONFIG_DIR (downloaded from GCS by main.py).
Mirrors the interface of the local ~/context/admin/rosie/rosie_mailer.py.
"""
import base64
import json
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

_CONFIG_DIR = Path(os.environ.get("SCRIPT_CONFIG_DIR", "/tmp/script-config"))


def _notify_email():
    cfg = _CONFIG_DIR / "config.json"
    return json.loads(cfg.read_text())["family"]["notify_email"]


def _get_service():
    token_file = _CONFIG_DIR / "gmail-token.json"
    creds = Credentials.from_authorized_user_file(str(token_file))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send(subject: str, html_body: str, to: str = None, text_body: str = ""):
    if to is None:
        to = _notify_email()
    svc = _get_service()
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    if text_body:
        msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"  ✉ sent: {subject}")


def send_plain(subject: str, body: str, to: str = None):
    if to is None:
        to = _notify_email()
    svc = _get_service()
    msg = MIMEText(body)
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"  ✉ sent: {subject}")
