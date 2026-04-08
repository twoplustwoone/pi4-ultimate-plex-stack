# Pi4 Plex Stack — Ops & Reliability Guide

A practical, opinionated guide for making this stack more reliable, observable,
and easier to operate. Written against the actual `docker-compose.yml` in this
repo, not a generic template.

---

## Table of Contents

1. [Honest assessment of the current setup](#1-honest-assessment)
2. [The critical gap most people miss: autoheal](#2-the-autoheal-gap)
3. [Monitoring stack recommendation](#3-monitoring-stack)
4. [Watchtower: the hidden risk](#4-watchtower-risk)
5. [Security: what to expose and what not to](#5-cloudflare-access)
6. [Backup and recovery strategy](#6-backup-and-recovery)
7. [Radarr / Sonarr quality profiles for Pi](#7-quality-profiles)
8. [Hardware assessment and upgrade path](#8-hardware)
9. [Start here: priority checklist](#9-start-here-checklist)

---

## 1. Honest Assessment

### What you already have (and it's actually good)

You've built a more complete stack than most people running Pi-based Plex setups.
The things you already do correctly:

- Every service has a Docker healthcheck. This is more than most stacks have.
- `restart: unless-stopped` is on everything, so crash-loops self-recover.
- `depends_on: condition: service_healthy` means downstream services wait for
  upstreams (e.g. qBittorrent won't start until Gluetun's VPN tunnel is up).
- Log rotation is configured (`max-size: 10m, max-file: 3`) which prevents disk
  exhaustion from runaway logs.
- Uptime Kuma is in the stack for HTTP endpoint monitoring.
- Dozzle is in the stack for log browsing without SSH.
- Tautulli is in the stack for Plex-specific analytics.
- Cloudflare Tunnel means zero open ports on your router.
- Gluetun wraps qBittorrent properly — if VPN drops, torrenting stops.
- The image pinning script (`scripts/pin-images.sh`) exists for stability.

### What's actually missing or fragile

There are four meaningful gaps, listed in order of practical impact:

**Gap 1: Unhealthy containers are detected but not healed.**
Docker's healthcheck marks a container `unhealthy`, but `restart: unless-stopped`
only restarts containers that *exit*. An unhealthy container that stays running
(e.g. Plex is up but its HTTP endpoint returns 503) will sit there marked
unhealthy indefinitely — no automatic restart, no alert fired. This is the most
common failure mode that feels like "it was working and then just… stopped."

**Gap 2: No system-level metrics with history.**
Uptime Kuma tells you "up or down" for each HTTP endpoint, right now. It does
not tell you: CPU was 97% for three hours before Plex died, your disk is 89%
full, the Pi throttled to 600MHz because it hit 85°C, or RAM pressure has been
building for a week. Without this, you're always debugging blind.

**Gap 3: Watchtower is updating everything automatically.**
With `WATCHTOWER_INTERVAL=86400` and `WATCHTOWER_NOTIFICATION_REPORT_ONLY=true`,
Watchtower is currently set to silently update all containers every 24 hours and
only notify you after the fact. This is how you wake up to a broken Radarr because
an upstream image introduced a config migration that needed manual intervention.

**Gap 4: No config volume backups.**
If the SD card dies (or USB drive), or if a bad Watchtower update corrupts a
config volume, you lose all your Radarr/Sonarr metadata, indexer configs, Plex
database, and Tautulli history. There is nothing in this stack that backs any
of it up.

---

## 2. The Autoheal Gap

This is the single highest-value fix you can make right now, and almost nobody
knows it exists until they've been bitten.

### Why the gap exists

Docker's health system has two halves that don't talk to each other:

- Healthchecks mark containers as `healthy` or `unhealthy`
- Restart policies (`unless-stopped`, `on-failure`) only trigger when a container *exits*

A container that is `unhealthy` but still running — which is the most common
real-world failure for HTTP-based services — will never be restarted by Docker
itself. It just sits there. Uptime Kuma will alert you (if configured), but
nothing self-heals it.

### The fix: `docker-autoheal`

Add this service to your compose file. It watches the Docker socket for
unhealthy containers and restarts them automatically.

```yaml
autoheal:
  image: willfarrell/autoheal:latest
  container_name: autoheal
  restart: unless-stopped
  environment:
    - AUTOHEAL_CONTAINER_LABEL=all        # watch all containers
    - AUTOHEAL_INTERVAL=30                # check every 30s
    - AUTOHEAL_START_PERIOD=0             # no grace period (healthchecks handle start)
    - AUTOHEAL_DEFAULT_STOP_TIMEOUT=10    # seconds before force-kill on restart
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
  logging: *default-logging
```

You can also restrict it to opt-in containers by labeling them:

```yaml
environment:
  - AUTOHEAL_CONTAINER_LABEL=autoheal.enable
```

And then adding `labels: ["autoheal.enable=true"]` to each service you want
covered. This is safer and gives you per-service control.

### Recommended autoheal targets

Enable autoheal on: plex, radarr, sonarr, prowlarr, overseerr, gluetun,
cloudflared, uptime-kuma.

Skip autoheal on: watchtower (it manages its own lifecycle), cross-seed (let it
fail cleanly), qbittorrent (if gluetun goes down, you want qbittorrent to stop,
not restart into a no-VPN state — the depends_on already handles this).

---

## 3. Monitoring Stack

### The gap: system metrics with history

You can see "is the service up" via Uptime Kuma and "what did the logs say" via
Dozzle. What you cannot see: CPU trends, RAM pressure, disk fill rate, network
throughput, Pi temperature, and per-container resource usage over time.

### Recommended addition: Netdata

Netdata is the right choice for a Pi. It is:

- Lightweight enough to run on Pi 4 (50–150MB RAM, ~2–5% CPU idle overhead)
- Ready out of the box — zero configuration required for basic system + Docker monitoring
- Has built-in alerts for CPU, RAM, disk, network, and temperature
- Has Docker container CPU/RAM/network per-container dashboards
- Has a 1-hour lookback window by default (configurable)
- Can push alerts to Discord, Slack, email, Telegram, ntfy, and more

Add to your compose file:

```yaml
netdata:
  image: netdata/netdata:stable
  container_name: netdata
  pid: host
  network_mode: host
  restart: unless-stopped
  cap_add:
    - SYS_PTRACE
    - SYS_ADMIN
  security_opt:
    - apparmor:unconfined
  volumes:
    - netdataconfig:/etc/netdata
    - netdatalib:/var/lib/netdata
    - netdatacache:/var/cache/netdata
    - /etc/passwd:/host/etc/passwd:ro
    - /etc/group:/host/etc/group:ro
    - /etc/localtime:/etc/localtime:ro
    - /proc:/host/proc:ro
    - /sys:/host/sys:ro
    - /etc/os-release:/host/etc/os-release:ro
    - /var/run/docker.sock:/var/run/docker.sock:ro
  environment:
    - NETDATA_CLAIM_TOKEN=${NETDATA_CLAIM_TOKEN:-}   # optional: connect to Netdata Cloud
    - NETDATA_CLAIM_URL=https://app.netdata.cloud
    - DOCKER_HOST=unix:///var/run/docker.sock
  logging: *default-logging
```

And add to your volumes section at the bottom:

```yaml
volumes:
  netdataconfig:
  netdatalib:
  netdatacache:
```

Netdata runs on port 19999 by default (accessible at `http://<pi-ip>:19999`).
Because it uses `network_mode: host`, it doesn't need a port mapping.

### Alerts to configure in Netdata

Out of the box, Netdata will alert on:

- CPU usage > 85% for extended periods
- RAM > 90% used
- Disk space > 85% used (this one is critical for Pi — watch it closely)
- Disk I/O wait high (sign your storage is the bottleneck)
- Temperature > 75°C (`/sys/class/thermal/thermal_zone0/temp`)

Configure alert notifications by editing `/etc/netdata/health_alarm_notify.conf`
inside the container, or mount a custom config. For Discord, set:

```conf
SEND_DISCORD="YES"
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN"
DEFAULT_RECIPIENT_DISCORD="all"
```

### Optional: Netdata Cloud (free tier)

Netdata Cloud lets you view your Pi's dashboard from anywhere without exposing
port 19999 externally. It's free for personal use. Set `NETDATA_CLAIM_TOKEN`
in your `.env` file and the agent will connect outbound to Netdata Cloud —
no inbound ports needed. This is a cleaner alternative to tunneling port 19999
through Cloudflare.

### What you do NOT need (for this setup)

**Prometheus + Grafana**: Technically superior for long-term metrics retention
and custom dashboards, but significantly heavier. Prometheus + Grafana + cAdvisor
will consume 300–600MB of RAM on the Pi and require ongoing configuration. For
a single-node homelab, Netdata gives you 80% of the value at 15% of the cost.
Revisit Prometheus/Grafana if you upgrade to a more capable machine.

**Loki + Promtail**: A proper log aggregation system (like the Grafana Loki stack)
would be ideal for centralized log search and correlation, but it's too heavy for
a Pi. Dozzle + good log rotation (which you already have) is the right call here.
The upgrade path is: if you ever move to a more capable machine, add Loki then.

### Portainer for Docker management without SSH

Portainer gives you a web UI for managing containers, viewing resource usage,
pulling images, restarting services, and browsing logs — all without SSH.
It's lightweight (~50MB RAM) and well worth running.

```yaml
portainer:
  image: portainer/portainer-ce:latest
  container_name: portainer
  restart: unless-stopped
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
    - ${BASE_PATH}/portainer:/data
  ports:
    - "9000:9000"
  logging: *default-logging
```

Expose Portainer through Cloudflare Tunnel (with Cloudflare Access protecting
it — see Section 5) and you have a full Docker management UI accessible from
anywhere, no SSH required.

---

## 4. Watchtower: The Hidden Risk

### The current behavior

Your Watchtower is currently configured to:

```yaml
- WATCHTOWER_INTERVAL=86400          # check every 24 hours
- WATCHTOWER_NOTIFICATION_REPORT_ONLY=true  # send update report, do NOT notify on failure
```

Despite the "REPORT_ONLY" naming, `WATCHTOWER_NOTIFICATION_REPORT_ONLY=true`
means Watchtower only sends a notification *when updates happen* (the "report"),
rather than notifying on every run. It is **still auto-updating containers**.

### The real risk

Auto-updating a production media stack causes real problems:

- Plex updates sometimes include database migrations that need time to complete.
  If the container is killed mid-migration, the database can corrupt.
- Radarr/Sonarr have broken their config schema on updates before.
- LinuxServer.io images occasionally have a "bad" release window that gets fixed
  within hours — but Watchtower will grab it.
- You have no rollback path after an auto-update (the old image is cleaned up by
  `WATCHTOWER_CLEANUP=true`).

### What to do

Switch Watchtower to **notify-only mode** and do updates intentionally:

```yaml
environment:
  # ... keep your existing env vars ...
  - WATCHTOWER_MONITOR_ONLY=true    # ADD THIS — never actually update, just notify
```

Now Watchtower will tell you when updates are available via your notification
channel, and you decide when to apply them by running:

```bash
docker compose pull && docker compose up -d
```

If you want some containers to stay on auto-update (e.g. Cloudflared, which
rarely breaks and benefits from security patches), use labels to opt specific
containers back in:

```yaml
# in docker-compose.yml, on the cloudflared service:
labels:
  - "com.centurylinklabs.watchtower.enable=true"

# and change Watchtower to label-based mode:
- WATCHTOWER_LABEL_ENABLE=true
- WATCHTOWER_MONITOR_ONLY=false
```

This gives you auto-updates only where you explicitly want them.

---

## 5. Cloudflare Access: What to Expose and What Not To

### Current exposure (from your README)

Your stack exposes these services externally via Cloudflare Tunnel:

- `overseerr.twoplustwoone.dev` — request management (this is appropriate)
- `radarr.twoplustwoone.dev` — admin interface
- `sonarr.twoplustwoone.dev` — admin interface
- Potentially Prowlarr, Tautulli, Bazarr

### The problem

Radarr, Sonarr, and Prowlarr are admin interfaces. They have no meaningful
multi-user access model. If someone else gets the URL (or brute-forces the
API key), they can delete your entire library metadata, change your indexers,
or add malicious custom scripts that run on your Pi. Exposing them to the
public internet — even through Cloudflare — without an additional auth layer
is risky.

### What to do: Cloudflare Access policies

Cloudflare Access lets you put an authentication wall in front of any tunnel
endpoint. The free tier supports this. Set up an Access Application for each
admin service:

1. Go to Cloudflare Dashboard → Zero Trust → Access → Applications
2. Add an application for each admin service
3. Set the policy to: Allow, Email addresses, add your email
4. Users (including you) get an email OTP or can use Google/GitHub SSO

Services and their recommended exposure level:

| Service | External Expose? | Cloudflare Access Required? |
|---|---|---|
| Overseerr | Yes | Recommended (it has its own auth, but belt+suspenders) |
| Plex | Yes | No (Plex has its own strong auth + Plex.tv verification) |
| Radarr | Cautiously | Yes — Access required before any other auth |
| Sonarr | Cautiously | Yes — Access required before any other auth |
| Prowlarr | Avoid or use Access | Yes if exposed at all |
| Tautulli | Avoid or use Access | Yes if exposed at all |
| Portainer | Avoid | Do not expose externally even with Access |
| Netdata | Avoid | Use Netdata Cloud instead (outbound connection only) |
| Dozzle | Never | Too sensitive — SSH or local only |
| qBittorrent | Never | Not external-facing |

### The principle

Expose only what you need to access from outside your home network. For media
requests, that's Overseerr. For watching, that's Plex (which manages its own
authentication well). Everything else can wait until you're on your home network
or connected via VPN.

---

## 6. Backup and Recovery

### What will hurt if you lose it

The containers themselves are ephemeral — you can pull images any time. What
cannot be recovered without a backup:

- **Plex config** (`BASE_PATH/plex/config`): Contains the Plex database with all
  watch history, metadata, ratings, collections, playlists, and server tokens.
  Losing this means re-scanning your entire library and losing play history.
- **Radarr config** (`BASE_PATH/radarr/config`): All your movie entries, custom
  formats, quality profiles, indexer configs, and notification settings.
- **Sonarr config** (`BASE_PATH/sonarr/config`): Same as Radarr but for TV.
- **Prowlarr config** (`BASE_PATH/prowlarr/config`): All your indexer credentials
  and settings. These are annoying to re-enter.
- **Tautulli config** (`BASE_PATH/tautulli/config`): Your play history database.
- **Uptime Kuma data** (`BASE_PATH/uptime-kuma`): All your monitors and history.
- **Bazarr config** (`BASE_PATH/bazarr/config`): Subtitle provider credentials.

Your media files themselves are presumably on a separate drive and are the
hardest to recover (re-downloading takes forever), but they are not in Docker
volumes.

### Backup approach for a Pi

The simplest approach that actually works:

**Option A: rsync to a remote destination (recommended)**

Add a cron job or a scheduled script that rsyncs your `BASE_PATH` to another
machine (a NAS, a cloud storage mount, or even a second drive on the Pi):

```bash
#!/bin/bash
# backup-configs.sh — run daily, e.g. 3am
set -euo pipefail

BACKUP_DEST="user@nas-ip:/backups/pi4-plex"
CONTAINERS=(plex radarr sonarr prowlarr overseerr tautulli uptime-kuma bazarr)
BASE_PATH="/home/username/docker"

# Stop containers for a clean backup (optional but safer for Plex DB)
# docker compose stop plex radarr sonarr

for service in "${CONTAINERS[@]}"; do
  rsync -az --delete \
    "${BASE_PATH}/${service}/" \
    "${BACKUP_DEST}/${service}/"
done

# Restart if stopped
# docker compose start plex radarr sonarr

echo "Backup complete: $(date)"
```

**Option B: Rclone to cloud storage**

Rclone can push to Google Drive, Backblaze B2, Cloudflare R2, or any S3-compatible
storage. Good if you don't have a NAS. Config volumes are small (a few hundred MB
total) and will fit on any free tier.

**What not to back up**: The qBittorrent/gluetun config (these are runtime state),
the Plex transcode cache (ephemeral), and your media files via this script (use
rsync or your NAS for that separately).

### Plex database safety

The Plex database (`Plug-in Support/Databases/com.plexapp.plugins.library.db`)
is a SQLite file. SQLite has a WAL (write-ahead log) mode, so copying it while
Plex is running can produce a corrupt backup. Either:

- Stop Plex during the backup window (`docker stop plex && rsync ... && docker start plex`)
- Or use `sqlite3 source.db .dump | gzip > backup.sql.gz` which reads safely

For a home setup, stopping Plex for 30 seconds at 3am is the simpler and safer choice.

---

## 7. Radarr / Sonarr Quality Profiles for Pi

This is where most Pi-based Plex setups quietly suffer without knowing why.
Understanding the codec situation completely changes how you configure Radarr
and Sonarr.

### The Pi 4 codec reality

The Pi 4 has a VideoCore VI GPU with hardware video decode. Plex can use it for:

- **H.264 (AVC)**: Hardware decode works. This is the sweet spot.
- **H.265 (HEVC)**: The GPU can decode HEVC in theory, but Plex's Linux ARM
  driver does not expose a working HEVC hardware decode path. In practice,
  HEVC files almost always trigger software transcoding on a Pi, which will
  saturate all four CPU cores and cause buffering or dropped frames.
- **AV1**: No hardware decode. Avoid entirely.
- **VP9**: No hardware decode via Plex. Avoid.
- **MPEG-2 / VC-1**: Hardware decode works but these are old formats you're
  unlikely to encounter.

The practical rule: **H.264 is the only safe codec on a Pi-based Plex setup.**
HEVC sounds better on paper (same quality at smaller file size) but it will make
your Pi miserable.

### What "transcoding" means in practice

Plex transcodes a stream when:

- The client can't decode the video codec natively (codec transcode)
- The bitrate exceeds what the client can handle (bitrate-based transcode)
- The audio format isn't supported by the client (audio transcode)
- Subtitles need to be burned in (subtitle-forced transcode)

Transcoding on a Pi 4 without hardware acceleration caps you at roughly
1x realtime for a 1080p H.264 stream. That's barely enough for one stream.
HEVC transcoding caps you at roughly 0.3x realtime — unwatchable.

Direct play uses essentially zero CPU. That's what you want, always.

### The goal: configure everything to always direct play

Direct play happens when: the file codec is natively supported by the client,
the audio format is natively supported, and subtitles are either external SRT
or can be rendered by the client. The way you achieve this is by downloading
files that match what your clients can play.

Most modern clients (Plex web, Plex app on iOS/Android/Apple TV, Nvidia Shield,
smart TVs) can direct play H.264 at 1080p without issue.

### Recommended Radarr quality profile

**Profile name**: "Pi Safe — 1080p H.264"

Quality cutoff (minimum acceptable): **Bluray-1080p**
Quality ceiling (maximum): **Bluray-1080p** (do not chase Remux)

Enabled qualities (in priority order):

1. Bluray-1080p
2. WEBRip-1080p
3. WEBDL-1080p
4. HDTV-1080p (fallback only)

**Custom Formats to add** (these are the key part):

Create these custom formats and assign positive/negative scores:

| Custom Format | Score | Reason |
|---|---|---|
| x264 | +200 | Force preference for H.264 encodes |
| x265 / HEVC | -10000 | Virtually blacklist HEVC |
| AV1 | -10000 | Blacklist AV1 |
| Remux-1080p | -500 | Files are too large; 30–60GB files slow down the Pi's I/O |
| HDR | -500 | Most clients won't direct play HDR without tone-mapping, which transcodes |
| DV (Dolby Vision) | -10000 | Blacklist Dolby Vision — guaranteed transcode on every client |
| Multi-Audio | +50 | Nice to have but not required |
| x264 (no group tag) | -100 | Prefer scene/P2P releases over random encodes |

**File size guidance**: For 1080p H.264, target 4–15 GB per movie. Files under
3 GB are often too compressed (low bitrate visible in action scenes); files over
20 GB are either remux or overproduced encodes that waste disk and I/O.

### Recommended Sonarr quality profile

**Profile name**: "Pi Safe — 1080p H.264"

Same logic as Radarr. For TV specifically:

- Prefer WEB-DL or WEBRip over Bluray (most TV content is streaming-native anyway)
- Target file size: 1–4 GB per episode for 1080p
- Apply the same HEVC penalty: -10000 on x265/HEVC
- Enable "Prefer HD" in Sonarr's language profile

### Audio format guidance

| Format | Safe? | Notes |
|---|---|---|
| AAC Stereo | ✅ | Universally safe, direct plays everywhere |
| AAC 5.1 | ✅ | Safe for most clients |
| AC3 / EAC3 (Dolby Digital) | ✅ | Safe for most clients |
| DTS-Core (DTS) | ✅ | Most clients handle this |
| DTS-HD MA | ⚠️ | Clients that don't support it will transcode audio (CPU hit) |
| TrueHD / Atmos | ⚠️ | Usually transcodes on clients that don't natively support it |
| DTS:X | ❌ | Avoid — will transcode |

Audio transcoding is much less CPU-intensive than video transcoding, but it
still adds load. If your clients are primarily web browser or mobile, prefer
files with AAC or EAC3 audio tracks.

### Subtitle strategy

- **SRT (external or embedded)**: Always safe. Rendered by the client.
- **ASS/SSA**: Rendered by client in most cases.
- **PGS (image-based)**: Requires burn-in for clients that don't support them,
  which forces a CPU transcode. Apple TV is the main culprit.
- **Forced subtitles**: Usually fine but test your clients.

In Bazarr, prefer SRT over PGS. If your clients are Apple TV-heavy, this
matters a lot.

### The Recyclarr shortcut

[Recyclarr](https://recyclarr.dev) is a tool that automatically syncs
TRaSH Guides custom formats and quality profiles into Radarr/Sonarr from
a YAML config file. Instead of manually configuring all the above, you define
it in code and Recyclarr applies it. Highly recommended for keeping profiles
in sync after updates. There is a Docker image available.

---

## 8. Hardware Assessment and Upgrade Path

### What the Pi 4 does well

- Running Plex as a media server for 1–2 concurrent direct play streams
- Running the entire *arr stack (Radarr, Sonarr, Prowlarr, Overseerr, Bazarr)
  with minimal resource use
- qBittorrent with Gluetun — download management is not CPU-intensive
- Running Tautulli, Uptime Kuma, Dozzle, Watchtower — all lightweight
- Serving media over your local network to clients that direct play

### Where the Pi 4 is fundamentally limited

- **Transcoding**: Any transcoding will visibly degrade the system. One HEVC
  transcode saturates all four cores.
- **Multiple concurrent streams**: Even two simultaneous direct-play streams
  of high-bitrate 1080p can stress the USB 3.0 bus and cause I/O bottlenecks.
- **4K content**: 4K H.264 has no hardware decode path in Plex on Pi. 4K HEVC
  is completely out of the question. Do not serve 4K from a Pi.
- **RAM ceiling**: 4GB (or 8GB) fills up faster than you'd expect with all
  these containers. Plex alone uses 300–600MB at idle, more during playback.
  With the full advanced stack, you're likely at 2.5–3.5GB in use.
- **SD card I/O**: If you're running the OS from an SD card (even a fast one),
  write-heavy workloads (Plex database writes, logging, Tautulli inserts) will
  wear it out and slow everything down. A USB SSD for the OS is non-optional
  for long-term reliability.
- **Thermal**: The Pi 4 throttles at ~80°C. Sustained Docker workloads in a
  warm environment will throttle it. A heatsink case (Argon ONE, Flirc, etc.)
  is mandatory.

### Symptoms that you've outgrown the Pi

You've outgrown the Pi if you regularly experience any of these:

- Plex buffering during playback that isn't caused by the source file
- SSH commands taking 5–10 seconds to respond (CPU/IO saturation)
- Docker container start times becoming very slow
- Watchtower updates failing because the system ran out of RAM during a pull
- Thermal throttling events visible in Netdata
- Disk fill rate accelerating beyond your ability to manage it
- Any client regularly triggering a transcode (because you can't fix the file)

### Upgrade path

**Tier 1 upgrade (stay ARM, ~$100)**: Raspberry Pi 5

The Pi 5 has roughly 2–3x the CPU performance of Pi 4, better thermal headroom,
and a PCIe 2.0 interface for an NVMe SSD (via a HAT). This is a good upgrade
if you're happy with your current stack's functionality and just need more headroom.
Still no meaningful Plex hardware transcode improvement for HEVC.

**Tier 2 upgrade (go x86, ~$150–300)**: Intel N100 / N305 mini PC

This is the highest-value upgrade recommendation. Mini PCs using the Intel N100
(Beelink Mini S12, Trigkey S5, CWWK N100, GMKtec G3, etc.) offer:

- Intel Quick Sync hardware transcoding for H.264, HEVC, and even AV1
- 4–16GB RAM (typically 8–16GB soldered or upgradable)
- Built-in NVMe storage slot
- Power consumption: 6–15W under load (comparable to Pi 4)
- Full x86_64 Linux, so all Docker images work without ARM compatibility concerns
- Intel iGPU exposed as `/dev/dri/renderD128` — Plex hardware transcode works
  for H.264 and HEVC without any additional configuration

With an N100, you can serve 4–6 simultaneous HEVC 1080p transcodes while keeping
the CPU under 40%. The Pi 4 cannot do even one. This upgrade fundamentally changes
what your Plex server can do.

**Tier 3 upgrade (NAS/storage focus, ~$400–800)**: Synology DS923+ or similar

If your primary concern is storage reliability (RAID, data redundancy, drive
management) rather than transcoding, a NAS with Docker support (Synology DSM,
TrueNAS Scale, Unraid) makes sense. These pair well with a dedicated Plex
transcoding machine or Plex running on a more capable mini PC.

**Migration path (Pi 4 → N100)**:

1. Set up the N100 with Ubuntu Server LTS
2. Install Docker and Docker Compose
3. Clone your repo to the N100
4. Copy your `BASE_PATH` config directories from the Pi to the N100 (rsync works)
5. Update `.env` with any paths that changed
6. Run `docker compose --profile advanced up -d` on the N100
7. Update your Cloudflare Tunnel token to point at the N100 (or just move the
   cloudflared container to the N100, which auto-handles routing)
8. Test Plex, Radarr, Sonarr — confirm everything works
9. Decommission the Pi (or repurpose it as a Pi-hole / network monitor)

The migration is straightforward because your entire stack is already
containerized with config volumes. The hardest part is moving your media
files, which are likely already on an external drive you can just replug.

---

## 9. Start Here: Priority Checklist

Work through these in order. Each item is independent — you don't need to do
them all at once.

### This week (high impact, low effort)

- [ ] **Add `docker-autoheal`** — single most important reliability fix.
  Without this, unhealthy containers just sit there. Takes 5 minutes.

- [ ] **Switch Watchtower to `WATCHTOWER_MONITOR_ONLY=true`** — stops silent
  auto-updates. Now you'll be notified of updates and apply them deliberately.

- [ ] **Verify your notification channel is actually working** — send a test
  alert from Uptime Kuma and confirm you receive it. Many people set it up and
  never verify it works.

- [ ] **Check if you're on SD card** — run `lsblk` on the Pi. If your root
  filesystem is `mmcblk0`, you're on SD card. Move to a USB SSD immediately.
  SD cards fail under write load. Your entire stack will become unreliable.

- [ ] **Run the image pinning script** (`scripts/pin-images.sh .env`) to lock
  your images to digests. Now if you run `docker compose pull`, it pulls exactly
  the version you pinned, not whatever "latest" is today.

- [ ] **Enable the advanced profile** if you haven't already:
  ```bash
  docker compose --profile advanced up -d
  ```
  Tautulli, Dozzle, Uptime Kuma, and Bazarr are sitting in your compose file
  but not running unless you use this profile.

### Next two weeks (medium effort, high payoff)

- [ ] **Add Netdata** for system-level metrics and temperature monitoring.
  Configure Discord alerts for CPU, disk, and temperature thresholds.

- [ ] **Add Portainer** for Docker management without SSH.

- [ ] **Set up Cloudflare Access** for Radarr and Sonarr. Takes 10 minutes in
  the Cloudflare dashboard. Adds meaningful security to your admin interfaces.

- [ ] **Set up Uptime Kuma monitors** properly — add monitors for all your
  services including the external Cloudflare tunnel URLs (the README has the
  recommended monitor list). Test that alerts fire.

- [ ] **Configure Radarr/Sonarr quality profiles** with HEVC blacklisted.
  Even if you don't do the full Recyclarr setup, manually setting x265 to
  -10000 score will stop new downloads from being HEVC. Existing HEVC files
  can be upgraded to H.264 by enabling "Upgrade Until" in your quality profile.

### When you have a rainy afternoon

- [ ] **Set up config backups** with rsync or rclone. Pick a destination (NAS,
  Backblaze B2, Google Drive) and schedule a daily backup of `BASE_PATH`.
  This is the difference between a minor incident and a catastrophic one.

- [ ] **Add a heatsink/fan case to the Pi** if you haven't. Check Netdata's
  temperature graph after adding it — you should see 10–15°C drop.

- [ ] **Audit your media library for HEVC files** — Tautulli's media info
  reports can show you which files are being transcoded. Target those for
  replacement with H.264 encodes.

- [ ] **Review Recyclarr** for managing quality profiles as code. Once set up,
  it maintains your Radarr/Sonarr profiles automatically as TRaSH Guides
  recommendations evolve.

### If you decide to upgrade hardware

- [ ] Evaluate Intel N100 mini PCs (Beelink, Trigkey, CWWK brands all work well)
- [ ] Keep the Pi running until the N100 is fully tested
- [ ] Follow the migration path in Section 8 — it's a copy-paste migration,
  not a rebuild
