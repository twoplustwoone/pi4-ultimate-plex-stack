# Agent Instructions

## Local Operational Artifacts

Keep local-only operational artifacts under `.local/`. This includes incident
history, media inventories, deletion-candidate lists, diagnostic captures, and
other machine-specific working data. The directory is ignored by git; do not
commit files from it.

## Pi Operations Log

When diagnosing or fixing issues on the Raspberry Pi Plex stack, keep a running
local incident log in `.local/pi-issue-history.md`.

For each issue, append a dated entry with:

- observed symptoms and user-reported problem
- commands or checks run, summarized without secrets
- root cause or best current theory
- actions taken
- follow-up work or hardware checks needed

The log is intentionally local-only. Do not include tokens, passwords, private
keys, or full `.env` contents in it.
