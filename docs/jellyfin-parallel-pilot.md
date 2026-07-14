# Jellyfin Parallel Pilot

This pilot runs Jellyfin beside Plex without moving or modifying Plex. Jellyfin is
opt-in through the `jellyfin` Compose profile, uses its own config/cache
directories, and mounts the media library read-only.

## Safety Model

- Plex stays on port `32400`; Jellyfin uses port `8096`.
- Jellyfin stores state under `${BASE_PATH}/jellyfin`.
- Jellyfin sees media at `/media`, backed by `${MEDIA_SHARE}:/media:ro`.
- Do not put Jellyfin behind Gluetun; it should be reachable on LAN and
  Tailscale, not through the torrent VPN.
- Do not expose Jellyfin through Cloudflare Tunnel during this pilot.

## Preflight

Run these on the Pi before starting Jellyfin:

```bash
cd /home/pi/pi4-ultimate-plex-stack

uname -m
awk -F= '/^(BASE_PATH|MEDIA_SHARE|PUID|PGID|SERVER_IP)=/{print}' .env
df -h / "$(awk -F= '$1=="BASE_PATH"{print $2}' .env)"
ss -ltnp | grep ':8096' || true
curl -fsS http://127.0.0.1:32400/identity >/dev/null
command -v tailscale >/dev/null && tailscale status || true
```

Proceed on the Pi only if `uname -m` reports `aarch64` or another ARM64/x86_64
architecture. Jellyfin 10.11+ does not support 32-bit ARM. The expected post
recovery config path is `BASE_PATH=/home/pi/docker-configs`; keep
`MEDIA_SHARE=/mnt/library`.

## Start Jellyfin

```bash
cd /home/pi/pi4-ultimate-plex-stack
set -a
. ./.env
set +a
sudo mkdir -p "$BASE_PATH/jellyfin/config" "$BASE_PATH/jellyfin/cache"
sudo chown -R "$PUID:$PGID" "$BASE_PATH/jellyfin"
docker compose --profile jellyfin up -d jellyfin
docker compose ps jellyfin
curl -fsS http://127.0.0.1:8096/ >/dev/null
docker inspect jellyfin --format '{{range .Mounts}}{{println .Destination .RW}}{{end}}'
```

Confirm the `/media` mount prints `false` for read-only. Open
`http://192.168.1.188:8096` from the LAN and complete the setup wizard.

## Jellyfin Setup

- Create a separate Jellyfin admin account; do not reuse Plex credentials.
- Create a non-admin user for Bree after the admin account is working.
- Add libraries by translating Plex paths from `/share/...` to `/media/...`.
  For example, Plex `/share/media/movies` becomes `/media/media/movies`.
- Before the first full scan, disable trickplay and chapter-image generation in
  the Jellyfin dashboard. Run the first scan overnight or during a quiet window.
- Pause active qBittorrent downloads and Sonarr/Radarr grabs during the first scan
  so the Pi is not judged while it is under scan load.

## Tailscale Remote Test

Install Tailscale on the Pi only if it is not already installed:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4
```

In the Tailscale admin console, disable key expiry for the Pi and for the test
phones. On Bree's phone, add the Jellyfin server as:

```text
http://<pi-tailscale-ip>:8096
```

For the remote test, turn Wi-Fi off on Bree's phone and play a title over
cellular. This tests connection speed and stability. Do not treat Pi transcoding
performance as a Jellyfin verdict; an N100 with Quick Sync is the performance
test target.

## Pass Criteria

- Shield playback on LAN feels usable for normal nightly viewing.
- A 1080p H.264 file, a 4K HEVC direct-play file, and subtitles all behave well
  on the Shield.
- Bree can connect over cellular through Tailscale, start playback, and remain
  connected.
- Plex remains healthy and unchanged throughout the pilot.
- The Pi returns to normal idle behavior after the library scan settles.

## Rollback

```bash
cd /home/pi/pi4-ultimate-plex-stack
docker compose --profile jellyfin stop jellyfin
docker compose --profile jellyfin rm -f jellyfin
```

Delete `${BASE_PATH}/jellyfin` only after deciding the pilot is over. Tailscale
can stay installed; it is useful independently of Jellyfin.
