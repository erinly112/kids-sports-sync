"""
teamsnap-rsvp Cloud Function — runs daily at 7am ET.

1. Downloads config/state from GCS (erin-lynch-scripts)
2. Runs sports_cal_sync.py  — syncs team calendars → Kid Activities
3. Runs driving_plan.py --apply  — creates/updates 🚗 driving reminders
4. Runs teamsnap_rsvp.py --apply  — sets TeamSnap RSVPs
5. Runs sportngin_rsvp.py --apply  — sets SportsEngine RSVPs
6. Uploads changed state back to GCS
7. Emails daily Sports RSVP digest from Rosie
"""

import datetime
import json
import os
import re
import smtplib
import subprocess
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import functions_framework
from google.cloud import storage

SMTP_HOST    = "smtp.gmail.com"
SMTP_PORT    = 587
ROSIE_FROM   = "Rosie <rosie@erinslynch.com>"
ACCENT_COLOR = "#d4860a"   # amber — matches Sports RSVP by Rosie brand

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
    "last-digest-sent.json",
]
UPLOAD_FILES = [
    "calendar-token.json",
    "teamsnap-token.json",
    "rsvp-cal-snapshot.json",
    "season-nudge-sent.json",
    "sports-sync-ids.json",
    "driving-plan-synced.json",
    "geocode-cache.json",
    "last-digest-sent.json",
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
    app_password = (LOCAL_CONFIG / "gmail-app-password.txt").read_text().strip()
    sender_addr  = json.loads((LOCAL_CONFIG / "config.json").read_text())["family"]["notify_email"]
    msg = MIMEMultipart("alternative")
    msg["From"]    = ROSIE_FROM
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(sender_addr, app_password)
        smtp.sendmail(sender_addr, to, msg.as_string())
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


def build_email_html(changes, all_rsvps, discrepancies, cal_diff, skipped, deleted_lines,
                     kid_colors, is_first_run, last_digest_time=None):
    today     = datetime.date.today()
    today_str = today.strftime("%A, %B %-d, %Y")

    parts = []
    p = parts.append

    # ── Helpers ────────────────────────────────────────────────────────────────
    def kid_badge(name):
        color = kid_colors.get(name, "#555")
        return (f'<span style="display:inline-block;background:{color};color:#fff;'
                f'border-radius:10px;padding:1px 7px;font-size:11px;font-weight:600;'
                f'margin-right:4px">{name}</span>')

    def going_badge(going):
        if going:
            return ('<span style="background:#e8f5e9;color:#2e7d32;border-radius:10px;'
                    'padding:2px 9px;font-size:12px;font-weight:600">✓ Going</span>')
        return ('<span style="background:#f5f5f5;color:#999;border-radius:10px;'
                'padding:2px 9px;font-size:12px;font-weight:600">– Not going</span>')

    def section_hdr(title, subtitle=None):
        sub = f'<div style="font-size:12px;font-weight:400;color:#aaa;margin-top:2px">{subtitle}</div>' if subtitle else ""
        return (f'<div style="font-size:15px;font-weight:700;color:#1a1a2e;margin-bottom:12px">'
                f'{title}{sub}</div>')

    def date_row(label):
        return (f'<tr><td colspan="2" style="padding:10px 0 4px;font-size:13px;font-weight:700;'
                f'color:#333;border-top:1px solid #eee">{label}</td></tr>')

    def event_row(r, show_dot=False, override_note=None):
        dot  = (' <span style="font-size:9px;color:#e67e22;vertical-align:middle">●</span>'
                if show_dot else "")
        note = (f' <span style="font-size:11px;color:#e67e22;margin-left:4px">{override_note}</span>'
                if override_note else "")
        emoji = r.get("team_emoji", "🏅")
        return f"""<tr>
  <td style="padding:3px 8px;font-size:13px;color:#333;width:100%">
    {emoji} {kid_badge(r['kid'])}{r['event']}{dot}{note}
  </td>
  <td style="padding:3px 0;text-align:right;white-space:nowrap">{going_badge(r['going'])}</td>
</tr>"""

    # ── Header ─────────────────────────────────────────────────────────────────
    p(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:20px;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);overflow:hidden">
<div style="padding:22px 28px 14px">
  <div style="font-size:22px;font-weight:800;color:#1a1a2e">
    <span style="color:{ACCENT_COLOR}">Sports RSVP</span> — {today_str}
  </div>
</div>
<div style="padding:4px 28px 28px">
""")

    # ── First-run banner ────────────────────────────────────────────────────────
    if is_first_run:
        p('<div style="background:#fff8e1;border-left:3px solid #f9a825;border-radius:4px;'
          'padding:10px 14px;margin-bottom:20px;font-size:13px;color:#6d4c00">'
          '👋 <strong>First run!</strong> Future digests will highlight only what changed.'
          '</div>')

    # ── Section 1: RSVP Changes ─────────────────────────────────────────────────
    since_label = ""
    if last_digest_time:
        since_label = f"since last digest ({last_digest_time})"
    elif not is_first_run:
        since_label = "since last digest"

    p('<div style="margin-bottom:24px">')
    p(section_hdr("⚡ RSVP Changes", since_label))

    if not changes:
        p('<div style="color:#888;font-size:14px">No changes this run.</div>')
    else:
        sorted_changes = sorted(changes, key=lambda x: (x["date"], x["kid"], 0 if x["going"] else 1))
        by_date = {}
        for r in sorted_changes:
            by_date.setdefault(r["date"], []).append(r)
        p('<table style="width:100%;border-collapse:collapse">')
        for date, rows in by_date.items():
            p(date_row(rows[0]["date_label"]))
            for r in rows:
                p(event_row(r))
        p('</table>')
    p('</div>')

    # ── Section 2: Discrepancies ────────────────────────────────────────────────
    if discrepancies:
        sorted_disc = sorted(discrepancies, key=lambda x: (x["date"], x["kid"]))
        by_date = {}
        for r in sorted_disc:
            by_date.setdefault(r["date"], []).append(r)
        p('<div style="margin-bottom:24px;background:#fff8e1;border-radius:8px;padding:14px 16px">')
        p(section_hdr("⚠️ Check with Tom",
                      "App RSVP differs from calendar — not auto-corrected"))
        p('<table style="width:100%;border-collapse:collapse">')
        for date, rows in by_date.items():
            p(date_row(rows[0]["date_label"]))
            for r in rows:
                app_val = "Going" if r["app_going"] else "Not going"
                cal_val = "Going" if r["cal_going"] else "Not going"
                emoji   = r.get("team_emoji", "🏅")
                p(f'<tr><td style="padding:3px 8px;font-size:13px;color:#333">'
                  f'{emoji} {kid_badge(r["kid"])}{r["event"]}'
                  f'<span style="font-size:12px;color:#b45309;margin-left:8px">'
                  f'app: <strong>{app_val}</strong> · calendar says: <strong>{cal_val}</strong>'
                  f'</span></td></tr>')
        p('</table>')
        p('</div>')

    # ── Section 3: Week Ahead ───────────────────────────────────────────────────
    week_end  = (today + datetime.timedelta(days=7)).isoformat()
    today_iso = today.isoformat()
    week_rsvps = [r for r in all_rsvps if today_iso <= r["date"] <= week_end]

    p('<div style="margin-bottom:24px">')
    p(section_hdr("📋 Week Ahead"))

    if not week_rsvps:
        p('<div style="color:#888;font-size:14px">No events in the next 7 days.</div>')
    else:
        # Sort: date → kid → Going first → event name
        sorted_rsvps = sorted(week_rsvps,
                              key=lambda x: (x["date"], x["kid"], 0 if x["going"] else 1, x["event"]))
        changed_keys = {(r["kid"], r["event"]) for r in changes}
        by_date = {}
        for r in sorted_rsvps:
            by_date.setdefault(r["date"], []).append(r)
        p('<table style="width:100%;border-collapse:collapse">')
        for date, rows in by_date.items():
            p(date_row(rows[0]["date_label"]))
            for r in rows:
                dot = (r["kid"], r["event"]) in changed_keys
                p(event_row(r, show_dot=dot))
        p('</table>')
        p('<div style="color:#aaa;font-size:11px;margin-top:6px">● = changed this run</div>')
    p('</div>')

    # ── Section 3: Auto-removed events ─────────────────────────────────────────
    if deleted_lines:
        # Parse date from lines like "🗑 Deleted  EventName (2026-06-11T18:15:00) ← Source"
        def parse_deleted(line):
            m = re.search(r'\((\d{4}-\d{2}-\d{2})', line)
            date = m.group(1) if m else "9999-99-99"
            try:
                date_label = datetime.datetime.strptime(date, "%Y-%m-%d").strftime("%a %b %-d")
            except Exception:
                date_label = date
            # Extract event name — between first space after 🗑/label and the opening paren
            name_m = re.search(r'(?:Deleted|cancelled[^:]*:)\s+(.+?)\s+\(', line)
            name = name_m.group(1).strip() if name_m else line.strip()
            return date, date_label, name

        deleted_parsed = sorted([parse_deleted(l) for l in deleted_lines], key=lambda x: x[0])
        by_date = {}
        for date, date_label, name in deleted_parsed:
            by_date.setdefault((date, date_label), []).append(name)

        p('<div style="margin-bottom:24px">')
        p(section_hdr("🗑 Auto-removed from Calendar"))
        p('<table style="width:100%;border-collapse:collapse">')
        for (date, date_label), names in by_date.items():
            p(date_row(date_label))
            for name in names:
                p(f'<tr><td style="padding:3px 8px;font-size:13px;color:#c62828">'
                  f'{name} <span style="color:#aaa;font-size:11px">(no longer in source calendar)</span>'
                  f'</td></tr>')
        p('</table>')
        p('</div>')

    # ── Section 4: Calendar Updates ─────────────────────────────────────────────
    if cal_diff:
        p('<div style="margin-bottom:24px">')
        p(section_hdr("🗓 Calendar Updates"))
        p('<table style="width:100%;border-collapse:collapse">')
        for cal_source, diff in sorted(cal_diff.items()):
            for d in diff.get("added", []):
                try:
                    dl = datetime.datetime.strptime(d, "%Y-%m-%d").strftime("%a %b %-d")
                except Exception:
                    dl = d
                p(f'<tr><td style="padding:4px 8px;font-size:13px;color:#2e7d32">'
                  f'➕ {dl} — {cal_source}</td></tr>')
            for d in diff.get("removed", []):
                try:
                    dl = datetime.datetime.strptime(d, "%Y-%m-%d").strftime("%a %b %-d")
                except Exception:
                    dl = d
                p(f'<tr><td style="padding:4px 8px;font-size:13px;color:#c62828">'
                  f'➖ {dl} — {cal_source}</td></tr>')
        p('</table>')
        p('</div>')

    # ── Section 5: Skipped ──────────────────────────────────────────────────────
    if skipped:
        p('<div style="margin-bottom:24px">')
        p(section_hdr("⚠️ Skipped — No Availability Record"))
        p('<table style="width:100%;border-collapse:collapse">')
        for r in sorted(skipped, key=lambda x: (x.get("date_label",""), x["kid"])):
            emoji = r.get("team_emoji", "🏅")
            p(f'<tr><td style="padding:3px 8px;font-size:13px;color:#555">'
              f'{emoji} {kid_badge(r["kid"])}{r["event"]}'
              f'<span style="color:#aaa;font-size:11px;margin-left:6px">{r["date_label"]}</span>'
              f'</td></tr>')
        p('</table>')
        p('</div>')

    # ── Footer ──────────────────────────────────────────────────────────────────
    p(f'<div style="padding:16px 28px;border-top:1px solid #eee;background:#fafafa">'
      f'<span style="font-size:13px;font-weight:700;color:{ACCENT_COLOR}">🏅 Sports RSVP</span>'
      f'<span style="font-size:13px;color:#aaa"> by </span>'
      f'<span style="font-size:13px;font-weight:700;color:{ACCENT_COLOR}">Rosie</span>'
      f'</div>')

    p('</div></body></html>')
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

        # Load last digest timestamp for "since last digest" label
        last_digest_time = None
        digest_path = LOCAL_CONFIG / "last-digest-sent.json"
        if digest_path.exists():
            try:
                last_digest_time = json.loads(digest_path.read_text()).get("sent_at")
            except Exception:
                pass

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

        all_changes      = ts_data.get("changes",       []) + sn_data.get("changes",       [])
        all_rsvps        = ts_data.get("all_rsvps",     []) + sn_data.get("all_rsvps",     [])
        all_discrepancies= ts_data.get("discrepancies", []) + sn_data.get("discrepancies", [])
        cal_diff         = ts_data.get("cal_diff",      {})
        skipped          = ts_data.get("skipped",       [])
        is_first_run     = ts_data.get("is_first_run",  False)

        # Parse auto-deleted lines from cal sync output
        deleted_lines = [l for l in cal_output.splitlines() if "🗑" in l]

        kid_colors = {k["name"]: k["color"] for k in cfg.get("kids", [])}
        html = build_email_html(all_changes, all_rsvps, all_discrepancies, cal_diff, skipped,
                                deleted_lines, kid_colors, is_first_run, last_digest_time)

        send_email("Team RSVPs updated", html, to_email)

        # Save digest timestamp
        now_str = datetime.datetime.now().strftime("%a %b %-d at %-I:%M %p")
        digest_path.write_text(json.dumps({"sent_at": now_str}))

        return "OK", 200

    except Exception as e:
        import traceback
        msg = traceback.format_exc()
        print(f"ERROR: {msg}", file=sys.stderr)
        return f"Error: {e}\n\n{msg}", 500
