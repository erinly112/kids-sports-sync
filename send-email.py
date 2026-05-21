#!/usr/bin/env python3
"""
Send a plain-text email via Gmail API using OAuth credentials.

Usage:
  python3 send-email.py --to recipient@example.com --subject "Subject" --body "Message body"
"""

import argparse
import base64
import email.mime.multipart
import email.mime.text
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES           = ["https://www.googleapis.com/auth/gmail.send"]
CONFIG_DIR       = Path.home() / ".config" / "google-tasks-sync"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE       = CONFIG_DIR / "gmail-token.json"


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


def send_email(to, subject, body, html=False):
    creds   = get_credentials()
    service = build("gmail", "v1", credentials=creds)

    if html:
        msg = email.mime.multipart.MIMEMultipart("alternative")
        msg["to"]      = to
        msg["subject"] = subject
        msg.attach(email.mime.text.MIMEText(body, "html"))
    else:
        msg = email.mime.text.MIMEText(body)
        msg["to"]      = to
        msg["subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"✓ Email sent to {to}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--to",      required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body",    required=True)
    parser.add_argument("--html",    action="store_true", help="Send as HTML email")
    args = parser.parse_args()
    send_email(args.to, args.subject, args.body, html=args.html)


if __name__ == "__main__":
    main()
