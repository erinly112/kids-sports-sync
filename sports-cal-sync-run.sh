#!/bin/zsh
# Weekly sports calendar sync — runs every Sunday at 5pm via launchd
# Syncs team calendars into Kids Activities, emails driving plan + schedule changes

DIR="$HOME/kids-sports-sync"

REMINDER_OUTPUT=$(/usr/bin/python3 ~/context/admin/commute/driving-plan.py --apply 2>/dev/null \
  | awk '/^Texts to send this week:/{found=1; next} found && NF{print}')

SYNC_OUTPUT=$(/usr/bin/python3 "$DIR/sports-cal-sync.py" 2>>"$DIR/sports-cal-sync.log" \
  | grep -E '(↻ Updated|✓ Copied|\[dry run\]|Done:|✗)')

[[ -z "$REMINDER_OUTPUT" ]] && REMINDER_OUTPUT="None this week"
[[ -z "$SYNC_OUTPUT" ]]     && SYNC_OUTPUT="No schedule changes"

/usr/bin/python3 "$DIR/send-email.py" \
  --to familynch@gmail.com \
  --subject "Kids calendar synced — driving plan for the week" \
  --body "=== Texts to send this week ===
${REMINDER_OUTPUT}

=== Schedule changes ===
${SYNC_OUTPUT}"
