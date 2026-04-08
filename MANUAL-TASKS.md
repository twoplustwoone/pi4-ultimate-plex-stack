# Manual Tasks

Things that require action on your part — either in a web UI, on the Pi directly,
or as a one-time physical task. None of these can be done through code changes.

---

## Priority 1 — Do these first

### 1. Apply the changes and restart the stack

The code in this repo has been updated. Pull/copy it to your Pi and run:

```bash
# Bring up everything including the new services (autoheal, netdata, portainer)
docker compose --profile advanced up -d

# Confirm autoheal is running
docker ps | grep autoheal

# Confirm Watchtower is now in monitor-only mode (check its logs)
docker logs watchtower | tail -20
```

### 2. Check if you're running from an SD card

Run this on the Pi:

```bash
lsblk
```

If your root filesystem (`/`) is on `mmcblk0`, you are running from an SD card.
**This is a reliability risk.** SD cards fail under sustained write load (logs,
Plex database writes, Tautulli inserts). You need to move to a USB SSD.

How to migrate:
- Buy any USB 3.0 SSD (a 120GB drive is fine for the OS; media lives elsewhere)
- Use the Raspberry Pi Imager to write Raspberry Pi OS to the SSD
- Use `rsync` or the `rpi-clone` tool to copy your existing OS to the SSD
- Boot from USB by holding down the shift key during boot (Pi 4 supports USB boot natively on recent firmware)

### 3. Set your Watchtower notification URL

In your `.env` file on the Pi, fill in `WATCHTOWER_NOTIFICATION_URL` if you haven't.
Without this, you'll never receive the update-available notifications that make
monitor-only mode useful.

For Discord: create a webhook in your server → Server Settings → Integrations →
Webhooks. The format is `discord://WEBHOOK_ID/WEBHOOK_TOKEN`.

Then restart Watchtower: `docker restart watchtower`

Test it: `docker exec watchtower /watchtower --run-once`

---

## Priority 2 — Do these within the first week

### 4. Set up Cloudflare Access for admin services

In the Cloudflare Dashboard → Zero Trust → Access → Applications:

Add an Application for each of these:
- `radarr.twoplustwoone.dev` — Action: Allow, Condition: email = yours
- `sonarr.twoplustwoone.dev` — Action: Allow, Condition: email = yours
- `prowlarr.twoplustwoone.dev` (if exposed) — same
- `portainer.twoplustwoone.dev` (if you add it to the tunnel) — same

Do NOT add Cloudflare Access to Overseerr or Plex — those are intended for
wider access and handle auth themselves.

### 5. Configure Uptime Kuma monitors

Access Uptime Kuma at `http://<pi-ip>:3001` and add monitors for:

| Monitor name | Type | URL |
|---|---|---|
| plex-local | HTTP(s) | `http://localhost:32400/identity` |
| radarr-local | HTTP(s) | `http://radarr:7878` |
| sonarr-local | HTTP(s) | `http://sonarr:8989` |
| overseerr-local | HTTP(s) | `http://overseerr:5055` |
| prowlarr-local | HTTP(s) | `http://prowlarr:9696` |
| gluetun-vpn | TCP | `gluetun:8888` (or use Docker health API) |
| overseerr-external | HTTP(s) | `https://overseerr.twoplustwoone.dev` |
| radarr-external | HTTP(s) | `https://radarr.twoplustwoone.dev` |
| sonarr-external | HTTP(s) | `https://sonarr.twoplustwoone.dev` |
| cloudflared-metrics | HTTP(s) | `http://cloudflared:20241/metrics` |

Configure your Discord (or email) notification channel in Settings → Notifications,
then assign it to each monitor.

### 6. Configure Netdata alerts

After Netdata starts (`http://<pi-ip>:19999`), configure Discord alerting.
Open a shell in the Netdata container:

```bash
docker exec -it netdata bash
```

Edit `/etc/netdata/health_alarm_notify.conf`:

```conf
SEND_DISCORD="YES"
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN"
DEFAULT_RECIPIENT_DISCORD="all"
```

Netdata's built-in alerts will fire for CPU, RAM, disk, and temperature without
any further configuration. Restart the container after editing:
`docker restart netdata`

