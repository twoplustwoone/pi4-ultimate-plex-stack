# Torrent cleanup

qbit_manage (hourly) tags torrents and removes public ones only once their library copy is gone and the seeding rule is met; `torrent-release-guard` decides what counts as `released`. Private-tracker torrents are held: unlimited limits, no cleanup, no recycle-bin expiry.

## Sub-features

- `clean-global-action` qBittorrent's global share-limit action is Stop (`max_ratio_act == 0`).
- `clean-run` qbit_manage ran in the last hour with its configured commands and no errors.
- `clean-guard` the release guard is running and heartbeating.
- `clean-private-hold` private torrents keep unlimited limits and none was removed.
- `clean-tags` torrents carry `released` / `arr-wanted` / `noHL` / `issue` tags.

## How to get to it (user POV)

- qBittorrent web UI through gluetun at `http://192.168.1.246:8080` (torrent list, tags sidebar).
- Dozzle (container logs) for `qbit_manage` and `torrent-release-guard`.
- The Discord channel that receives Kuma and canary alerts.

## Driving it with probe.sh and ssh

Preconditions: `doctor.sh` shows qBittorrent login ok and `max_ratio_act` Stop. Read-only.

- **Global action.** Run `$B/probe.sh qbit /api/v2/app/preferences '{max_ratio_act,max_ratio_enabled}'`. `max_ratio_act` is `0`. Any other value deletes torrents outright, skipping the recycle bin and the minimum-seeding check (qBittorrent below 5.2).
- **Tags.** Run `$B/probe.sh qbit /api/v2/torrents/info '[.[]|.tags]|map(split(", ")[])|group_by(.)|map({(.[0]):length})|add'`. Expect `private`/`public`, `IPT`, `released`, `noHL`, `issue` and `~share_limit_*` group tags; `arr-wanted` appears only while something is arr-wanted (it was absent at 2026-10-20). `released` equals the guard's `released N` figure; `~share_limit_0.private_hold` equals `private`.
- **Last qbit_manage run.** Run `ssh twoplustwoone@192.168.1.246 'docker exec qbit_manage sh -c "grep -E \"Starting Run|Finished Run|Executed Commands\" /config/logs/qbit_manage.log | tail -3"'`. A `Finished Run` within the last hour (log is America/New_York), and `Executed Commands` lists `tag_update, tag_tracker_error, tag_nohardlinks, share_limits`.
- **Run errors.** Run `ssh twoplustwoone@192.168.1.246 'docker exec qbit_manage sh -c "tail -n 400 /config/logs/qbit_manage.log" | grep -cE "\[ERROR\]|\[CRITICAL\]|Traceback"'`. Count is `0` for the latest run.
- **Guard.** Run `ssh twoplustwoone@192.168.1.246 'docker logs --tail 3 torrent-release-guard 2>&1 | cut -c1-120'`. A `released N, kept M` line within the last 30 minutes (same New_York clock).
- **Guard heartbeat.** Run `$B/probe.sh kuma /monitors '.[]|select(.name=="torrent cleanup heartbeat")|{status,time_utc,msg}'`. `status` is `1` and `time_utc` is within the last 30 minutes. `status 0` has two readings: the guard fail-closed and reported down (msg names the cause, e.g. `qbit_manage successful tracker scan over 90m old`), or Kuma itself marked it down (msg `No heartbeat in the time window`, meaning no push arrived within the monitor's window). Report which.
- **Whole-pipeline proof.** Run `$B/canary-report.sh`. No `qBittorrent share-limit action`, `torrent cleanup stalled` or `queued instead of seeding` lines. Also grep for `tracker scan`: a `successful tracker scan is over 90 minutes old` line means the canary is not accepting any recent qbit_manage run, even if `Finished Run` is recent.
- **Private hold (config).** Run `ssh twoplustwoone@192.168.1.246 'cd ~/yggdrasil && git diff --stat -- qbit_manage/config/config.yml'` and read the file; private groups keep `cleanup: false` and unlimited limits. Do not edit it.

## Gotchas

- qBittorrent's `seeding_time` counts only active seeding, not time spent queued.
- Deleting a torrent leaves behind anything unpackerr extracted beside it (for example an `.iso` from a RAR'd disc release).
- qbit_manage's log and `Starting Run` timestamps are America/New_York; the host and the canary compare in UTC. Convert before judging "over 90 minutes".
- A completed qbit_manage run is not the same as a *supported completed tracker scan*; the canary requires the latter. They can diverge, and the doctor's Kuma WARN will say so.
- Never verify cleanup by deleting or releasing a real torrent.
