# kids-sports-sync

**Automatically keep your kids' sports calendars organized — and RSVP for you.**

If your kids are on multiple teams across multiple apps (TeamSnap, PlayMetrics, etc.), this pulls everything into one Google Calendar, sets RSVPs automatically based on whether an event is on your calendar, and sends you a daily email so you always know what's happening.

You set it up once. After that it just runs.

---

## What it does

- **One unified calendar.** All your kids' games and practices from every team, copied into a single "Kid Activities" Google Calendar you can share with your spouse.
- **Automatic RSVPs.** If an event is on your calendar, your kid is marked Going in TeamSnap or PlayMetrics. If you remove it, they're marked Not Going. No more forgetting.
- **Cancellation detection.** If a practice gets cancelled in the team app, it disappears from your calendar automatically — and you get an email so you know what happened.
- **Driving reminders.** Creates a "🚗 Leave for..." block on your personal calendar with drive-time built in.
- **Daily email summary.** Every morning: what RSVPs were set, what changed, what's coming up.

---

## Before you start

You'll need:
- A Mac or Windows computer
- A **Claude Code subscription** (this is how you'll set everything up — more on that below)
- A **Google account** (you almost certainly already have one via Gmail)
- About **30–45 minutes** the first time

---

## Step 1 — Get Claude Code

Claude Code is an AI assistant you talk to in plain English. You'll use it to set up these scripts — it reads the files, asks you questions, and handles the technical parts for you. Think of it as having a tech-savvy friend sitting next to you.

1. Go to **[claude.ai](https://claude.ai)** and create an account (or sign in if you have one)
2. Subscribe to **Claude Pro** (~$20/month) or higher — this is what gives you access to Claude Code
3. Download the **Claude Code desktop app**:
   - Mac: [claude.ai/download](https://claude.ai/download)
   - Windows: same link
4. Open the app and sign in with your Claude account

> **Why do I need a subscription?** Claude Code uses AI to understand your files and help you through setup. The free tier doesn't include this. Pro is the most affordable option that works.

---

## Step 2 — Download these scripts

1. Go to **[github.com/erinly112/kids-sports-sync](https://github.com/erinly112/kids-sports-sync)**
2. Click the green **Code** button → **Download ZIP**
3. Unzip the folder somewhere easy to find, like your Desktop or Documents
4. Rename the folder to `kids-sports-sync` if it isn't already

> **Don't have git?** That's fine — the ZIP download is all you need.

---

## Step 3 — Open Claude Code in the folder

1. Open the Claude Code desktop app
2. Click **Open Folder** (or similar — it may say "Open Project")
3. Navigate to the `kids-sports-sync` folder you just unzipped and select it
4. Claude Code will open with that folder as its workspace

---

## Step 4 — Let Claude set it up

In the Claude Code chat box, type exactly this:

> **"Help me set up this sports calendar sync for my family"**

Then just answer Claude's questions. It will:
- Ask about your kids' names and which teams they're on
- Help you find your TeamSnap team IDs
- Walk you through connecting your Google account
- Run a test to make sure everything is working
- Explain what to do if something goes wrong

You don't need to understand the code. Claude does the technical parts — you just answer questions like "what's your family's last name" and "which kid is on the Falcons?"

---

## Step 5 — Go live

Once Claude confirms the test run looks good, it will run the scripts for real and start the automation. Your unified calendar will populate, RSVPs will be set, and you'll get your first daily email the next morning.

---

## Automating it (so it runs every day without you doing anything)

After setup, Claude can configure your Mac to run these scripts automatically — every morning for RSVPs, once a week for the full calendar sync. Just ask:

> **"Set up the automation so this runs every day"**

---

## Troubleshooting

Something not working? Open Claude Code in the `kids-sports-sync` folder and describe the problem:

> "The script ran but I don't see any events in my Kid Activities calendar"

> "I got an error about Google authentication"

> "TeamSnap RSVPs aren't being set"

Claude will read the files and logs and help you fix it.

---

## Now build your own thing

This is just one example of what's possible. Once you're comfortable with Claude Code, you can ask it to build automations for other parts of your life — school reminders, grocery lists, work workflows, whatever's taking up mental energy.

The way to use it: just describe a problem you have and ask Claude how it could help. You don't need to know anything about code.

> **"I spend 20 minutes every Sunday figuring out who's driving where this week. Can we automate that?"**

> **"Our family has 4 different apps for activities and I'm constantly missing things. Can we consolidate?"**

Start there. Claude will ask clarifying questions and suggest an approach.

---

*Built by a sports parent, for sports parents. Improved with Claude Code.*