**Optional — Netdata Cloud**: If you set `NETDATA_CLAIM_TOKEN` in `.env`, Netdata
will connect outbound to Netdata Cloud (free) so you can view dashboards from
anywhere without exposing port 19999 externally. Get a claim token at
`https://app.netdata.cloud` → Connect Nodes → Docker.

### 7. Set up config backups

Decide on a backup destination:
- **Local**: A second USB drive mounted at e.g. `/mnt/backup`
- **Remote NAS**: `user@nas-ip:/backups/pi4-plex` (set up SSH key auth first)
- **Cloud**: Use [rclone](https://rclone.org) to push to Backblaze B2, Google Drive,
  or Cloudflare R2 (rclone is a drop-in replacement for rsync with cloud targets)

Then set `BACKUP_DEST` in your environment and schedule the backup script:

```bash
# Test it first
BACKUP_DEST="user@nas:/backups/pi4-plex" ./scripts/backup-configs.sh

# Add to crontab (runs daily at 3am)
crontab -e
# Add this line:
# 0 3 * * * BACKUP_DEST=user@nas:/backups/pi4-plex /home/pi/pi4-ultimate-plex-stack/scripts/backup-configs.sh >> /var/log/plex-backup.log 2>&1
```

For SSH key-based auth (required for unattended remote backups):
```bash
ssh-keygen -t ed25519 -f ~/.ssh/backup_key -N ""
ssh-copy-id -i ~/.ssh/backup_key.pub user@nas-ip
```

---

## Priority 3 — Quality of life improvements

### 8. Add a heatsink / fan case to the Pi

If the Pi doesn't already have one, this is a physical hardware task. The Pi 4
thermal-throttles at ~80°C. Under sustained Docker workloads, a naked Pi 4 in a
warm room will throttle. Good options: Argon ONE M.2, Flirc, or any passive
heatsink case. After adding it, check Netdata's temperature graph — you should
see a 10–15°C improvement.

### 9. Tune Radarr and Sonarr quality profiles

In the Radarr and Sonarr web UIs:

**Create a new quality profile** (don't modify the defaults):
- Name: "Pi Safe — 1080p H.264"
- Enable: Bluray-1080p, WEBRip-1080p, WEBDL-1080p, HDTV-1080p
- Cutoff: Bluray-1080p

**Add Custom Formats** (Settings → Custom Formats → New):

| Name | Condition | Score |
|---|---|---|
| x264 | Release Title contains `x264` or `H.264` | +200 |
| HEVC / x265 | Release Title contains `x265`, `HEVC`, `H.265` | -10000 |
| AV1 | Release Title contains `AV1` | -10000 |
| Dolby Vision | Release Title contains `DV` or `DoVi` | -10000 |
| HDR | Release Title contains `HDR` | -500 |
| Remux | Quality is Remux-1080p | -500 |

Set the new profile as the default for all movies and shows.

For existing HEVC files in your library: enable "Upgrade Until" in the quality
profile so Radarr/Sonarr will search for and replace HEVC files with H.264 versions.

Alternatively, look into [Recyclarr](https://recyclarr.dev) — a tool that syncs
TRaSH Guides quality profiles into Radarr/Sonarr from a config file. It automates
all of the above and keeps profiles updated as recommendations evolve.

### 10. Add Portainer to Cloudflare Tunnel (optional)

If you want Docker management from outside your network, add a public hostname
in Cloudflare Tunnel for Portainer (`portainer.twoplustwoone.dev` → `http://localhost:9000`)
and protect it with Cloudflare Access (mandatory — do not expose Portainer without Access).

---

## Things that will always require SSH (be honest about it)

A few things genuinely need SSH or direct Pi access and always will:

- **OS-level changes**: updating the Pi OS, changing network config, adjusting
  USB boot settings, mounting/unmounting drives
- **Docker daemon issues**: if Docker itself crashes or the compose socket
  becomes unavailable, you need SSH to recover
- **Crontab management**: editing scheduled tasks
- **Pi boot issues**: if the Pi fails to boot, SSH is unavailable by definition
- **Initial Portainer and Netdata setup**: first-time configuration requires
  accessing their UIs from your local network before they're tunneled

The goal of this setup is not to eliminate SSH — it's to make SSH an occasional
diagnostic tool rather than your primary operations interface.
