#!/usr/bin/env bash
# doctor.sh - read-only "is yggdrasil worth driving?" check. Exit 0 = all PASS.
#   .claude/skills/verify/bin/doctor.sh
# Set VERIFY_RUN_DIR to also save the transcript as $VERIFY_RUN_DIR/doctor.txt.
# Prints lengths and statuses only, never key values.
set -u
HOST="${VERIFY_HOST:-twoplustwoone@192.168.1.246}"
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST")

# Local checksums of the files the canary actually runs from ~/ on the host.
LOCAL_SUMS=$(cd "$REPO/scripts" && shasum -a 256 pipeline-canary.py tracker_health.py ipt_credit.py torrent_retirement.py host_checks.py)

run() {
"${SSH[@]}" bash -s -- "$(printf %q "$LOCAL_SUMS")" <<'REMOTE'
LOCAL_SUMS="$1"; fails=0
pass() { echo "PASS  $*"; }
warn() { echo "WARN  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails+1)); }
jqv() { jq -r "$1" 2>/dev/null; }

[ "$(hostname)" = yggdrasil ] && pass "host is yggdrasil" || fail "host is $(hostname), not yggdrasil"
[ "$(date +%Z)" = UTC ] && pass "host clock is UTC (timers need an explicit zone)" || warn "host clock is $(date +%Z)"

uuid=$(findmnt -rn -T /mnt/library -o UUID)
[ "$uuid" = 3af380cc-3f62-4ef3-b6cf-5dcab2c67469 ] && pass "/mnt/library is the live library disk ($(df -h /mnt/library | awk 'END{print $4" free"}'))" \
  || fail "/mnt/library UUID is '$uuid'"

for c in plex jellyfin radarr sonarr prowlarr seerr gluetun qbittorrent qbit_manage torrent-release-guard tautulli uptime-kuma unpackerr cloudflared; do
  s=$(docker inspect "$c" --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}nohealthcheck{{end}}' 2>/dev/null)
  case "$s" in running/healthy|running/nohealthcheck) pass "container $c $s";; *) fail "container $c: ${s:-missing}";; esac
done

g=$(docker inspect gluetun --format '{{.State.StartedAt}}'); q=$(docker inspect qbittorrent --format '{{.State.StartedAt}}')
nm=$(docker inspect qbittorrent --format '{{.HostConfig.NetworkMode}}')
case "$nm" in container:*) [[ "$g" < "$q" || "$g" = "$q" ]] && pass "qbittorrent joined gluetun's namespace and started after it" \
   || fail "gluetun restarted after qbittorrent: qbittorrent has a dead network, restart it";;
  *) fail "qbittorrent network mode is $nm";; esac

curl -sf -m 6 http://192.168.1.246:32400/identity >/dev/null && pass "Plex answers on 192.168.1.246:32400" || fail "Plex not answering"
[ "$(curl -s -m 6 http://192.168.1.246:8096/health)" = Healthy ] && pass "Jellyfin answers on 192.168.1.246:8096" || fail "Jellyfin not answering"

cd ~/yggdrasil || exit 1
for p in "radarr 7878 v3" "sonarr 8989 v3" "prowlarr 9696 v1"; do
  set -- $p; key=$(docker exec "$1" sed -n 's#.*<ApiKey>\(.*\)</ApiKey>.*#\1#p' /config/config.xml)
  [ "${#key}" -eq 32 ] || { fail "$1 API key length ${#key}"; continue; }
  errs=$(curl -s -m 8 -H "X-Api-Key: $key" "http://127.0.0.1:$2/api/$3/health" | jq '[.[]|select(.type=="error")]|length' 2>/dev/null)
  [ "$errs" = 0 ] && pass "$1 API ok, 0 health errors" || fail "$1 health errors: ${errs:-unreachable}"
done
sk=$(docker exec seerr cat /app/config/settings.json | jq -r .main.apiKey)
[ "${#sk}" -gt 20 ] && v=$(curl -s -m 8 -H "X-Api-Key: $sk" http://127.0.0.1:5055/api/v1/status | jqv .version)
[ -n "${v:-}" ] && pass "seerr API ok (v$v)" || fail "seerr API unreachable or key empty"

# qBittorrent: credentials from .env, session cookie in a scratch file, removed on exit.
envv() { sed -n "s/^$1=//p" .env | head -1 | sed 's/^"//;s/"$//'; }
jar=$(mktemp); trap 'rm -f "$jar"' EXIT
login=$(curl -s -m 8 -c "$jar" --data-urlencode "username=$(envv QBITTORRENT_USER)" --data-urlencode "password=$(envv QBITTORRENT_PASS)" -H "Referer: http://127.0.0.1:8080" http://127.0.0.1:8080/api/v2/auth/login)
if [ "$login" = "Ok." ]; then
  act=$(curl -s -m 8 -b "$jar" http://127.0.0.1:8080/api/v2/app/preferences | jqv .max_ratio_act)
  [ "$act" = 0 ] && pass "qBittorrent login ok, global share-limit action is Stop (0)" \
    || fail "qBittorrent max_ratio_act=$act (must stay 0 or it deletes torrents bypassing the recycle bin)"
else fail "qBittorrent login refused"; fi

age=$(( $(date +%s) - $(stat -c %Y ~/.canary-tracker-health.json) ))
[ "$(systemctl is-active pipeline-canary.timer)" = active ] && [ "$age" -lt 1200 ] \
  && pass "pipeline-canary.timer active, last run ${age}s ago" || fail "canary timer/last run stale (age ${age}s)"

down=$(docker exec uptime-kuma node -e '
const s=require("@louislam/sqlite3");const db=new s.Database("/app/data/kuma.db",s.OPEN_READONLY);
db.all("SELECT m.name n,(SELECT status FROM heartbeat h WHERE h.monitor_id=m.id ORDER BY time DESC LIMIT 1) st FROM monitor m WHERE m.active=1",(e,r)=>{if(e)process.exit(1);console.log(r.filter(x=>x.st!==1).map(x=>x.n).join(", "));db.close()})' 2>/dev/null)
[ -z "$down" ] && pass "all active Uptime Kuma monitors up" || warn "Uptime Kuma monitors not up: $down (real findings, not a reason to skip driving)"

cd ~ && drift=$(echo "$LOCAL_SUMS" | while read -r sum f; do [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$sum" ] || echo -n "$f "; done)
[ -z "$drift" ] && pass "deployed canary modules match this checkout" || warn "deployed copy differs from this checkout: $drift"

echo; [ "$fails" -eq 0 ] && echo "DOCTOR OK" || echo "DOCTOR: $fails failure(s)"; exit "$fails"
REMOTE
}

if [ -n "${VERIFY_RUN_DIR:-}" ]; then mkdir -p "$VERIFY_RUN_DIR"; run | tee "$VERIFY_RUN_DIR/doctor.txt"; exit "${PIPESTATUS[0]}"; else run; fi
