# Plex stack verification map

Maintained source for verifying what the household actually gets from the stack
on `yggdrasil`. Read this index, run the doctor, then follow the matching
feature file as a recipe. See `../SKILL.md` for the helpers and hard limits.

## Baseline preconditions

- `.claude/skills/verify/bin/doctor.sh` prints `DOCTOR OK` (no `FAIL` lines).
- `export VERIFY_RUN_DIR=.local/verify/$(date -u +%Y%m%dT%H%M%SZ)` is set, and each recipe saves to `$VERIFY_RUN_DIR/<feature-id>/`.
- There is one live instance and no isolation. Every recipe here is read-only unless it says **MUTATES**.
- Time zones: host and Kuma are UTC; qbit_manage and `arr-sweep.timer` use `America/New_York`.

## Driving conventions

- `B=.claude/skills/verify/bin`; use `$B/probe.sh` for service APIs and `ssh twoplustwoone@192.168.1.246` for journals and files.
- Prefer API fields and state files over scraping logs. Quote hashes and counts, never keys.
- A "healthy" container is not proof of delivery. Always finish with the end-state check in the recipe.
- Existing real findings are not regressions you introduced. Diff against the doctor's WARNs from the start of the run and say which is which.

## Proof and skip reporting

- Save the command and its output for each step, with the feature ID and UTC time.
- State-changing claims need a second view (Kuma row, journal line, file hash, torrent state).
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Features

- [Torrent cleanup](./torrent-cleanup.md): qbit_manage and the release guard remove only what is released, and never bypass the recycle bin. Driven end to end on 2026-10-20.

Other areas (request pipeline, canary and alerting, Plex and Tautulli, scheduled jobs) have no recipe yet. For those, use `doctor.sh`, `probe.sh` and `canary-report.sh` directly, and add a recipe here once you have run it for real.
