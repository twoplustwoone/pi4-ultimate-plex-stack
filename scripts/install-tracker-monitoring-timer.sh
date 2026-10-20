#!/usr/bin/env bash
# Root-only portion of the tracker-monitor rollout; never restarts media services.
set -euo pipefail
die() { echo "REFUSING: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run with sudo"
[[ $(hostname -s) == yggdrasil ]] || die "wrong host"
[[ $(findmnt -rn -T /mnt/library -o UUID) == 3af380cc-3f62-4ef3-b6cf-5dcab2c67469 ]] || die "wrong/missing library"
[[ $(systemctl show pipeline-canary.service -p User --value) == twoplustwoone ]] || die "unexpected service user"
systemctl show pipeline-canary.service -p ExecStart --value | grep -Fq '/home/twoplustwoone/pipeline-canary.py --apply' || die "unexpected service command"
[[ -f /home/twoplustwoone/tracker_health.py ]] || die "monitor module not deployed"
[[ -f /home/twoplustwoone/.canary-tracker-health.json ]] || die "monitor state not initialized"
target=/etc/systemd/system/pipeline-canary.timer.d/20-tracker-monitoring.conf
install -d -m 0755 /etc/systemd/system/pipeline-canary.timer.d
if [[ -f $target ]]; then cp -a "$target" "$target.bak-$(date -u +%Y%m%dT%H%M%SZ)"; fi
# An empty assignment resets every trigger, monotonic and calendar alike.
printf '%s\n' '[Timer]' 'OnBootSec=' 'OnCalendar=*:0/15' > "$target"
systemctl daemon-reload
systemd-analyze verify pipeline-canary.timer pipeline-canary.service
systemctl enable pipeline-canary.timer
systemctl restart pipeline-canary.timer
# Change Kuma while stopped, through a guarded SQLite transaction. Keep its
# expiry coupled to this timer so the old six-hour schedule cannot false-alarm.
docker stop uptime-kuma >/dev/null
trap 'docker start uptime-kuma >/dev/null' EXIT
python3 - <<'PY'
import sqlite3, pathlib, datetime, os
dbpath='/home/pi/docker-configs/uptime-kuma/kuma.db'
db=sqlite3.connect(dbpath)
row=db.execute('SELECT name,type,interval FROM monitor WHERE id=12').fetchone()
if row is None or row[:2] != ('pipeline-canary heartbeat','push') or row[2] not in (21600,2700):
    raise SystemExit('REFUSING: unexpected Kuma heartbeat monitor')
folder=pathlib.Path('/home/twoplustwoone/pi4-ultimate-plex-stack/.local')
backup=folder/('kuma-before-tracker-timer-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.db')
fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
with sqlite3.connect(backup) as target:
    db.backup(target)
with db:
    db.execute('UPDATE monitor SET interval=2700,retry_interval=60,maxretries=0 WHERE id=12')
assert db.execute('SELECT interval FROM monitor WHERE id=12').fetchone()[0]==2700
db.close()
print('Kuma heartbeat expiry set to 45 minutes; backup:',backup)
PY
docker start uptime-kuma >/dev/null
trap - EXIT
# Kuma may need a few seconds after its brief restart.
for attempt in {1..20}; do
  if curl -fsS --max-time 2 http://127.0.0.1:3001/ >/dev/null; then break; fi
  sleep 1
done
systemctl start pipeline-canary.service
systemctl show pipeline-canary.timer -p TimersMonotonic -p NextElapseUSecMonotonic
echo 'Installed 15-minute canary timer. No media services restarted.'
