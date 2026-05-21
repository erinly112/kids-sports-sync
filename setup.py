#!/usr/bin/env python3
"""
Interactive setup wizard for kids-sports-sync.

Run once after cloning the repo:
  python3 setup.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE       = Path(__file__).parent
CONFIG_DIR = Path(os.environ.get("SCRIPT_CONFIG_DIR", Path.home() / ".config" / "google-tasks-sync"))

BOLD  = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED   = "\033[31m"
RESET = "\033[0m"

def h(text):  print(f"\n{BOLD}{text}{RESET}")
def ok(text): print(f"{GREEN}✓{RESET} {text}")
def warn(text): print(f"{YELLOW}⚠{RESET}  {text}")
def err(text):  print(f"{RED}✗{RESET} {text}")
def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"  {prompt}{suffix}: ").strip()
    return val or default or ""


# ── Step 1: Python version ────────────────────────────────────────────────────

h("Step 1 — Python version")
if sys.version_info < (3, 9):
    err(f"Python 3.9+ required (you have {sys.version}). Please upgrade.")
    sys.exit(1)
ok(f"Python {sys.version.split()[0]}")


# ── Step 2: pip dependencies ──────────────────────────────────────────────────

h("Step 2 — Install dependencies")
req = HERE / "requirements.txt"
if req.exists():
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req), "-q"])
    if result.returncode == 0:
        ok("Dependencies installed")
    else:
        err("pip install failed — check the error above and re-run setup.")
        sys.exit(1)
else:
    warn("requirements.txt not found — skipping.")


# ── Step 3: config.json ───────────────────────────────────────────────────────

h("Step 3 — config.json")
config_path = HERE / "config.json"
example_path = HERE / "config.example.json"

if config_path.exists():
    ok("config.json already exists")
else:
    if not example_path.exists():
        err("config.example.json not found. Make sure you cloned the full repo.")
        sys.exit(1)
    print(f"\n  config.json is missing. Copy and edit config.example.json:")
    print(f"    cp {example_path} {config_path}")
    print(f"  Then fill in your family, kids, and teams — then re-run this setup.")
    sys.exit(0)

try:
    cfg = json.loads(config_path.read_text())
    ok(f"config.json loaded — {len(cfg.get('teams', []))} team(s) configured")
except json.JSONDecodeError as e:
    err(f"config.json has invalid JSON: {e}")
    sys.exit(1)

# Validate required fields
missing = []
for field in ["family", "kids", "target_calendar", "teams"]:
    if not cfg.get(field):
        missing.append(field)
if missing:
    err(f"config.json is missing required fields: {', '.join(missing)}")
    sys.exit(1)

has_teamsnap  = any(t.get("rsvp_platform") == "teamsnap"  for t in cfg["teams"])
has_sportngin = any(t.get("rsvp_platform") == "sportngin" for t in cfg["teams"])
needs_driving = any(t.get("driving") for t in cfg["teams"])


# ── Step 4: Credentials directory ────────────────────────────────────────────

h("Step 4 — Credentials directory")
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
ok(f"Credentials directory: {CONFIG_DIR}")


# ── Step 5: Google OAuth credentials ─────────────────────────────────────────

h("Step 5 — Google OAuth credentials")
gcal_creds = CONFIG_DIR / "credentials.json"

if gcal_creds.exists():
    ok("Google credentials.json found")
else:
    print("""
  You need a Google OAuth 2.0 client ID (Desktop app type).

  1. Go to: https://console.cloud.google.com/apis/credentials
  2. Create a project (or select an existing one)
  3. Enable: Google Calendar API  (and Gmail API if you want deletion alerts)
  4. Create credentials → OAuth client ID → Desktop app
  5. Download the JSON and save it to:
""")
    print(f"     {gcal_creds}\n")
    input("  Press Enter once you've saved credentials.json... ")
    if not gcal_creds.exists():
        err(f"Still not found at {gcal_creds}. Re-run setup when ready.")
        sys.exit(1)
    ok("Google credentials.json found")


# ── Step 6: Google Calendar auth ─────────────────────────────────────────────

h("Step 6 — Authorize Google Calendar")
cal_token = CONFIG_DIR / "calendar-token.json"

if cal_token.exists():
    ok("Google Calendar already authorized")
else:
    print("\n  Running sports-cal-sync in dry-run mode to trigger Google auth...")
    print("  A browser window will open — sign in and grant calendar access.\n")
    result = subprocess.run([sys.executable, str(HERE / "sports-cal-sync.py"), "--dry-run", "--days", "1"])
    if cal_token.exists():
        ok("Google Calendar authorized")
    else:
        err("Authorization did not complete. Re-run setup to try again.")
        sys.exit(1)


# ── Step 7: Gmail auth (optional, for deletion alerts) ───────────────────────

h("Step 7 — Gmail auth (optional — for deletion email alerts)")
gmail_token = CONFIG_DIR / "gmail-token.json"
notify_email = cfg["family"].get("notify_email", "")

if gmail_token.exists():
    ok("Gmail already authorized")
elif notify_email:
    ans = ask(f"Set up Gmail to send deletion alerts to {notify_email}? (y/n)", "y")
    if ans.lower() == "y":
        print("\n  A browser window will open — sign in with the Gmail account that will send alerts.")
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(
                str(gcal_creds), ["https://www.googleapis.com/auth/gmail.send"]
            )
            creds = flow.run_local_server(port=0)
            gmail_token.write_text(creds.to_json())
            ok("Gmail authorized")
        except Exception as e:
            warn(f"Gmail auth failed: {e} — deletion alerts will be skipped.")
    else:
        warn("Skipping Gmail auth — deletion alerts will be disabled.")
else:
    warn("No notify_email in config — skipping Gmail auth.")


# ── Step 8: TeamSnap auth ─────────────────────────────────────────────────────

if has_teamsnap:
    h("Step 8 — TeamSnap credentials")
    ts_creds = CONFIG_DIR / "teamsnap-credentials.json"
    ts_token = CONFIG_DIR / "teamsnap-token.json"

    if ts_token.exists():
        ok("TeamSnap already authorized")
    elif ts_creds.exists():
        print("\n  Running teamsnap-rsvp to complete auth...")
        subprocess.run([sys.executable, str(HERE / "teamsnap-rsvp.py"), "--days", "1"])
        ok("TeamSnap auth complete")
    else:
        print("""
  You need a TeamSnap OAuth client ID and secret.

  1. Go to: https://developer.teamsnap.com  (sign up if needed)
  2. Create an app → get client_id and client_secret
  3. Save to:
""")
        print(f"     {ts_creds}\n")
        print('  Format: {"client_id": "...", "client_secret": "..."}')
        print()
        input("  Press Enter once you've saved teamsnap-credentials.json... ")
        if ts_creds.exists():
            print("\n  A browser window will open — sign in to TeamSnap.")
            subprocess.run([sys.executable, str(HERE / "teamsnap-rsvp.py"), "--days", "1"])
            ok("TeamSnap auth complete")
        else:
            warn(f"teamsnap-credentials.json not found — TeamSnap RSVPs will be skipped.")
else:
    h("Step 8 — TeamSnap")
    ok("No TeamSnap teams in config — skipping")


# ── Step 9: SportNgin auth ────────────────────────────────────────────────────

if has_sportngin:
    h("Step 9 — SportNgin / PlayMetrics credentials")
    sn_creds = CONFIG_DIR / "sportngin-credentials.json"

    if sn_creds.exists():
        ok("sportngin-credentials.json found")
    else:
        print(f"""
  Save your PlayMetrics login to:
    {sn_creds}

  Format: {{"email": "you@example.com", "password": "yourpassword"}}

  Note: sportngin_team_id and sportngin_persona_id go in config.json (already there).
  To find your persona_id, run:
    python3 sportngin-rsvp.py  (after saving credentials)
""")
        input("  Press Enter once you've saved sportngin-credentials.json... ")
        if sn_creds.exists():
            ok("sportngin-credentials.json found")
        else:
            warn("Not found — SportNgin RSVPs will be skipped.")
else:
    h("Step 9 — SportNgin")
    ok("No SportNgin teams in config — skipping")


# ── Step 10: Dry run ──────────────────────────────────────────────────────────

h("Step 10 — Dry run test")
print("\n  Running sports-cal-sync --dry-run to verify everything works...\n")
result = subprocess.run([sys.executable, str(HERE / "sports-cal-sync.py"), "--dry-run"])
if result.returncode == 0:
    ok("sports-cal-sync dry run passed")
else:
    warn("sports-cal-sync dry run had errors — check output above.")

print(f"""
{BOLD}Setup complete!{RESET}

Next steps:
  1. Test the sync:    python3 sports-cal-sync.py --dry-run
  2. Run for real:     python3 sports-cal-sync.py
  3. Test RSVPs:       python3 teamsnap-rsvp.py       (TeamSnap)
                       python3 sportngin-rsvp.py       (PlayMetrics)
  4. Automate:         Add scripts to launchd (Mac) or cron
                       sports-cal-sync.py  → weekly, e.g. Fridays 5pm
                       teamsnap-rsvp.py    → daily, e.g. 7am
                       sportngin-rsvp.py   → daily, e.g. 7am

  See README.md for full automation instructions.
""")
