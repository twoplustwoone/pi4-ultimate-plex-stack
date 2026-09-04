#!/usr/bin/env bash
# post-cutover-tuning.sh — lift Pi-era constraints that no longer apply on yggdrasil.
# Run AFTER cutover-disk.sh, once playback from the new disk is confirmed.
# Safe to re-run; every change is idempotent.
set -euo pipefail

PLEX_PREFS="/home/pi/docker-configs/plex/config/Library/Application Support/Plex Media Server/Preferences.xml"

echo "=== 1. Plex hardware transcoding (Intel Quick Sync) ==="
# The i5-10500T has UHD 630; /dev/dri is already mapped into the container.
# Plex must be stopped or it rewrites Preferences.xml on exit.
if [[ "$(docker inspect -f '{{.State.Running}}' plex 2>/dev/null)" == "true" ]]; then
  echo "  stopping plex"; docker stop plex >/dev/null
  RESTART_PLEX=1
else
  RESTART_PLEX=0
fi

if grep -q 'HardwareAcceleratedCodecs=' "$PLEX_PREFS"; then
  sed -i 's/HardwareAcceleratedCodecs="[^"]*"/HardwareAcceleratedCodecs="1"/' "$PLEX_PREFS"
  echo "  updated existing HardwareAcceleratedCodecs -> 1"
else
  sed -i 's|/>$| HardwareAcceleratedCodecs="1"/>|' "$PLEX_PREFS"
  echo "  added HardwareAcceleratedCodecs=\"1\""
fi
grep -o 'HardwareAcceleratedCodecs="[^"]*"' "$PLEX_PREFS" | sed 's/^/  now: /'

[[ "$RESTART_PLEX" == "1" ]] && { docker start plex >/dev/null; echo "  restarted plex"; }

echo
echo "=== 2. Stop penalising HEVC in the Pi-era profiles ==="
# UHD 630 hardware-decodes H.265/HEVC 8-bit and 10-bit, so the -50 penalty is obsolete.
# AV1 stays at -10000 on purpose: UHD 630 has NO AV1 hardware decode.
for svc in radarr:7878 sonarr:8989; do
  n=${svc%%:*}; p=${svc##*:}
  key=$(docker exec "$n" sed -n 's#.*<ApiKey>\(.*\)</ApiKey>.*#\1#p' /config/config.xml)
  for pid in 9 10; do
    body=$(curl -s -m 20 -H "X-Api-Key: $key" "http://127.0.0.1:$p/api/v3/qualityprofile/$pid") || continue
    echo "$body" | grep -q '"name"' || continue
    updated=$(echo "$body" | python3 -c '
import sys, json
pr = json.load(sys.stdin)
changed = False
for f in pr.get("formatItems", []):
    if f.get("name") in ("x265/HEVC",) and f.get("score", 0) < 0:
        f["score"] = 0
        changed = True
pr["_changed"] = changed
print(json.dumps(pr))
')
    if echo "$updated" | python3 -c 'import sys,json; sys.exit(0 if json.load(sys.stdin).get("_changed") else 1)'; then
      echo "$updated" | python3 -c 'import sys,json; d=json.load(sys.stdin); d.pop("_changed",None); print(json.dumps(d))' > /tmp/qp.json
      code=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -X PUT -H "X-Api-Key: $key" \
        -H "Content-Type: application/json" -d @/tmp/qp.json \
        "http://127.0.0.1:$p/api/v3/qualityprofile/$pid")
      rm -f /tmp/qp.json
      echo "  $n profile $pid: HEVC penalty removed (HTTP $code)"
    else
      echo "  $n profile $pid: no change needed"
    fi
  done
done

echo
echo "DONE."
echo "Deliberately NOT changed (your call, they depend on your TV):"
echo "  AV1          -10000   keep: UHD 630 has no AV1 hardware decode"
echo "  Dolby Vision -10000   depends on whether your TV handles DV profile 5"
echo "  HDR            -500   relax only if your display is HDR"
echo "  Remux          -500   relax if you want bigger/better sources now space allows"
