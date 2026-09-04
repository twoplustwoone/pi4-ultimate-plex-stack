#!/usr/bin/env bash
# prep-new-disk.sh — partition, format and mount the replacement media disk.
# Destructive by design. Refuses to run against anything but the expected drive.
set -euo pipefail

TARGET_SERIAL="WD-1F0AK8EU"
DEV="/dev/sdb"
PART="${DEV}1"
LABEL="media"
MOUNTPOINT="/mnt/library2"
OWNER="1000:1000"

die() { echo "REFUSING: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "must run as root (use sudo)"
[[ -b "$DEV" ]]   || die "$DEV is not a block device"

serial=$(lsblk -ndo SERIAL "$DEV" | tr -d '[:space:]')
model=$(lsblk -ndo MODEL  "$DEV" | sed 's/[[:space:]]*$//')
size=$(lsblk -ndo SIZE    "$DEV" | tr -d '[:space:]')

# Guard 1: must be the exact drive we baselined.
[[ "$serial" == "$TARGET_SERIAL" ]] || die "serial is '$serial', expected '$TARGET_SERIAL'"

# Guard 2: never touch whatever currently backs the live library.
live=$(findmnt -no SOURCE /mnt/library 2>/dev/null || true)
[[ -n "$live" && ( "$live" == "$DEV" || "$live" == "$PART" ) ]] && die "$DEV backs the live /mnt/library"

# Guard 3: nothing on this device may be mounted.
if findmnt -S "$DEV" >/dev/null 2>&1 || findmnt -S "$PART" >/dev/null 2>&1; then
  die "$DEV has a mounted partition"
fi

echo "Target : $DEV"
echo "Model  : $model"
echo "Serial : $serial"
echo "Size   : $size"
echo "Live library is on: ${live:-<none>}  (will not be touched)"
echo
echo "This ERASES everything on $DEV."
read -rp "Type ERASE to continue: " ans
[[ "$ans" == "ERASE" ]] || { echo "Aborted."; exit 1; }

echo "==> wiping existing signatures"
wipefs -a "$DEV"

echo "==> creating GPT + aligned partition"
parted -s "$DEV" mklabel gpt
parted -s -a optimal "$DEV" mkpart primary ext4 0% 100%
partprobe "$DEV"
udevadm settle || true
sleep 3

echo "==> creating ext4 (label=$LABEL, 1% reserved)"
# -m 1 instead of the 5% default reclaims ~320 GB on an 8 TB disk.
mkfs.ext4 -q -m 1 -L "$LABEL" "$PART"

echo "==> mounting at $MOUNTPOINT"
mkdir -p "$MOUNTPOINT"
mount -o defaults,noatime "$PART" "$MOUNTPOINT"
chown "$OWNER" "$MOUNTPOINT"

uuid=$(blkid -s UUID -o value "$PART")

# Persist the staging mount so a reboot mid-copy doesn't lose it.
# nofail: a missing disk must never block boot.
if ! grep -q "$uuid" /etc/fstab; then
  cp /etc/fstab /etc/fstab.bak-$(date +%Y%m%d-%H%M%S)
  printf 'UUID=%s  %s  ext4  defaults,noatime,nofail  0  2\n' "$uuid" "$MOUNTPOINT" >> /etc/fstab
  echo "==> added fstab entry (backup saved)"
fi

echo
echo "DONE."
echo "  UUID       : $uuid"
echo "  Mountpoint : $MOUNTPOINT"
df -h "$MOUNTPOINT" | tail -1
