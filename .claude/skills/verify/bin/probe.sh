#!/usr/bin/env bash
# probe.sh - authenticated, GET-only call to a service API on yggdrasil.
#   probe.sh <radarr|sonarr|prowlarr|seerr|tautulli|plex|qbit|kuma> <path[?query]> [jq-filter]
#   probe.sh radarr  '/api/v3/queue?pageSize=20' '.records[]|{title,status}'
#   probe.sh tautulli '?cmd=get_activity'
#   probe.sh qbit    '/api/v2/torrents/info?filter=downloading' 'length'
#   probe.sh kuma    /monitors '.[]|select(.status==0)'   # read-only SQLite read; status 1=up 0=down
# Keys and tokens are resolved on the host and never printed. Endpoints that
# return secrets (config, indexers, clients, notifications, settings, preferences)
# are refused unless a jq filter picks out the fields you want.
set -u
[ $# -ge 2 ] || { sed -n '2,10p' "$0"; exit 2; }
svc=$1; path=$2; filter=${3:-.}
case "$svc" in radarr|sonarr|prowlarr|seerr|tautulli|plex|qbit|kuma) ;; *) echo "unknown service $svc" >&2; exit 2;; esac
[[ "$path" =~ ^[A-Za-z0-9_./?\&=%,:+-]*$ ]] || { echo "refusing path with unsafe characters" >&2; exit 2; }
if [ "$filter" = . ] && [[ "$path" =~ (/config|/indexer|/downloadclient|/notification|/settings|/preferences|/auth|/user|get_settings|get_plex_token|cmd=get_users) ]]; then
  echo "refusing: $path can return secrets; pass a jq filter that selects safe fields" >&2; exit 2
fi
HOST="${VERIFY_HOST:-twoplustwoone@192.168.1.246}"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" bash -s -- "$(printf %q "$svc")" "$(printf %q "$path")" "$(printf %q "$filter")" <<'REMOTE' 
svc=$1; path=$2; filter=$3
cd ~/pi4-ultimate-plex-stack
BP=$(sed -n 's/^BASE_PATH=//p' .env | head -1 | tr -d '"')
arr() { k=$(docker exec "$1" sed -n 's#.*<ApiKey>\(.*\)</ApiKey>.*#\1#p' /config/config.xml); curl -sS -m 30 -H "X-Api-Key: $k" "http://127.0.0.1:$2$path"; }
case "$svc" in
  radarr)   out=$(arr radarr 7878);;
  sonarr)   out=$(arr sonarr 8989);;
  prowlarr) out=$(arr prowlarr 9696);;
  seerr)    k=$(docker exec seerr cat /app/config/settings.json | jq -r .main.apiKey)
            out=$(curl -sS -m 30 -H "X-Api-Key: $k" "http://127.0.0.1:5055$path");;
  tautulli) k=$(sed -n 's/^api_key = //p' "$BP/tautulli/config/config.ini" | head -1)
            sep='?'; case "$path" in \?*) q="${path#\?}";; *) q="${path#*\?}";; esac
            out=$(curl -sS -m 30 "http://127.0.0.1:8181/api/v2?apikey=$k&$q");;
  plex)     t=$(sed -n 's/.*PlexOnlineToken="\([^"]*\)".*/\1/p' "$BP/plex/config/Library/Application Support/Plex Media Server/Preferences.xml")
            out=$(curl -sS -m 30 -H "X-Plex-Token: $t" -H "Accept: application/json" "http://192.168.1.246:32400$path");;
  qbit)     envv() { sed -n "s/^$1=//p" .env | head -1 | sed 's/^"//;s/"$//'; }
            jar=$(mktemp); trap 'rm -f "$jar"' EXIT
            curl -sS -m 10 -o /dev/null -c "$jar" --data-urlencode "username=$(envv QBITTORRENT_USER)" --data-urlencode "password=$(envv QBITTORRENT_PASS)" -H "Referer: http://127.0.0.1:8080" http://127.0.0.1:8080/api/v2/auth/login
            out=$(curl -sS -m 30 -b "$jar" "http://127.0.0.1:8080$path");;
  kuma)     out=$(docker exec uptime-kuma node -e '
const s=require("@louislam/sqlite3");const db=new s.Database("/app/data/kuma.db",s.OPEN_READONLY);
db.all("SELECT m.name,m.type,(SELECT status FROM heartbeat h WHERE h.monitor_id=m.id ORDER BY time DESC LIMIT 1) status,(SELECT time FROM heartbeat h WHERE h.monitor_id=m.id ORDER BY time DESC LIMIT 1) time_utc,(SELECT msg FROM heartbeat h WHERE h.monitor_id=m.id ORDER BY time DESC LIMIT 1) msg FROM monitor m WHERE m.active=1",(e,r)=>{if(e){console.error(e.message);process.exit(1)}console.log(JSON.stringify(r));db.close()})'
            );;
esac
printf '%s' "$out" | jq -c "$filter" 2>/dev/null || printf '%s\n' "$out"
REMOTE
