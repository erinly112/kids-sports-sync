#!/bin/zsh
# Daily 7am job: sync team calendars → Kids Activities, then set RSVPs + email digest

DIR="$HOME/kids-sports-sync"

# Step 1: pull latest events from team calendars into Kids Activities
/usr/bin/python3 "$DIR/sports-cal-sync.py" 2>>"$DIR/sports-cal-sync.log"

# Step 2: read Kids Activities, set TeamSnap RSVPs, capture HTML digest
OUTPUT=$(/usr/bin/python3 "$DIR/teamsnap-rsvp.py" --apply 2>>"$DIR/teamsnap-rsvp.log")

/usr/bin/python3 "$DIR/send-email.py" \
  --to familynch@gmail.com \
  --subject "Sports RSVP — $(date '+%A, %B %-d, %Y')" \
  --html \
  --body "${OUTPUT}"
