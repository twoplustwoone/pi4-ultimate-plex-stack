# Pi4 Plex Stack — Full Context for Hardware Discussion

_Compiled from maintenance sessions through 2026-07-14. Use this to discuss
upgrade and migration options._

## The Setup

**Hardware:** Raspberry Pi 4, **2 GB RAM** model. Running Raspberry Pi OS (Linux ARM). OS boots from **SD card** (this is a problem — see below).

**Storage:** A single **WD My Passport 4TB spinning USB HDD** (`WDC WD40NDZW`,
`/dev/sda1`), mounted at `/mnt/library`, 83% full (~626 GB free). This disk
holds all media files and is confirmed to be failing by SMART; replacement is
urgent. Docker container configs were previously on this disk too, but have
since been moved to the SD card.

**Network/VPN:** VPN via **gluetun** (PIA VPN, OpenVPN). qBittorrent routes all
traffic through gluetun, sharing its network namespace. PIA port forwarding is
enabled. Admin services use a **Cloudflare Tunnel**. Plex direct remote access
uses public TCP 32400 to `192.168.1.188:32400`; a Pi systemd timer renews the
OpenWrt UPnP lease every five minutes.

**The Stack (base profile, always running):**
- **Plex** — media server
- **Radarr** — movie automation
- **Sonarr** — TV automation
- **Prowlarr** — indexer management
- **qBittorrent** — torrent client (wrapped by gluetun)
- **gluetun** — VPN container
- **Overseerr** — media request UI
- **cloudflared** — Cloudflare tunnel for remote access
- **cross-seed** — cross-seeding (heavy; known problematic on this hardware, kept off/run rarely)
- *(Advanced profile, not always running: Tautulli, Bazarr, Dozzle, Autoheal, Watchtower, Uptime Kuma, Netdata, Portainer, FlareSolverr, Autobrr, unpackerr — 8 of these were stopped for 2+ months and later removed as dead containers)*

---

## The Core Problems (Root Causes Identified, In Order Discovered)

### 1. The Pi 4 Only Has 2 GB RAM (Fundamental Hardware Ceiling)

With ~10-14 containers running at various points, RAM fills up fast. Plex alone uses 300–600MB at idle, more during playback. The arr apps (Radarr, Sonarr, Prowlarr, Overseerr) each take 100–300MB. At any given time, the system sits near or at its RAM limit. When RAM runs out, the OS starts swapping to disk.

### 2. Swap Was on the Spinning USB HDD (Fixed 2026-06-24)

This was the biggest single configuration disaster. The OS swap file (`/mnt/library/.swapfile`, 2 GB) lived on the same slow spinning disk as all the media files. So whenever RAM filled up, the OS would page memory to/from the HDD — competing with Plex reading media files and qBittorrent writing torrent data. Load averages would spike to 20–43 with the CPU sitting at ~0% busy but 70–86% in **I/O-wait** (blocked on disk). SSH would time out. The arr web UIs would take 15–20 seconds to load or time out entirely.

**Fixed 2026-06-24:** Swap moved to **zram** (`/dev/zram0`, ~1.1 GB compressed, zstd, priority 100 — compressed RAM swap, zero disk I/O). `vm.swappiness=10` and `vm.vfs_cache_pressure=50` persisted so the OS is reluctant to swap at all. Both duplicate HDD-swap lines in `/etc/fstab` disabled so it can never come back after reboot. Result: arr web UIs went from 15–20s timeout → milliseconds.

