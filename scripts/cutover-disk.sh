#!/usr/bin/env bash
# cutover-disk.sh — make the new disk the live /mnt/library.
# The old disk is NOT erased; it is remounted read-only at /mnt/library-old
# so it stays available as a cold fallback.
set -euo pipefail

NEW_UUID="3af380cc-3f62-4ef3-b6cf-5dcab2c67469"   # WD80EDAZ (8TB, label=media)
OLD_UUID=""                                        # resolved below
LIVE="/mnt/library"
STAGING="/mnt/library2"
OLDMNT="/mnt/library-old"

die() { echo "REFUSING: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "must run as root (use sudo)"

# The new disk must currently be the staging mount.
src=$(findmnt -no SOURCE "$STAGING" 2>/dev/null || true)
[[ -n "$src" ]] || die "$STAGING is not mounted"
[[ "$(blkid -s UUID -o value "$src")" == "$NEW_UUID" ]] || die "$STAGING is not the expected new disk"

OLD_SRC=$(findmnt -no SOURCE "$LIVE" 2>/dev/null || true)
[[ -n "$OLD_SRC" ]] || die "$LIVE is not mounted; nothing to cut over from"
OLD_UUID=$(blkid -s UUID -o value "$OLD_SRC")
[[ "$OLD_UUID" != "$NEW_UUID" ]] || die "$LIVE already appears to be the new disk"

echo "Old (live)  : $OLD_SRC  UUID=$OLD_UUID"
echo "New (staged): $src  UUID=$NEW_UUID"
echo
echo "After this: new disk -> $LIVE, old disk -> $OLDMNT (read-only)."
read -rp "Type CUTOVER to continue: " ans
[[ "$ans" == "CUTOVER" ]] || { echo "Aborted."; exit 1; }

echo "==> stopping containers that hold the mount open"
STOPPED=""
for c in plex sonarr radarr overseerr tautulli prowlarr bazarr autobrr \
         cross-seed unpackerr qbittorrent gluetun; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" == "true" ]]; then
    docker stop "$c" >/dev/null && STOPPED="$STOPPED $c"
    echo "    stopped $c"
  fi
done

echo "==> unmounting both filesystems"
umount "$STAGING"
umount "$LIVE" || { echo "  $LIVE busy; processes holding it:"; fuser -vm "$LIVE" || true; die "could not unmount $LIVE"; }

echo "==> rewriting /etc/fstab"
cp /etc/fstab "/etc/fstab.bak-cutover-$(date +%Y%m%d-%H%M%S)"
# drop any existing lines for these UUIDs or mountpoints
grep -vE "($NEW_UUID|$OLD_UUID|[[:space:]]$LIVE[[:space:]]|[[:space:]]$STAGING[[:space:]]|[[:space:]]$OLDMNT[[:space:]])" \
  /etc/fstab > /etc/fstab.new
{
  printf 'UUID=%s  %s  ext4  defaults,noatime,nofail  0  2\n' "$NEW_UUID" "$LIVE"
  printf 'UUID=%s  %s  ext4  ro,nofail,noauto  0  0\n'        "$OLD_UUID" "$OLDMNT"
} >> /etc/fstab.new
mv /etc/fstab.new /etc/fstab

echo "==> mounting new disk at $LIVE"
mkdir -p "$LIVE" "$OLDMNT"
mount "$LIVE"

echo "==> mounting old disk read-only at $OLDMNT (fallback)"
mount "$OLDMNT" || echo "  (old disk not mounted; mount $OLDMNT manually if needed)"

echo "==> sanity check"
findmnt "$LIVE"
df -h "$LIVE" | tail -1
for d in movies tv downloads; do
  printf "    %-10s %s entries\n" "$d" "$(find "$LIVE/$d" -maxdepth 1 2>/dev/null | wc -l)"
done

echo
echo "DONE. Containers stopped by this script:$STOPPED"
echo "Start them again once you've eyeballed the above, e.g.:"
echo "  docker start plex sonarr radarr overseerr tautulli prowlarr"
