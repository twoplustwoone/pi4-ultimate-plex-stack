# Plex to Jellyfin migration

Jellyfin replaces Plex on `yggdrasil`. Both run side by side until Jellyfin has
carried all viewing for four weeks, then Plex and Tautulli are removed. Nothing
in the download pipeline depends on Plex, so the migration touches only playback,
Seerr's login, monitoring and backups.

The rules from the October 2026 Plex2Shell incident carry over unchanged.
Jellyfin is never port-forwarded, never pinned, and Watchtower keeps it current.
Remote devices reach it over Tailscale at the same address they use at home.

The household guide for phones, the laptop, the Shield and TVs is
[`jellyfin-devices.md`](jellyfin-devices.md).

## What the library asks of a player

Measured from Plex's library on 2026-10-29. There are 85 movies and 1,485
episodes, 3.4 TB in all.

| | HEVC | H.264 | VC-1 | 4K |
|---|---|---|---|---|
| Movies | 49 | 34 | 2 | 36 |
| Episodes | 1,119 | 366 | 0 | 124 |

Lossless audio is common. 17 movies are TrueHD and 35 titles are DTS-HD MA.

Three consequences follow.

- Most files are HEVC, so a client without HEVC decoding forces a transcode.
  Firefox is the household's likely case.
- The i5-10500T's UHD 630 decodes and encodes HEVC 10-bit through Quick Sync.
  Jellyfin uses it for free. Plex needs a Plex Pass for that, so today Plex
  transcodes on the CPU.
- 4K HDR and lossless audio are what the Shield is for. Direct play there is the
  main pass criterion.

## Phase 0. Make Jellyfin a hardened first-class service

One commit. The existing `jellyfin` service in `docker-compose.yml` is a pilot
stub written for the Pi, and it is weaker than the Plex service in three ways.

- [ ] Remove `profiles: ["jellyfin"]` so a plain `docker compose up -d` manages it.
- [ ] Replace the whole-disk `${MEDIA_SHARE}:/media:ro` mount with read-only
  binds of `${MEDIA_SHARE}/movies` and `${MEDIA_SHARE}/tv`, written the way Plex's are
  (`create_host_path: false`). A compromised server should see only the library.
- [ ] Add `devices: [/dev/dri:/dev/dri]` and `group_add: ["991"]`. The container
  runs as `${PUID}:${PGID}`, and `renderD128` is `root:render` mode 660, with
  `render` being gid 991 on this host.
- [ ] Add the label `com.centurylinklabs.watchtower.monitor-only=false`, and a
  comment that Jellyfin is the second exception for the same reason as Plex.
- [ ] Keep `network_mode: host`. Jellyfin then answers on `192.168.1.246:8096`,
  which the Tailscale route `192.168.1.246/32` already covers. No new route or
  firewall change is needed.
- [ ] Extend the canary's intrusion checks to Jellyfin.
  - [ ] Add `jellyfin` to `WATCHED` in `scripts/pipeline-canary.py`.
  - [ ] Add the image's program folders to `PROGRAM_DIRS` in
    `scripts/host_checks.py`. Check first that they are root-owned and not
    writable by `${PUID}`. Expect `/jellyfin/` and `/usr/lib/jellyfin-ffmpeg/`.
  - [ ] Generalize `plex_update_problem` to take a service name and an age limit.
    The official Jellyfin image is rebuilt per release, not weekly, so 21 days
    would false-alarm. Pick the limit from the release history, or compare
    against the registry's current `latest` digest instead.
  - [ ] Add tests beside the existing ones in `scripts/test_host_checks.py`.
- [ ] Add `jellyfin/config` to `SERVICES` in `scripts/backup-configs.sh`. Exclude
  `jellyfin/cache`, which is regenerable. Stop Jellyfin for the snapshot the way
  Plex is stopped, and skip the stop when `/Sessions` shows anything playing.
  Read the job's other side effects before the first run, per AGENTS.md.
