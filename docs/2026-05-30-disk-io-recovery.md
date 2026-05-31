# 2026-05-30 — Disk-I/O recovery & config relocation

Record of the work done on **2026-05-30** to stop the Pi from freezing under load,
slim the stack to a minimal base profile, and move the container databases off the
slow USB hard drive.

## TL;DR

- The Pi (Raspberry Pi 4, **2 GB RAM**) was freezing. Root cause was **disk I/O
  saturation on a slow spinning USB drive**, *not* RAM.
- Slimmed the default compose profile down to a minimal base set.
- Throttled qBittorrent so its startup re-hash can't saturate the disk.
- **Moved all container configs/databases from the USB HDD to the SD card**, which is
  the change that actually fixes the contention.
- End state: all base containers healthy and the Pi is responsive again.

## Environment

- Host: `pi@192.168.1.188` (hostname `raspberrypi`). Key-based SSH set up this day
  (no password needed — copied `~/.ssh/id_ed25519.pub` via `ssh-copy-id`).
- Repo on Pi: `/home/pi/pi4-ultimate-plex-stack`.
- Storage layout discovered:
  - `sda` = **WDC WD40NDZW** — a 4 TB *spinning* USB portable HDD (My Passport),
    mounted at `/mnt/library`, ~87% full. Terrible random-I/O performance.
  - `mmcblk0` = 29.7 GB **SD card**, holds the OS (`/`), ~7.3 GB free.
- Before today: `BASE_PATH=/mnt/library/docker-configs` and `MEDIA_SHARE=/mnt/library`
  — i.e. **all app databases AND all media lived on the one slow HDD**.

## Diagnosis

Symptoms during load: load average spiked to **20–43**, SSH responses dropped, `docker`
commands hung. But CPU was nearly idle — the time was spent in **I/O wait (70–89%)**.
Processes consistently stuck in uninterruptible (`D`) state: `usb-storage`,
`jbd2/sda1-8` (ext4 journal), `kworker/*flush-8:0` (writeback to `sda`).

Conclusion: the spinning USB HDD couldn't keep up with the random I/O of every app's
database (Plex DB, Radarr/Sonarr/Prowlarr SQLite) **plus** media access. Two cold-start
operations triggered the worst storm:

1. **qBittorrent** re-hashes every torrent on startup (reads all torrent data off disk).
2. **cross-seed** does a full scan on startup.

Running both at once (plus several containers recreating simultaneously) pinned the disk.

The drive itself is **not failing** — the only kernel error was an old, harmless
`DISCARD/TRIM not supported` rejection from the USB-SATA bridge (weeks ago).

## Changes made

### 1. Minimal base compose profile (`docker-compose.yml`)

Goal: the default `docker compose up -d` should run only the bare-bones stack; push
notification/health/monitoring services into the `advanced` profile.

Diff:

```diff
   cross-seed:
     image: ${CROSS_SEED_IMAGE:-ghcr.io/cross-seed/cross-seed:latest}
     container_name: cross-seed
-    profiles: ["advanced"]          # moved cross-seed INTO base

   watchtower:
     container_name: watchtower
+    profiles: ["advanced"]          # update notifications -> advanced

   autoheal:
     container_name: autoheal
+    profiles: ["advanced"]          # health auto-restart -> advanced
```

Resulting **base** set (runs on plain `docker compose up -d`):
`plex, radarr, sonarr, prowlarr, qbittorrent, gluetun (VPN), cross-seed, overseerr, cloudflared`.

Everything else (`unpackerr, tautulli, bazarr, autobrr, dozzle, flaresolverr,
uptime-kuma, netdata, portainer, watchtower, autoheal`) is now `advanced`-only.

> **Compose gotcha:** `docker compose up -d --remove-orphans` does **not** remove
> containers that are merely in an inactive profile — they're still "defined". The
> leftover advanced containers had to be removed explicitly with `docker rm -f`.
>
> **Restart-policy gotcha:** `docker kill` on a `restart: unless-stopped` container
> lets Docker auto-restart it. Use `docker stop` (which marks it stopped) to keep it down.

