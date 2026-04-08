#!/usr/bin/env bash
# backup-configs.sh — backs up all Docker config volumes to a remote destination.
#
# Usage:
#   ./scripts/backup-configs.sh [destination]
#
# destination defaults to BACKUP_DEST environment variable, or prompts if unset.
#
# Examples:
#   BACKUP_DEST="user@nas:/backups/pi4-plex" ./scripts/backup-configs.sh
#   ./scripts/backup-configs.sh user@nas:/backups/pi4-plex
#   ./scripts/backup-configs.sh /mnt/backup-drive/pi4-plex
#
# Prerequisites:
#   - rsync installed on Pi (sudo apt install rsync)
#   - SSH key-based auth configured if using a remote destination
#   - BASE_PATH set in environment or sourced from .env
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

# Resolve destination
DEST="${1:-${BACKUP_DEST:-}}"
if [[ -z "$DEST" ]]; then
  echo "Error: no backup destination specified." >&2
  echo "Set BACKUP_DEST in your environment or pass it as the first argument." >&2
  echo "Example: $0 user@nas:/backups/pi4-plex" >&2
  exit 1
fi

BASE="${BASE_PATH:-/home/pi/ups-configs}"

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

echo "$LOG_PREFIX Starting backup to $DEST"

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
  rsync -az --delete \
    --exclude='*.log' \
    --exclude='Logs/' \
    --exclude='logs/' \
    --exclude='cache/' \
    --exclude='Cache/' \
    "$src/" \
    "$DEST/$service/"
done

# --- Restart Plex if it was stopped ---
if [[ "$PLEX_RUNNING" == "true" ]]; then
  echo "$LOG_PREFIX Restarting plex..."
  docker start plex >/dev/null
fi

echo "$LOG_PREFIX Backup complete."
