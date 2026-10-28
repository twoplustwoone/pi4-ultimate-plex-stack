#!/bin/sh
# gluetun's VPN_PORT_FORWARDING_UP_COMMAND: set qBittorrent's listen port to the
# port PIA just forwarded. qBittorrent shares gluetun's network namespace, so its
# WebUI is 127.0.0.1:8080 here. Logs in like any other client; local auth bypass
# stays off. Usage: gluetun-qbt-port.sh <port>[,<port>...]
set -eu

port=${1%%,*}
base=http://127.0.0.1:8080/api/v2
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT

urlencode() {
  s=$1 out=
  while [ -n "$s" ]; do
    c=${s%"${s#?}"} s=${s#?}
    case $c in
      [A-Za-z0-9.~_-]) out=$out$c ;;
      *) out=$out$(printf '%%%02X' "'$c") ;;
    esac
  done
  printf %s "$out"
}

form="username=$(urlencode "$QBT_USER")&password=$(urlencode "$QBT_PASS")"
# qBittorrent starts after gluetun, so the first forwarded port can arrive before
# the WebUI is up: retry for up to five minutes while it is unreachable. A wrong
# password stops at once, since qBittorrent bans an IP after repeated failures.
tries=0
until r=$(wget -q -O - --save-cookies "$jar" --keep-session-cookies --header "Referer: http://127.0.0.1:8080" \
    --post-data "$form" "$base/auth/login" 2>/dev/null); [ "$r" = "Ok." ]; do
  [ -z "$r" ] || { echo "gluetun-qbt-port: qBittorrent rejected the login; listen port NOT set to $port"; exit 1; }
  tries=$((tries + 1))
  [ "$tries" -lt 60 ] || { echo "gluetun-qbt-port: qBittorrent login failed; listen port NOT set to $port"; exit 1; }
  sleep 5
done

wget -q -O /dev/null --load-cookies "$jar" --header "Referer: http://127.0.0.1:8080" \
  --post-data "json={\"listen_port\":$port}" "$base/app/setPreferences"
echo "gluetun-qbt-port: qBittorrent listen port set to $port"
