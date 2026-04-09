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

  # -------------------------------------------------------------------------
  # Global excludes — regenerable/ephemeral data across ALL services.
  # These are never needed for a restore and should never be uploaded.
  # -------------------------------------------------------------------------
  extra_args=(
    # Log directories — different apps use different casings/names
    --exclude "logs/**"         # *arr, overseerr, autobrr, tautulli (e.g. radarr.0.txt)
    --exclude "log/**"          # Bazarr (singular)
    --exclude "Logs/**"         # Plex-style uppercase

    # Log databases — *arr stores logs in a separate SQLite DB, not needed for restore
    --exclude "logs.db"
    --exclude "logs.db-shm"
    --exclude "logs.db-wal"

    # Generic log files
    --exclude "*.log"
    --exclude "*.log.*"

    # Cached artwork — Radarr/Sonarr/Prowlarr re-download these on startup
    --exclude "MediaCover/**"

    # Runtime ephemera
    --exclude "*.pid"           # Process ID files — meaningless after a restart

    # Regenerable caches
    --exclude "cache/**"        # Tautulli thumbnail cache (lowercase)
    --exclude "Cache/**"        # Plex-style uppercase (also caught in Plex block below)
    --exclude "ecs/**"          # Radarr/Sonarr entity-component-system cache
    --exclude "updatedata/**"   # *arr update-checker cache
    --exclude "Definitions/**"  # Prowlarr indexer definitions — auto-downloaded from GitHub on startup
  )

  # -------------------------------------------------------------------------
  # Plex-specific excludes — large regenerable directories nested under
  # Library/Application Support/Plex Media Server/
  # -------------------------------------------------------------------------
  if [[ "$service" == "plex/config" ]]; then
    extra_args+=(
      --exclude "Codecs/**"                    # Platform binaries auto-downloaded by Plex on startup
      --exclude "Crash Reports/**"             # Not useful for restore
      --exclude "Media/**"                     # Analysis data: chapter thumbnails, GoP indexes, video thumbnails
      --exclude "Plug-in Support/Caches/**"    # HTTP caches for agents (fanarttv, imdb, themoviedb, etc.) — rebuilt on startup
    )
  fi

  rclone sync "$src/" "${R2_REMOTE}:${R2_BUCKET}/${service}/" \
    --transfers=2 \
    --checksum \
    --log-level INFO \
    "${extra_args[@]}"
done

# --- Restart Plex if it was stopped ---
if [[ "$PLEX_RUNNING" == "true" ]]; then
  echo "$LOG_PREFIX Restarting plex..."
  docker start plex >/dev/null
fi

echo "$LOG_PREFIX Backup complete."
