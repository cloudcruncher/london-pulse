#!/bin/bash
# Backup for GitHub's cron: start the Daily update only if none has succeeded or is running since 00:00 UTC today.
# Run by launchd (see docs/REFRESH.md). TODAY=YYYY-MM-DD overrides the date and DRY_RUN=1 only reports, for testing.
set -u
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
REPO="cloudcruncher/london-pulse"
today="${TODAY:-$(date -u +%F)}"

runs=$(gh run list -R "$REPO" --workflow daily.yml --created ">=$today" --limit 20 --json status,conclusion 2>&1) || {
  echo "$(date -u +%FT%TZ) cannot list runs (gh auth or network): $runs"; exit 1; }
ok=$(echo "$runs" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(sum(1 for x in r if x["conclusion"]=="success" or x["status"] in ("queued","in_progress","waiting","pending")))')

if [ "$ok" -gt 0 ]; then
  echo "$(date -u +%FT%TZ) daily update already ran or is running for $today: nothing to do"; exit 0
fi
if [ "${DRY_RUN:-0}" = 1 ]; then echo "$(date -u +%FT%TZ) would trigger daily update for $today"; exit 0; fi
gh workflow run daily.yml -R "$REPO" && echo "$(date -u +%FT%TZ) no successful daily run for $today: triggered one"
