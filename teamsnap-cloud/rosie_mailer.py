"""
rosie_mailer shim for cloud deployment.
Uses gmail-app-password.txt from SCRIPT_CONFIG_DIR (downloaded from GCS by main.py).
Mirrors the interface of the local ~/context/admin/rosie/rosie_mailer.py.
"""
import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

_CONFIG_DIR = Path(os.environ.get("SCRIPT_CONFIG_DIR", "/tmp/script-config"))

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _notify_email():
    cfg = _CONFIG_DIR / "config.json"
    return json.loads(cfg.read_text())["family"]["notify_email"]


def _app_password():
    return (_CONFIG_DIR / "gmail-app-password.txt").read_text().strip()


def _send(msg, to):
    sender = _notify_email()
    msg["From"] = sender
    msg["To"] = to
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(sender, _app_password())
        smtp.sendmail(sender, to, msg.as_string())
    print(f"  ✉ sent: {msg['Subject']}")


def send(subject: str, html_body: str, to: str = None, text_body: str = ""):
    if to is None:
        to = _notify_email()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    if text_body:
        msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    _send(msg, to)


def send_plain(subject: str, body: str, to: str = None):
    if to is None:
        to = _notify_email()
    msg = MIMEText(body)
    msg["Subject"] = subject
    _send(msg, to)
