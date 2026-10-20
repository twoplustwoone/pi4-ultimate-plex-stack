---
name: verify
description: Verify changes to the Plex stack on yggdrasil (Plex, Radarr/Sonarr/Prowlarr, Seerr, qBittorrent behind gluetun, qbit_manage, pipeline canary, Uptime Kuma) by probing the live host over SSH. Use after changing compose, qbit_manage config, canary/monitoring scripts, systemd units or arr-sweep, or when asked to prove the pipeline is delivering. Read-only by default.
---

# Verify the Plex stack

There is no UI to launch. The product is a **live, single-instance production
stack** on `yggdrasil` (`twoplustwoone@192.168.1.246`) that people are watching
right now. You verify it by reading its real APIs, state files, journals and
the Uptime Kuma database over SSH, and by running the repo's unit tests locally.
`AGENTS.md` is the authority on host facts; read it first if you have not.

**There is no isolation.** There is exactly one instance and it is the user's
household media server. Never start a second copy, and never restart, stop,
pull or recreate a container to "get a clean run". Everything below is
read-only unless a recipe says otherwise and the user asked for it.

## Hard limits

- No `docker stop|restart|compose up|down|pull`. `unless-stopped` means a manual stop survives reboot.
- No `--apply` on `pipeline-canary.py` or `arr-sweep.py`: it posts to Discord, pings Kuma and can stop torrents or blocklist releases. Use `--report` / the default dry run.
- No `sudo` (it cannot prompt over SSH). Root work goes to the user as a guarded script (see `scripts/prep-new-disk.sh`).
- Never print keys, tokens, cookies or `.env` values. Helpers resolve secrets on the host and print lengths or fields only. Check **length**, not appearance: an empty key still looks populated once masked.
- Do not post to Discord to "test delivery". Prove delivery from state (Kuma heartbeat rows, `.canary-*` files), not by sending.

## Launch

Nothing to start. The stack is already up and is brought up by Docker on boot.
"Ready" means the doctor below prints `DOCTOR OK`. Local code verification needs
only Python 3:

```bash
python3 -B -m unittest discover -s scripts -p 'test_*.py'   # from repo root; expect "OK"
```

Every verification run writes its proof under a fresh directory. `.local/` is
git-ignored and holds local operational artifacts (per `AGENTS.md`):

```bash
export VERIFY_RUN_DIR=.local/verify/$(date -u +%Y%m%dT%H%M%SZ)   # keep for the whole run
```

## Doctor

Run first, and again whenever anything looks off:

```bash
.claude/skills/verify/bin/doctor.sh      # writes $VERIFY_RUN_DIR/doctor.txt when set
```

It checks: host identity and UTC clock, `/mnt/library` UUID, 13 core containers
running and healthy, qBittorrent joined gluetun's namespace and started *after*
it, Plex on `192.168.1.246:32400`, arr/Prowlarr/Seerr APIs and zero health
errors, qBittorrent login and `max_ratio_act == 0`, canary timer active with a
run in the last 20 minutes, Kuma monitors, and whether the deployed canary
modules on the host match this checkout. Exit code is the number of `FAIL`s.
`WARN` lines (Kuma monitors down, deployed code differs) are real information,
not a reason to stop. Report them.

## Drive

Pick the recipe from `features/README.md`. The tools:

```bash
B=.claude/skills/verify/bin
$B/probe.sh <radarr|sonarr|prowlarr|seerr|tautulli|plex|qbit|kuma> <path[?query]> [jq-filter]
$B/canary-report.sh          # canary --report, then proves its state files did not change
```

`probe.sh` is GET-only, resolves the right credential on the host, and refuses
endpoints that return secrets (`/config`, `/indexer`, `/downloadclient`,
`/notification`, `/settings`, `/preferences`, `/auth`) unless you pass a jq
filter naming safe fields. Examples with real handles:

```bash
$B/probe.sh radarr '/api/v3/queue?pageSize=20' '[.records[]|{title,status}]'
$B/probe.sh seerr  /api/v1/request/count
$B/probe.sh qbit   /api/v2/torrents/info '[.[]|.state]|group_by(.)|map({(.[0]):length})|add'
$B/probe.sh plex   /library/sections '[.MediaContainer.Directory[]|.title]'
$B/probe.sh tautulli '?cmd=get_history&length=1' '.response.data.data[0]|{full_title,date,user}'
$B/probe.sh kuma   /monitors '[.[]|{name,status,time_utc}]'
```

For anything the helpers do not cover, use plain `ssh twoplustwoone@192.168.1.246 '<read-only command>'`.
Remember the topology rules: Plex is on host networking (`192.168.1.246:32400`),
most services are on the bridge, qBittorrent's API is `127.0.0.1:8080` on the
host because gluetun publishes it, and the host clock is UTC while qbit_manage
logs in `America/New_York`.

## Evidence

Save every proof under `$VERIFY_RUN_DIR/<feature-id>/`. Standards:

- **Real user path.** Probe the same APIs the apps and canary use. Do not call internal setters, edit state files, or hand-craft rows to make a check pass.
- **Action and resulting state.** Capture the command you ran *and* the output. A green `/health` alone proves liveness, not delivery.
- **Side effects.** For anything that should change something (a torrent stopped, a Discord post, a state file rewritten), verify it from a second source: Kuma heartbeat time, `journalctl -u pipeline-canary`, state-file mtime and hash, qBittorrent torrent state.
- **Mutation is the exception.** Creating a Seerr request or editing config changes the real library and downloads real files. Only do it if the user asked, record exactly what you changed, and undo it through the same path.
- **Dry-run names lie.** `canary-report.sh` earns trust by hashing state before and after; do the same for any other "dry run" you did not read the source of.
- **Report skips honestly.** If a path was unreachable, say what you tried and what was unmet. Never claim a feature verified through a different entry point.
- If the run finds a real problem, append a dated entry to `.local/pi-issue-history.md` (symptoms, checks, misleading findings, root cause or best theory).

## Cleanup

The run creates no instances, so there is nothing to kill. Scratch state is
confined to: the `$VERIFY_RUN_DIR` evidence directory (**keep it**), and
`mktemp` cookie jars that the helpers delete on exit. If you created anything
else on the host (a temp file, a test request), remove exactly that and name it
in your report. Never `pkill`, `docker rm`, or clear Kuma/canary state files.

## Helpers

| Script | Invocation | Purpose |
|---|---|---|
| `bin/doctor.sh` | `bin/doctor.sh` | read-only readiness check, exit = failure count |
| `bin/probe.sh` | `bin/probe.sh <svc> <path> [jq]` | authenticated GET against a service on the host |
| `bin/canary-report.sh` | `bin/canary-report.sh` | deployed canary in `--report` mode plus a no-mutation proof |

All three honour `VERIFY_HOST` (default `twoplustwoone@192.168.1.246`). They are
bash 3.2 compatible so they run on the Mac as-is.