**Gotcha learned:** draining an active HDD swapfile via `swapoff` is uninterruptible (D-state, can't be killed) and crawls at ~8MB/min because it's random reads on a spinning disk — nothing (including freezing qBittorrent) speeds it up. The faster fix for stranded app memory: `docker restart` the affected containers — process exit discards swapped pages instantly instead of slowly reading them back.

### 3. Docker Config Databases Were on the HDD (Fixed 2026-05-30)

Previously all Docker container configs (including Plex's SQLite database, Radarr/Sonarr/Prowlarr databases) lived on the spinning HDD (`BASE_PATH=/mnt/library/docker-configs`). Database random I/O on a spinning disk is brutal — every write to Sonarr's DB, every Plex metadata lookup, was random I/O on a disk that's already slow for random access.

**Fixed 2026-05-30:** All container configs (~1.8 GB / 8,414 files) rsync'd to the SD card at `/home/pi/docker-configs`. Only media files remain on the HDD. This eliminated the database I/O storms.

### 4. The Media HDD Is Slow and Confirmed Failing

The WD My Passport over USB has terrible random I/O. On sequential reads (streaming a movie) it's adequate — HDDs do 100+ MB/s sequentially, plenty for video. But anything requiring random I/O — qBittorrent re-hashing torrents on startup, cross-seed scanning, multiple containers doing random reads simultaneously — saturates it completely.

**Triggers that caused past load storms:**
- qBittorrent cold start → re-hashes ALL torrent data (reads all torrent files from HDD randomly) → disk pinned
- cross-seed initial scan → reads all torrent data simultaneously → compounds the qBittorrent problem
- Plex transcoding → CPU + I/O simultaneously
- A forgotten paused Plex Web (Firefox) tab transcoding audio → kept the audio encoder respawning → load climbed to 28, SSH timed out

**Mitigations applied:**
- `Session\MaxActiveCheckingTorrents=1` in qBittorrent (re-checks one torrent at a time instead of all at once)
- cross-seed kept off or run rarely (always-on daemon scan is too heavy)
- Swap moved to zram (no HDD swap I/O) — see #2

**Confirmed 2026-07-09:** SMART attributes establish that the drive itself is
failing. `Reallocated_Sector_Ct=10008` and `Reallocated_Event_Count=1051`
(normalized value 001) explain the long internal retry/remap stalls seen on
July 3. `UDMA_CRC_Error_Count=0`, so the evidence does not point to the USB
cable or link. `Current_Pending_Sector=0` and `Offline_Uncorrectable=0` mean the
currently visible data remains readable, but they do not make the drive safe.
Do not run a long SMART test or keep stressing it; copy irreplaceable data and
replace it before migration.

### 5. Plex Transcoding Kills the Pi

The Pi 4 can hardware-decode H.264 but **Plex does not expose a working HEVC hardware decode path on Linux ARM**. In practice:
- **H.264 direct play**: ~0% CPU, fine
- **H.264 transcode**: saturates all 4 cores, barely 1x realtime for 1080p
- **HEVC (H.265) transcode**: ~0.3x realtime — unwatchable, kills the system
- **Audio transcode** (e.g. browser can't play EAC3/DTS/TrueHD surround): adds CPU and I/O load even when video direct-plays

The 2026-06-24 major incident was triggered by a **Plex Web tab in Firefox** playing with audio transcoding — browsers can't natively play surround audio codecs, so Plex spins up the audio encoder even when video direct-plays. Combined with the old HDD swap, this made the system unresponsive (load 28, SSH timeouts).

**2026-07-10/11 client-specific finding:** a failing NVIDIA Shield playback
attempt copied H.264 video but transcoded the selected TrueHD Atmos 7.1 audio to
Opus because the client requested `directPlay=0`. Selecting the AC3 5.1 fallback
track with original-quality/direct playback enabled worked. This was not a Pi
load or disk-saturation incident at the time of diagnosis.

---

## Current State (as of 2026-07-14)

**What's been fixed:**
- ✅ Swap moved to zram — no more HDD swap I/O
- ✅ Docker configs on SD card — no more database I/O on HDD
- ✅ qBittorrent throttled — one re-check at a time
- ✅ cross-seed kept off by default
- ✅ PIA port forwarding enabled (OpenVPN; port re-sync after VPN reconnect is still manual — no automation yet)
- ✅ Plex direct remote access restored on public TCP 32400; OpenWrt's short
  UPnP lease is renewed by a five-minute systemd timer
- ✅ Dead/zombie containers cleaned up (8 removed, base profile is 8-9 healthy containers)
- ✅ Compose gated into base vs. `advanced` profiles
- ✅ Jellyfin parallel-pilot configuration added behind its own opt-in profile
- ✅ Scrutiny disk-health monitoring added to the advanced profile

**What's still true/still a risk:**
- ⚠️ **2 GB RAM is a hard ceiling.** zram buys headroom but doesn't add real RAM. Under sustained load (Plex + qBittorrent + arr apps all active), the system is still near its memory limit.
- ⚠️ **OS still runs from SD card.** SD cards fail under write load (Plex DB writes, logging). Long-term reliability risk, and no known backup of configs exists yet.
- ⚠️ **No hardware Plex transcoding.** Any transcode is brutal; clients must be configured to direct play.
- ⚠️ **The only media disk is actively failing and has no redundancy.** It has
  10,008 reallocated sectors; replace it rather than moving it into the next
  server as primary storage.
- ⚠️ **Plex Web (browser) causes audio transcoding** — native apps (Apple TV, Android TV, Roku, mobile) are needed for surround sound files without transcoding.
- ⚠️ **Shield TrueHD playback can still force an audio transcode.** Prefer an
  AC3 fallback track unless the entire HDMI/passthrough chain supports TrueHD.

---

## The Pi 4's Fundamental Limits

| What | Pi 4 Reality |
|---|---|
| RAM | 2 GB hard ceiling (this model). 4 GB/8 GB models exist. |
| CPU | 4x ARM Cortex-A72 @ 1.8GHz. Fine for arr stack + direct play. Not for transcoding. |
| Storage bus | USB 3.0 for external drives — shared bus and limited IOPS on the spinning HDD; the current HDD itself is failing |
| GPU/Hardware decode | H.264 only via Plex on Linux. HEVC = software only = unusable for transcoding. |
| NVMe/PCIe | **None on Pi 4.** No way to add a fast internal SSD without an adapter. Pi 5 has PCIe. |
| 4K content | No hardware decode path in Plex. Completely out of the question. |
| Concurrent streams | 1 direct-play stream is fine. 2 is OK if both are H.264. Any transcode = system stress. |
| Thermal | Throttles at ~80°C under sustained load. Heatsink case mandatory. |

---

## What Would Actually Fix the Remaining Problems

### Critical (Should Do Regardless of Upgrade Decision)
1. **Replace the WD media drive now** — copy irreplaceable data while reads are
   still succeeding. Do not carry this disk into the HP ProDesk migration as
   primary storage, and do not spend time on a long SMART self-test.
2. **Config backups** — rsync/rclone `/home/pi/docker-configs` to another disk,
   NAS, or cloud target before migrating. This protects the ~1.8 GB of service
   state currently living on the SD card.
3. **USB SSD for the OS/configs if the Pi remains in service** — boot from a
   fast USB SSD instead of SD card to reduce wear and improve reliability.
4. **Radarr/Sonarr quality profiles — strongly penalize HEVC** where clients
   cannot direct-play it.
5. **Clients configured for Direct Play** — use Original/Maximum quality and
   native apps. On the Shield, use the AC3 fallback when TrueHD passthrough is
   unavailable.

### High-Value Upgrade
6. **Migrate to the planned HP ProDesk 600 G6** — validate its exact CPU, RAM,
   storage, and Intel iGPU first, then move the container stack to x86_64.
   - Put the OS and `/home/.../docker-configs` on its internal SSD/NVMe.
   - Expose `/dev/dri` to Plex or Jellyfin for Intel Quick Sync where supported.
   - Copy configs while services are stopped, attach the replacement media
     storage, and bring up only the base profile first.
   - Keep the Pi intact until library metadata, remote access, direct play, and
     at least one hardware-transcode test pass on the ProDesk.
   - An Intel N100 mini PC remains a low-power alternative if the ProDesk's
     exact configuration is unsuitable.

### Nice to Have (Already in the Compose File, Just Not Running)
- **Autoheal** — watches for unhealthy containers and restarts them
- **Netdata** — system metrics with history, temperature monitoring, alerts for CPU/RAM/disk/IO-wait
- **Portainer** — Docker management web UI without SSH
- **Tautulli** — Plex analytics, identifies which files are transcoding (helps audit HEVC problem)
- **Scrutiny** — SMART history and alerts; useful for replacement disks, but it
  does not make the current failing drive safe

---

## Budget Framing

| Option | Cost | What It Fixes |
|---|---|---|
| Replacement media storage | Varies by capacity/redundancy | Removes the immediate failing-drive risk |
| USB SSD (64–128GB) | ~$20–30 | SD card failure risk, faster config I/O |
| Pi 5 + NVMe HAT | ~$100–150 | More CPU headroom, PCIe NVMe; still no HEVC HW transcode in Plex |
| Intel N100 mini PC | ~$150–250 | Everything: RAM, transcode, NVMe, x86 |
| N100 + separate NAS | $400–800 | Transcoding + storage redundancy/RAID |

**Bottom line:** The media drive is the immediate failure risk and should be
replaced first. The Pi 4 with 2 GB RAM is also at its practical limit: its SD
card, RAM ceiling, and lack of useful HEVC transcoding remain hardware
constraints. The planned HP ProDesk migration can address the compute and boot
storage constraints, but it must use replacement media storage rather than the
failing WD drive.

---

## Key Technical Facts for Reference

- Pi IP: `192.168.1.188`, hostname `raspberrypi`, user `pi`, SSH key-based (no password)
- Repo: `/home/pi/pi4-ultimate-plex-stack`
- Docker configs: `/home/pi/docker-configs` (SD card)
- Media: `/mnt/library` (WD My Passport 4TB USB HDD, `sda1`), 83% full
  (~626 GB free), failing with 10,008 reallocated sectors
- Swap: `/dev/zram0`, ~1.1 GB compressed, zstd, priority 100 — swappiness=10, vfs_cache_pressure=50
- Domain: `twoplustwoone.dev` (Cloudflare tunnel, external access)
- Plex remote access: public TCP 32400 to `192.168.1.188:32400`, with a
  five-minute UPnP renewal timer on the Pi
- VPN: PIA via gluetun (OpenVPN — WireGuard not viable on PIA via gluetun). Port forwarding enabled, port changes on VPN reconnect with no auto-update yet — must be manually re-synced to qBittorrent's `Session\Port`.

---

## Open Questions to Bring to Claude Chat

1. **What exact CPU, RAM, and storage configuration is in the HP ProDesk 600
   G6, and does its Intel iGPU support the desired Plex/Jellyfin transcodes?**
2. **What replacement media-storage strategy fits the budget?** One external
   disk is simplest; mirrored storage or a NAS adds resilience.
3. **Should the ProDesk run Plex, Jellyfin, or both during migration?** Use the
   existing Jellyfin pilot to compare clients without risking Plex metadata.
4. **What is the minimum backup target for `/home/pi/docker-configs` before the
   migration starts?**
5. **Should the Pi remain as a fallback service after migration, or be retired
   once the ProDesk passes burn-in?**