### 2. qBittorrent re-hash throttle

Set in `qBittorrent.conf` under `[BitTorrent]`:

```
Session\MaxActiveCheckingTorrents=1
```

This makes qBittorrent re-check **one torrent at a time** instead of many in parallel,
so a startup re-hash can't saturate the disk. Backup left at
`qBittorrent.conf.bak-throttle` (in the qbittorrent config dir).

### 3. Config/database relocation (the real fix)

Moved all container configs from the slow HDD to the SD card. Media stays on the HDD.

Procedure used:

```bash
cd ~/pi4-ultimate-plex-stack
docker compose down -t 90                                   # clean stop everything
sudo rsync -aHAX /mnt/library/docker-configs/ /home/pi/docker-configs/   # ~1.8 GB, 8,414 files
sed -i 's|^BASE_PATH=.*|BASE_PATH=/home/pi/docker-configs|' .env         # repoint
docker compose up -d plex radarr sonarr prowlarr qbittorrent overseerr cloudflared
```

- `.env` change: `BASE_PATH=/mnt/library/docker-configs` → `BASE_PATH=/home/pi/docker-configs`.
  Backup left at `.env.bak-basepath`.
- `MEDIA_SHARE=/mnt/library` is **unchanged** — bulk media stays on the HDD (sequential
  I/O the HDD handles fine).
- Old configs still on the HDD at `/mnt/library/docker-configs` as a safety net.

Config sizes that moved (total ~1.8 GB): plex 1.1 GB, radarr 277 MB, sonarr 177 MB,
prowlarr 97 MB, qbittorrent 18 MB, everything else < 10 MB.

### 4. qBittorrent seeding / ratio hardening

With 180 torrents and queueing enabled (`Maximum active torrents = 5`), the queue was
stopping most torrents from seeding — bad for ratio on a private tracker (IPTorrents).
Two safe settings added/changed in `qBittorrent.conf` `[BitTorrent]`:

```
Session\ShareLimitAction=Stop                 # was RemoveWithContent (deleted torrent+data on limit!)
Session\IgnoreSlowTorrentsForQueueing=true    # idle/slow seeds don't count vs the active cap
```

- `ShareLimitAction=Stop`: if a ratio/seeding-time limit is ever hit, the torrent just
  **stops** instead of deleting itself **and its files**. (Previously `RemoveWithContent`
  — a data-loss footgun.)
- `IgnoreSlowTorrentsForQueueing=true`: lets all 180 torrents stay seeding (they sit idle,
  ready for peers) without tripping the "5 active" queue cap, while downloads stay capped
  at 3 (gentle on the HDD, since seeding is cheap reads but downloading is random writes).

Backup left at `qBittorrent.conf.bak-seedfix`. Existing global limits remain unlimited
(`GlobalMaxRatio=-1`, `GlobalMaxSeedingMinutes=-1`) so nothing auto-stops seeding.

**Still TODO for best seeding (bigger changes, not done yet):** enable PIA **port
forwarding** in gluetun (`VPN_PORT_FORWARDING=on`) so peers can reach us for upload, ideally
paired with the **OpenVPN → WireGuard** switch.

### 5. PIA port forwarding (seeding / ratio)

Enabled native port forwarding on the **existing OpenVPN** setup (NOT WireGuard — see
note below). Added to the gluetun service in `docker-compose.yml`:

```yaml
      - VPN_PORT_FORWARDING=on
```

- gluetun negotiates a forwarded port with PIA and writes it to
  `/tmp/gluetun/forwarded_port` (also readable via the control server at
  `http://127.0.0.1:8000/v1/openvpn/portforwarded`). It auto-selects a PF-capable
  server. First assigned port: **42299**, valid ~62 days.
- gluetun automatically opens that port (tcp+udp) in its firewall.
- qBittorrent's listen port was set to match: `Session\Port=42299` in `qBittorrent.conf`
  (done by stopping qBit, editing, restarting). Verified qBit listens on `42299` on the
  tunnel interface, and the VPN exit IP stays separate from the host (no leak).

