# kids-sports-sync

Automated sports calendar sync and RSVP management for families with kids on multiple teams.

## What this does

- **sports-cal-sync.py** — copies events from each team's source calendar into a single unified "Kid Activities" Google Calendar. Runs deletions when events are cancelled. Emails you if anything is auto-deleted.
- **teamsnap-rsvp.py** — reads the unified calendar and sets TeamSnap RSVPs (Going / Not Going) for each kid automatically. Sends a daily email summary.
- **sportngin-rsvp.py** — same for PlayMetrics / SportsEngine / SportNgin teams.
- **drive-to-games.py** — creates "🚗 Leave for..." reminders on your personal calendar with drive-time estimates using OpenStreetMap.

## If a user is setting this up for the first time

Walk them through these steps in order. Be conversational and patient — they may not be technical.

1. **Check for config.json.** If it doesn't exist, tell them to copy `config.example.json` to `config.json`. Open both files side by side and walk them through each field. Key things to collect:
   - Family last name and notification email
   - Kids' names (and let them pick badge colors — the hex codes in the example are nice defaults)
   - Target calendar name (their unified "Kid Activities" calendar — they may need to create it in Google Calendar first)
   - For each team: the Google Calendar name (must match exactly), which kids are on it, the platform (teamsnap or sportngin), and whether driving reminders are needed

2. **Find TeamSnap team IDs.** Log in to app.teamsnap.com → click a team → the team ID is in the URL: `app.teamsnap.com/team/XXXXXXXX/...`

3. **Find SportNgin/PlayMetrics team and persona IDs.** These are trickier — offer to help look them up by running a quick API call, or check the existing `sportngin-credentials.json` if migrating from another setup.

4. **Run setup.py.** `python3 setup.py` — it handles Google OAuth (opens a browser), TeamSnap OAuth, and SportNgin login. Guide them through any browser prompts.

5. **Dry run.** `python3 sports-cal-sync.py --dry-run` — confirm events are being found. Then `python3 teamsnap-rsvp.py` to preview RSVPs.

6. **Go live.** `python3 sports-cal-sync.py` then `python3 teamsnap-rsvp.py --apply`

## Common issues

- **"config.json not found"** — they forgot to copy config.example.json, or ran the script from the wrong directory
- **"Calendar not found"** — the calendar_name in config.json doesn't exactly match the name in Google Calendar (check for trailing spaces, different capitalization)
- **Google auth browser doesn't open** — run `python3 setup.py` and follow the terminal prompt; or open the URL it prints manually
- **TeamSnap "no members found"** — the team ID is wrong, or the OAuth token is for a different TeamSnap account than the one that's on the team
- **SportNgin login fails** — password may have special characters; try resetting it to something simpler temporarily

## Automation (once everything works)

Mac (launchd): create a `.plist` in `~/Library/LaunchAgents/`. Ask the user what time they want the daily emails and help them write the plist.

Suggested schedule:
- `sports-cal-sync.py` — weekly, e.g. Fridays at 5pm
- `teamsnap-rsvp.py --apply` — daily at 7am
- `sportngin-rsvp.py --apply` — daily at 7am
- `drive-to-games.py` — weekly, same time as cal sync

## Key files

| File | Purpose |
|---|---|
| `config.json` | Your family config — gitignored, never committed |
| `config.example.json` | Template to copy |
| `sportsync_config.py` | Shared config loader (don't edit) |
| `setup.py` | Interactive setup wizard |
| `requirements.txt` | Python dependencies |
