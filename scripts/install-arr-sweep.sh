#!/usr/bin/env bash
# Root-only portion of the arr-sweep rollout, plus re-arming the pipeline
# canary timer. Run once with sudo after deploying arr-sweep.py/arr_sweep.py
# and running the sweep manually once. Never restarts media services.
set -euo pipefail
die() { echo "REFUSING: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run with sudo"
[[ $(hostname -s) == yggdrasil ]] || die "wrong host"
[[ $(findmnt -rn -T /mnt/library -o UUID) == 3af380cc-3f62-4ef3-b6cf-5dcab2c67469 ]] || die "wrong/missing library"
home=/home/twoplustwoone
repo=$home/pi4-ultimate-plex-stack
for f in $home/arr-sweep.py $home/arr_sweep.py $repo/systemd/arr-sweep.service $repo/systemd/arr-sweep.timer \
         /etc/systemd/system/pipeline-canary.timer; do
  [[ -f $f ]] || die "$f missing (deploy it first)"
done
[[ -d $repo/.local ]] || die "$repo/.local missing"
stamp=$(date -u +%Y%m%dT%H%M%SZ)

# Kuma first: the canary timer below may fire at once (Persistent catch-up)
# and must find Kuma running. Change it while stopped, in one transaction.
docker stop uptime-kuma >/dev/null
trap 'docker start uptime-kuma >/dev/null' EXIT
python3 - "$repo/.local/kuma-before-arr-sweep-$stamp.db" <<'PY'
import os, sqlite3, sys
TOKEN, NAME = 'aSw33pHb7xQ2kLm9', 'arr-sweep heartbeat'
# Kuma alerts once on the transition to DOWN; re-notify about daily instead.
RESEND = {12: (2700, 32), 13: (3600, 24)}
NEW_INTERVAL, NEW_RESEND = 25200, 4
db = sqlite3.connect('/home/pi/docker-configs/uptime-kuma/kuma.db')
columns = [r[1] for r in db.execute('PRAGMA table_info(monitor)')]
for needed in ('resend_interval', 'push_token', 'interval', 'active'):
    if needed not in columns:
        raise SystemExit('REFUSING: Kuma monitor table has no %s column' % needed)
expected = {12: ('pipeline-canary heartbeat', 'push'), 13: ('torrent cleanup heartbeat', 'push')}
for mid, (name, kind) in expected.items():
    row = db.execute('SELECT name, type, interval FROM monitor WHERE id=?', (mid,)).fetchone()
    if row != (name, kind, RESEND[mid][0]):
        raise SystemExit('REFUSING: Kuma monitor %d is not the expected %s push monitor' % (mid, name))
clash = db.execute('SELECT id FROM monitor WHERE push_token=? AND name<>?', (TOKEN, NAME)).fetchall()
if clash:
    raise SystemExit('REFUSING: push token already used by another monitor')
existing = db.execute('SELECT id FROM monitor WHERE name=?', (NAME,)).fetchall()
if len(existing) > 1:
    raise SystemExit('REFUSING: several monitors named %s' % NAME)

backup = sys.argv[1]
os.close(os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
with sqlite3.connect(backup) as target:
    db.backup(target)
print('Kuma backup:', backup)

with db:
    if existing:
        new_id = existing[0][0]
        print('monitor %r already exists (id %d)' % (NAME, new_id))
    else:
        overrides = {'name': NAME, 'push_token': TOKEN, 'interval': NEW_INTERVAL, 'active': 1}
        copied = [c for c in columns if c != 'id']
        select = ', '.join('?' if c in overrides else '[%s]' % c for c in copied)
        new_id = db.execute('INSERT INTO monitor (%s) SELECT %s FROM monitor WHERE id=13'
                            % (', '.join('[%s]' % c for c in copied), select),
                            [overrides[c] for c in copied if c in overrides]).lastrowid
        print('created push monitor %r (id %d) from monitor 13' % (NAME, new_id))
    db.execute('UPDATE monitor SET interval=?, active=1, push_token=? WHERE id=?', (NEW_INTERVAL, TOKEN, new_id))
    added = db.execute('''INSERT INTO monitor_notification (monitor_id, notification_id)
        SELECT ?, notification_id FROM monitor_notification m WHERE monitor_id=13 AND NOT EXISTS
        (SELECT 1 FROM monitor_notification WHERE monitor_id=? AND notification_id=m.notification_id)''',
        (new_id, new_id)).rowcount
    print('copied %d notification link(s) from monitor 13' % added)
    for mid, resend in [(12, RESEND[12][1]), (13, RESEND[13][1]), (new_id, NEW_RESEND)]:
        db.execute('UPDATE monitor SET resend_interval=? WHERE id=?', (resend, mid))
        print('monitor %d resend_interval=%d' % (mid, resend))
row = db.execute('SELECT type, interval, resend_interval, active, push_token FROM monitor WHERE id=?', (new_id,)).fetchone()
assert row == ('push', NEW_INTERVAL, NEW_RESEND, 1, TOKEN), 'arr-sweep monitor did not converge'
assert db.execute('SELECT COUNT(*) FROM monitor_notification WHERE monitor_id=?', (new_id,)).fetchone()[0] >= 1
db.close()
PY
docker start uptime-kuma >/dev/null
trap - EXIT
# Kuma may need a few seconds after its brief restart.
for attempt in {1..20}; do
  if curl -fsS --max-time 2 http://127.0.0.1:3001/ >/dev/null; then break; fi
  sleep 1
done
curl -fsS --max-time 2 http://127.0.0.1:3001/ >/dev/null || die "Kuma not answering after restart"

for unit in arr-sweep.service arr-sweep.timer; do
  install -m 0644 "$repo/systemd/$unit" "/etc/systemd/system/$unit"
  echo "installed /etc/systemd/system/$unit"
done

# The old drop-in reset its triggers twice; an empty assignment clears every
# trigger, so OnBootSec was lost and the canary never fired after the Oct 12
# reboot. Keep this identical to install-tracker-monitoring-timer.sh.
dropin=/etc/systemd/system/pipeline-canary.timer.d/20-tracker-monitoring.conf
desired=$(printf '%s\n' '[Timer]' 'OnBootSec=' 'OnCalendar=*:0/15')
install -d -m 0755 "$(dirname "$dropin")"
if [[ -f $dropin && $(cat "$dropin") == "$desired" ]]; then
  echo "canary drop-in already current: $dropin"
else
  if [[ -f $dropin ]]; then
    cp -a "$dropin" "$dropin.bak-$stamp"
    echo "backed up canary drop-in to $dropin.bak-$stamp"
  fi
  printf '%s\n' "$desired" > "$dropin"
  echo "rewrote $dropin to OnCalendar=*:0/15"
fi

systemctl daemon-reload
systemd-analyze verify arr-sweep.service arr-sweep.timer pipeline-canary.timer pipeline-canary.service
systemctl enable arr-sweep.timer pipeline-canary.timer
systemctl restart arr-sweep.timer pipeline-canary.timer
for timer in arr-sweep.timer pipeline-canary.timer; do
  next=$(systemctl show "$timer" -p NextElapseUSecRealtime --value)
  [[ -n $next && $next != 0 && $next != n/a ]] || die "$timer has no next trigger"
  echo "$timer next trigger: $next"
done
echo 'Installed arr-sweep timer and re-armed the canary timer. No media services restarted.'
echo 'arr-sweep.service was not started. pipeline-canary.service may run now: Persistent=true catches up its missed slots.'
