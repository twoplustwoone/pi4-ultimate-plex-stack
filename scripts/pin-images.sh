#!/usr/bin/env bash
set -euo pipefail

env_file="${1:-.env}"
out_file="${2:-$env_file}"

if [[ ! -f "$env_file" ]]; then
  echo "Env file not found: $env_file" >&2
  exit 1
fi

image_vars=(
  PLEX_IMAGE
  RADARR_IMAGE
  SONARR_IMAGE
  OVERSEERR_IMAGE
  QBITTORRENT_IMAGE
  GLUETUN_IMAGE
  UNPACKERR_IMAGE
  PROWLARR_IMAGE
  TAUTULLI_IMAGE
  BAZARR_IMAGE
  CROSS_SEED_IMAGE
  AUTOBRR_IMAGE
  DOZZLE_IMAGE
  FLARESOLVERR_IMAGE
)

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

tmp_keys="$(mktemp)"
tmp_out="$(mktemp)"
cleanup() {
  rm -f "$tmp_keys" "$tmp_out"
}
trap cleanup EXIT

for var in "${image_vars[@]}"; do
  val="${!var:-}"
  if [[ -z "$val" ]]; then
    echo "Missing $var in $env_file" >&2
    continue
  fi

  if [[ "$val" == *@sha256:* ]]; then
    echo "$var=$val" >> "$tmp_keys"
    continue
  fi

  echo "Pulling $val..." >&2
  docker pull "$val" >/dev/null

  digest_ref="$(docker image inspect --format '{{index .RepoDigests 0}}' "$val")"
  if [[ -z "$digest_ref" || "$digest_ref" == "<no value>" ]]; then
    echo "Failed to resolve digest for $val" >&2
    exit 1
  fi

  echo "$var=$digest_ref" >> "$tmp_keys"
done

awk -v keys_file="$tmp_keys" '
  BEGIN {
    while ((getline line < keys_file) > 0) {
      split(line, a, "=");
      key=a[1];
      value=substr(line, length(key)+2);
      map[key]=value;
    }
  }
  /^[A-Za-z_][A-Za-z0-9_]*=/ {
    split($0, a, "=");
    key=a[1];
    if (key in map) {
      print key "=" map[key];
      next;
    }
  }
  { print }
' "$env_file" > "$tmp_out"

mv "$tmp_out" "$out_file"

echo "Wrote $out_file" >&2
