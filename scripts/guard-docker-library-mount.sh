#!/usr/bin/env bash
# Ensure Docker cannot start containers before the live media library is mounted.
# This writes only a Docker systemd drop-in; it does not restart Docker or alter fstab.
set -euo pipefail

MOUNTPOINT="/mnt/library"
EXPECTED_UUID="3af380cc-3f62-4ef3-b6cf-5dcab2c67469"
DROPIN_DIR="/etc/systemd/system/docker.service.d"
DROPIN="$DROPIN_DIR/10-requires-library.conf"

die() { echo "REFUSING: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "must run as root (use sudo)"
findmnt -rn -T "$MOUNTPOINT" >/dev/null || die "$MOUNTPOINT is not mounted"

mounted_uuid=$(findmnt -rn -T "$MOUNTPOINT" -o UUID)
mounted_type=$(findmnt -rn -T "$MOUNTPOINT" -o FSTYPE)
[[ "$mounted_uuid" == "$EXPECTED_UUID" ]] || die "$MOUNTPOINT UUID is '$mounted_uuid', expected '$EXPECTED_UUID'"
[[ "$mounted_type" == "ext4" ]] || die "$MOUNTPOINT filesystem is '$mounted_type', expected ext4"

for directory in movies tv downloads; do
  [[ -d "$MOUNTPOINT/$directory" ]] || die "$MOUNTPOINT/$directory is missing"
done

fstab_entry=$(grep -E "^[^#].*[[:space:]]${MOUNTPOINT//\//\\/}[[:space:]]" /etc/fstab || true)
[[ "$fstab_entry" == *"UUID=$EXPECTED_UUID"* ]] || die "/etc/fstab does not map UUID $EXPECTED_UUID to $MOUNTPOINT"

echo "Verified live library: $MOUNTPOINT (UUID $mounted_uuid, $mounted_type)"
echo
echo "This will install a Docker systemd drop-in requiring $MOUNTPOINT before Docker starts."
echo "If the media disk is unavailable on a future boot, Docker will wait/fail rather than"
echo "starting this stack against the empty mountpoint. Docker will NOT be restarted now."
read -rp "Type INSTALL to continue: " answer
[[ "$answer" == "INSTALL" ]] || { echo "Aborted."; exit 0; }

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<'EOF'
[Unit]
# Prevent bind-mounted media paths from resolving to the empty pre-mount directory.
RequiresMountsFor=/mnt/library
EOF

install -d -m 0755 "$DROPIN_DIR"
if [[ -f "$DROPIN" ]] && cmp -s "$tmp" "$DROPIN"; then
  echo "Drop-in already has the expected contents."
else
  if [[ -f "$DROPIN" ]]; then
    backup="$DROPIN.bak-$(date +%Y%m%d-%H%M%S)"
    cp -a "$DROPIN" "$backup"
    echo "Backed up existing drop-in to $backup"
  fi
  install -m 0644 "$tmp" "$DROPIN"
  echo "Installed $DROPIN"
fi

systemctl daemon-reload
systemd-analyze verify docker.service

echo
echo "DONE. Docker was not restarted. The dependency will take effect on its next start or reboot."
