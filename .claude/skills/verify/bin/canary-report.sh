#!/usr/bin/env bash
# canary-report.sh - run the deployed pipeline canary in --report mode and PROVE
# it changed nothing: its state files must be byte-identical before and after.
#   .claude/skills/verify/bin/canary-report.sh
# Never pass --apply: that posts to Discord, pings Kuma, and can stop torrents.
set -u
HOST="${VERIFY_HOST:-twoplustwoone@192.168.1.246}"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" bash -s <<'REMOTE'
# The real timer fires on the quarter hour and rewrites state; sit out that window
# (run takes ~5s) so a concurrent scheduled run cannot read as report-mode mutation.
m=$(( $(date +%s) % 900 )); if [ "$m" -lt 90 ]; then sleep $((90 - m)); elif [ "$m" -gt 880 ]; then sleep $((990 - m)); fi
S="$HOME/.canary-tracker-health.json $HOME/.canary-stopped-released.json $HOME/.canary-last-summary"
before=$(sha256sum $S)
echo "=== canary --report ($(date -u +%FT%TZ))"
python3 -B ~/pipeline-canary.py --report; rc=$?
after=$(sha256sum $S)
echo "=== exit code $rc"
if [ "$before" = "$after" ]; then echo "STATE UNCHANGED: report mode was read-only"; else echo "STATE CHANGED: report mode wrote state, investigate"; exit 3; fi
exit $rc
REMOTE
