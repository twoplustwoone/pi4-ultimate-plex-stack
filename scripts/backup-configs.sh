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
  # portainer intentionally excluded — files owned by root, not readable by pi.
  # Portainer is trivially reconfigurable; its DB isn't worth the sudo complexity.
)

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')] backup-configs:"

echo "$LOG_PREFIX Starting backup to ${R2_REMOTE}:${R2_BUCKET}"

# Containers that must be stopped for a clean database snapshot.
# These write to SQLite WAL files continuously and can't be safely copied live.
STOP_FOR_BACKUP=(plex uptime-kuma)
declare -A WAS_RUNNING

for container in "${STOP_FOR_BACKUP[@]}"; do
  if docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null | grep -q true; then
    WAS_RUNNING[$container]=true
    echo "$LOG_PREFIX Stopping $container for clean snapshot..."
    docker stop "$container" >/dev/null
  else
    WAS_RUNNING[$container]=false
  fi
done

# Track whether any service had errors (don't abort the whole run on one failure)
BACKUP_ERRORS=0

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
    --exclude "screenshots/**"  # Uptime Kuma monitoring screenshots — regenerated automatically
    --exclude "bin/**"          # Portainer application binaries — downloaded on startup
    --exclude "chisel/**"       # Portainer chisel binary — downloaded on startup
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

  if ! rclone sync "$src/" "${R2_REMOTE}:${R2_BUCKET}/${service}/" \
    --transfers=2 \
    --checksum \
    --log-level INFO \
    "${extra_args[@]}"; then
    echo "$LOG_PREFIX WARNING: $service completed with rclone errors (see above)"
    BACKUP_ERRORS=$((BACKUP_ERRORS + 1))
  fi
done

# --- Restart any containers that were stopped ---
for container in "${STOP_FOR_BACKUP[@]}"; do
  if [[ "${WAS_RUNNING[$container]}" == "true" ]]; then
    echo "$LOG_PREFIX Restarting $container..."
    docker start "$container" >/dev/null
  fi
done

if [[ $BACKUP_ERRORS -gt 0 ]]; then
  echo "$LOG_PREFIX Backup finished with $BACKUP_ERRORS service(s) having errors."
  exit 1
fi

echo "$LOG_PREFIX Backup complete."
