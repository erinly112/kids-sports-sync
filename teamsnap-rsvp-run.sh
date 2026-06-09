#!/bin/zsh
# Daily TeamSnap RSVP sync — runs at 7am via launchd
# Reads Kids Activities calendar, sets RSVPs, emails digest to family

DIR="$HOME/kids-sports-sync"

OUTPUT=$(/usr/bin/python3 "$DIR/teamsnap-rsvp.py" --apply 2>>"$DIR/teamsnap-rsvp.log")

/usr/bin/python3 "$DIR/send-email.py" \
  --to familynch@gmail.com \
  --subject "Sports RSVP — $(date '+%A, %B %-d, %Y')" \
  --html \
  --body "${OUTPUT}"
