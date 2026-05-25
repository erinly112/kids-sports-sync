# Cowork setup instructions — for Claude

When the user asks you to set up this project, follow these steps in order.
Use the connected Chrome browser where noted.
Ask questions one at a time — never a list all at once.
Be patient and conversational — the user may not be technical.

## Step 1 — Install Claude in Chrome

In the connected Chrome (Claude sandbox profile):
- Navigate to the Chrome Web Store
- Search for **Claude** by Anthropic
- Install the extension
- Confirm it appears in the toolbar

## Step 2 — Download the project

In Chrome, navigate to:
```
https://github.com/erinly112/kids-sports-sync
```
- Click **Code** → **Download ZIP**
- When the download completes, unzip it
- The unzipped folder will be named `kids-sports-sync-main` — rename it to `kids-sports-sync` and move it to the home folder (`~/kids-sports-sync`)

## Step 3 — Check Python

```bash
python3 --version
```

Python 3.9 or higher: proceed. If missing or below 3.9, direct the user to python.org/downloads to install the latest Python 3, then continue.

## Step 4 — Download Google credentials

Download the app credentials file and place it where the scripts expect it:

```bash
mkdir -p ~/.config/google-tasks-sync
curl -L "https://drive.google.com/uc?export=download&id=1fPHkK8Pq1CKEj632U66sT5zPpfGfxZlt" -o ~/.config/google-tasks-sync/credentials.json
```

## Step 5 — Build config.json

Tell the user: *"I need to ask you a few questions about your family so I can set everything up."*

Open `~/kids-sports-sync/config.example.json` so you can see every field. Ask the user the following, one at a time, and write their answers into `~/kids-sports-sync/config.json`:

1. *"What's your family's last name?"* → `family.last_name`
2. *"What email address should get the daily summary?"* → `family.notify_email`
3. *"What are your kids' names?"* → `kids[].name` (use the default colors from the example file)
4. *"Is there a Google Calendar where all their sports events collect — something like 'Kid Activities'? If not, I can help you create one."* → `target_calendar`
5. *"What's your home address?"* → `home_address`
6. *"What's the name of your personal Google Calendar — the one where you'd want drive-time reminders to appear?"* → `driver_calendar`

Then for each sports team, one at a time:
- *"What's the name of this team's Google Calendar?"* — must match the calendar name in Google Calendar exactly, including spacing and capitalization
- *"Which of your kids are on this team?"*
- *"Is this a TeamSnap team, a PlayMetrics/SportsEngine team, or something else?"*
- *"Do you drive to this team's games, or does another parent always drive?"*

For TeamSnap teams: ask the user to log into app.teamsnap.com, click the team, and read the number out of the URL — that's the `teamsnap_team_id`.

For SportNgin/PlayMetrics teams: offer to help look up `sportngin_team_id` and `sportngin_persona_id` via the API once credentials are set up.

Write all answers into `config.json`. Validate it is valid JSON before continuing.

## Step 6 — Run setup.py

```bash
cd ~/kids-sports-sync && python3 setup.py
```

`setup.py` is an interactive wizard — it will pause at several points and you need to guide the user through each one:

**Google sign-in:** A browser window will open. Tell the user: *"Please sign in with the Google account that has your family's calendars, then click Allow."* Wait for it to complete before moving on.

**Gmail auth (optional):** `setup.py` will ask if the user wants email alerts for deleted events. If `notify_email` is set in config, recommend saying yes — it's useful. A second Google sign-in window will open for this.

**TeamSnap (if applicable):** If any teams use TeamSnap, `setup.py` will ask for a TeamSnap developer client ID and secret. Walk the user through this:
- Open Chrome and navigate to developer.teamsnap.com
- Sign up for a developer account if needed
- Create a new app — name doesn't matter
- Copy the client ID and client secret
- Save them to `~/.config/google-tasks-sync/teamsnap-credentials.json` in this format:
  ```json
  {"client_id": "...", "client_secret": "..."}
  ```
- Press Enter in the terminal when done — a browser will open to authorize TeamSnap

**SportNgin (if applicable):** If any teams use SportNgin/PlayMetrics, `setup.py` will ask for the user's PlayMetrics login. Save it to `~/.config/google-tasks-sync/sportngin-credentials.json`:
  ```json
  {"email": "...", "password": "..."}
  ```

`setup.py` runs a dry-run test at the end — it will show what events it found. If any calendars aren't matching, the most common cause is a name mismatch: check that `calendar_name` in `config.json` matches the exact name in Google Calendar.

## Step 7 — Done

Tell the user in plain English:
- Sports events from their kids' team calendars will now copy automatically into their unified Kid Activities calendar
- Drive-time reminders will appear on their personal calendar before each game
- They can come back to Cowork any time to re-run a sync, update a team, or troubleshoot