- [ ] Add an Uptime Kuma HTTP monitor on `http://192.168.1.246:8096/health`.

**Done when** `docker compose config` is clean, the canary tests pass, and
`/verify` reports the stack unchanged.

## Phase 1. Start Jellyfin and build the library

No `sudo` is needed. The config folder is under `/home/pi/docker-configs`, which
the user already owns.

- [ ] `mkdir -p /home/pi/docker-configs/jellyfin/{config,cache}`, then
  `docker compose up -d jellyfin`.
- [ ] Confirm the library mounts are read-only with
  `docker inspect jellyfin --format '{{range .Mounts}}{{println .Destination .RW}}{{end}}'`.
- [ ] Open `http://192.168.1.246:8096` and run the wizard.
  - [ ] Create an admin account with a password not used anywhere else.
  - [ ] Add a Movies library on `/media/movies` and a Shows library on `/media/tv`.
    Leave real-time monitoring on, so new imports appear without a scan.
  - [ ] Turn off "Allow remote connections to this server" only if remote
    viewing is not wanted. Leave it on otherwise. Tailscale is the only way in.
- [ ] In Dashboard, Playback, Transcoding, choose Intel QuickSync (QSV). Enable
  hardware decoding for H.264, HEVC, HEVC 10-bit, VC-1 and VP9. Enable hardware
  encoding and tone mapping.
- [ ] In Dashboard, Networking, add `100.64.0.0/10` and `192.168.1.0/24` to the LAN
  networks. Without it, tailnet clients count as remote and get the remote
  bitrate limit.
- [ ] Run the first scan overnight. Turn on trickplay (scrub previews) only after
  it finishes, so the two do not compete.
- [ ] Install the Playback Reporting plugin. It replaces Tautulli's history.

**Done when** both libraries show every title Plex shows, and a forced transcode
of a 4K HEVC file shows `qsv` in its ffmpeg log under
`/home/pi/docker-configs/jellyfin/config/log/`.

## Phase 2. Accounts, watch history and devices

- [ ] Create a non-admin Jellyfin user for each person in Plex Home. Plex lists
  four accounts today.
- [ ] Copy watch history from Plex with `luigi311/jellyplex-watched`. Run it with
  `DRYRUN=True` first and read the log. Then run it for real, and keep it on a
  schedule for the whole parallel run, so progress follows a viewer between
  apps. The tool matches by provider IDs and file names, and its author calls it
  imperfect. Spot-check ten shows. Its maintenance status is unverified, so
  check its last release before relying on it.
- [ ] Set up every device per [`jellyfin-devices.md`](jellyfin-devices.md), and
  tick its checklist there.

**Done when** each person can open their own account on each of their devices,
and a show half-watched in Plex resumes at the same point in Jellyfin.

## Phase 3. Run both servers for four weeks

Jellyfin is the default app on every device. Plex stays installed as the
fallback, and every time someone falls back is recorded with the title and the
reason, in `.local/pi-issue-history.md`.

- [ ] Point Radarr and Sonarr at Jellyfin as well. In Settings, Connect, add an
  "Emby / Jellyfin" connection with a Jellyfin API key, so imports refresh the
  library. Keep the Plex connection until Phase 5.
- [ ] Each week, look at Jellyfin's Playback Reporting for transcodes. A transcode
  on the Shield is a finding. On a laptop browser it is expected for HEVC.
- [ ] Each week, check Tautulli for Plex plays. Each one is either a recorded
  fallback or a habit to change.

**Exit criteria.** All of these, over the last 14 days.

- [ ] No Plex plays in Tautulli.
- [ ] No open fallback entries.
- [ ] Every device row in the household guide is ticked.
- [ ] One remote session over cellular played to the end without stalling.

## Phase 4. Move Seerr and statistics to Jellyfin

Seerr 3.5.0 runs with Plex as its media server today, and household logins are
Plex accounts. Whether Seerr can change its media server in place without
losing request history is not established. Test it on a copy first.