**Why not WireGuard:** gluetun has no native PIA WireGuard support as of early 2026 — it
requires generating a custom config with an external tool (`kylegrantlucas/pia-wg-config`),
and PIA port-forwarding on custom WireGuard configs is buggy
(qdm12/gluetun#3070, Dec 2025). Not worth the fragility for a CPU saving. Stayed on OpenVPN.

**Phase 2 (NOT done — needs a decision):** PIA's forwarded port **changes on every VPN
reconnect**, so the static `Session\Port` above goes stale after a reconnect (~62 days, or
sooner if the tunnel drops). Auto-updating it requires gluetun's
`VPN_PORT_FORWARDING_UP_COMMAND` to call qBittorrent's API, which needs EITHER:
- `WebUI\LocalHostAuth=false` (disable auth for in-namespace localhost calls — generally
  safe since LAN/remote traffic isn't seen as 127.0.0.1, but a security trade-off), OR
- the qBittorrent WebUI password stored in the gluetun up-command.

Until then: after a VPN reconnect, re-read `/tmp/gluetun/forwarded_port` and update
`Session\Port` to match (stop qBit, edit, start).

## End state

- All 8 base containers (cross-seed intentionally left off) come up **healthy**.
- The database thrashing is gone; the Pi is responsive (`docker ps` returns instantly).
- Remaining load is *real work*: qBittorrent actively downloading (writing to the HDD)
  and gluetun's **OpenVPN pegging one CPU core** to encrypt that traffic. This eases as
  active downloads finish.

## Follow-ups / recommendations

1. **Verify, then reclaim space:** once you're confident everything works for a day or
   two, delete the old configs to free ~1.8 GB on the HDD:
   `rm -rf /mnt/library/docker-configs` (the dir, not the parent `/mnt/library`!).
2. **cross-seed:** still stopped. Its always-on daemon scan is heavy on the HDD. Bring it
   back deliberately (`docker compose up -d cross-seed`) and watch load; consider running
   it sparingly rather than 24/7.
3. **Port forwarding auto-update (Phase 2)** — see §5. Static port works ~62 days; after a
   reconnect it must be updated manually unless we wire up the gluetun up-command (needs an
   auth trade-off decision). ~~Switch to WireGuard~~ — *not viable*: no native PIA WireGuard
   in gluetun, and PF on custom WG is buggy (see §5). The OpenVPN CPU usage remains the main
   bottleneck but there's no clean fix on PIA right now.
4. **SD-card endurance:** databases write constantly; a decent SD card is fine for a long
   time, but a small dedicated USB **SSD** (not the spinning HDD) for configs would be the
   most durable home. Same procedure, different target path.
5. **`.env` now differs between the Pi and the local repo checkout** (`.env` is gitignored).
   The Pi has `BASE_PATH=/home/pi/docker-configs`; update any other copies accordingly.

## Backups / rollback

| Backup | Location (on Pi) | Restores |
| --- | --- | --- |
| `.env.bak-basepath` | `~/pi4-ultimate-plex-stack/` | old `BASE_PATH` |
| `qBittorrent.conf.bak-throttle` | qbittorrent config dir | pre-throttle qBittorrent config |
| `qBittorrent.conf.bak-seedfix` | qbittorrent config dir | pre-seeding-hardening qBittorrent config |
| `qBittorrent.conf.bak-portfwd` | qbittorrent config dir | pre-port-forwarding qBittorrent config |
| `docker-compose.yml.bak-portfwd` | `~/pi4-ultimate-plex-stack/` | compose before port forwarding |
| `.env.bak-portfwd` | `~/pi4-ultimate-plex-stack/` | `.env` before port forwarding |
| old configs | `/mnt/library/docker-configs/` | full pre-move config tree |

To roll back the relocation: `docker compose down`, restore `BASE_PATH` from
`.env.bak-basepath`, `docker compose up -d`.
