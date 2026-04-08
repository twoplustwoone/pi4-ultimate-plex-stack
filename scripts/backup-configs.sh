#!/usr/bin/env bash
# backup-configs.sh — backs up all Docker config volumes to Cloudflare R2.
#
# Prerequisites:
#   - rclone installed and configured with an "r2" remote pointing to Cloudflare R2
#     (run `rclone config` to set up — see MANUAL-TASKS.md for full instructions)
#   - BASE_PATH set in .env (or exported in the environment)
#
# Usage:
#   ./scripts/backup-configs.sh
#
# Scheduling (add to crontab with: crontab -e):
#   0 3 * * * /home/pi/pi4-ultimate-plex-stack/scripts/backup-configs.sh >> /var/log/plex-backup.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Source .env if present (picks up BASE_PATH)
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

BASE="${BASE_PATH:?BASE_PATH must be set in .env or the environment}"
R2_BUCKET="${R2_BUCKET:-pi4-plex-backups}"
R2_REMOTE="${R2_REMOTE:-r2}"

# Services to back up — matches the config volume paths in docker-compose.yml
SERVICES=(
  plex/config
  radarr/config
  sonarr/config
  prowlarr/config
  overseerr/config
  tautulli/config
  bazarr/config
  uptime-kuma
  autobrr/config
  cloudflared
  portainer
)

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')] backup-configs:"

echo "$LOG_PREFIX Starting backup to ${R2_REMOTE}:${R2_BUCKET}"

# --- Plex: stop briefly for a clean database snapshot ---
PLEX_RUNNING=false
if docker inspect --format '{{.State.Running}}' plex 2>/dev/null | grep -q true; then
  PLEX_RUNNING=true
  echo "$LOG_PREFIX Stopping plex for clean database snapshot..."
  docker stop plex >/dev/null
fi

# --- Sync all config directories ---
for service in "${SERVICES[@]}"; do
  src="$BASE/$service"
  if [[ ! -d "$src" ]]; then
    echo "$LOG_PREFIX Skipping $service (directory not found: $src)"
    continue
  fi
  echo "$LOG_PREFIX Syncing $service..."
  rclone sync "$src/" "${R2_REMOTE}:${R2_BUCKET}/${service}/" \
    --transfers=2 \
    --checksum \
    --log-level INFO
done

# --- Restart Plex if it was stopped ---
if [[ "$PLEX_RUNNING" == "true" ]]; then
  echo "$LOG_PREFIX Restarting plex..."
  docker start plex >/dev/null
fi

echo "$LOG_PREFIX Backup complete."