- [ ] Copy `/home/pi/docker-configs/seerr/config` to a scratch folder and start a
  throwaway Seerr on another port against the copy. Switch it to Jellyfin and
  check that past requests and their availability survive.
- [ ] If they survive, do the same on the real instance after a backup. If they
  do not, decide whether request history matters enough to keep. A fresh
  Seerr on Jellyfin is the fallback.
- [ ] Each person signs in to Seerr with their Jellyfin account and makes one
  request.

## Phase 5. Switch Plex off, then remove it

The switch-off is reversible. The removal is not, so it waits.

- [ ] Run JellyPlex-Watched once more, then stop it.
- [ ] `docker stop plex tautulli`. Under `unless-stopped`, they stay off across
  reboots, which is the point. Leave them off for 30 days. `docker start plex`
  is the whole rollback.
- [ ] After the 30 days, archive and remove.
  - [ ] Tar `plex/config` and `tautulli/config`, excluding Plex's `Cache` and
    `Media` folders, and copy the tarball to R2. Confirm its size and that it
    lists.
  - [ ] Remove the `plex` and `tautulli` services from `docker-compose.yml`, and
    remove Plex's connection from Radarr and Sonarr.
  - [ ] Delete `scripts/ensure-plex-upnp-port.py` and
    `systemd/plex-upnp-port.{service,timer}`. Hand the user the `systemctl`
    command to remove the installed units.
  - [ ] Move the canary's update and process checks from Plex to Jellyfin only,
    and rename `plex-config-backup` to `config-backup`. The canary checks the
    unit by name, so rename both in one change, and hand the user the guarded
    script that swaps the installed units.
  - [ ] Drop Plex and Tautulli from `.claude/skills/verify/`.
  - [ ] Rewrite AGENTS.md's Plex sections for Jellyfin. Keep the incident
    history and the never-forward rule.
  - [ ] Delete the Plex config folders from the host. Pause and confirm with
    the user first. The R2 tarball is the only copy after this.
- [ ] In the plex.tv account, remove the server from Authorized Devices. Deleting
  the account itself is the user's call.

## Rollback

Until Phase 5's removal step, Plex is untouched and rollback is
`docker start plex tautulli`. Removing Jellyfin is `docker compose rm -sf jellyfin`
and deleting `/home/pi/docker-configs/jellyfin`. The media is mounted read-only
and never changes.

## Appendix A. Decisions and their reasons

- **Tailscale, not a public URL.** Jellyfin has had its own security advisories,
  and Plex2Shell showed what an exposed media server costs. Tailscale already
  carries Plex and needs no new route.
- **One address everywhere.** `192.168.1.246:8096` works at home without
  Tailscale and away with it, because the tailnet routes that address. Plex
  needed this because plex.tv rejects tailnet URLs. Jellyfin has no such limit,
  so `100.110.236.10:8096` also works, but one address means one setting per device.
- **Official image, not linuxserver.** The official image ships Jellyfin's own
  patched ffmpeg with the Intel drivers. Either would work. The official one
  keeps one maintainer between Jellyfin and its ffmpeg.
- **Playback Reporting, not Jellystat.** Jellystat is another container and
  database. The built-in plugin covers what Tautulli is used for here, which is
  who watched what and whether it transcoded.
- **Four weeks, not a fixed date.** The exit criteria end the parallel run, not
  the calendar. Four weeks is the minimum because a habit of opening Plex shows
  up only over several weekends.

## Appendix B. Sharing with someone outside the house

Plex charges for remote viewers, which is why sharing with a friend in Canada
stalled. Jellyfin does not. The free path is to share the `yggdrasil` node with
the friend from the Tailscale admin console, give them a Jellyfin user, and
have them connect to `http://100.110.236.10:8096`. Node sharing is not expected
to pass on the `192.168.1.246/32` route, so the friend uses the tailnet address.
Confirm that when setting it up.

Before sharing, add a Tailscale ACL that limits the friend to port 8096 on that
node. Every tailnet device can reach every service on the host today.
