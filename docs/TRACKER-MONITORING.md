# Tracker monitoring and private cleanup hold

qbit_manage 4.13.0 is pinned by digest. It runs hourly, tags torrents without
a working real tracker `issue`, and clears the tag on recovery. Its native
webhooks remain disabled because payloads include tracker URLs/passkeys.
The pipeline canary consumes tags and supplements them with IPT-specific
tracker responses (another working tracker must not mask an IPT rejection).
The canary additionally attempts a read-only account-credit lookup using the
existing Prowlarr IPT connection. Authentication stays in memory. A redirect to
login, unsupported format, or incomplete inventory is a monitoring failure,
never completion evidence. This is a validated-format HTML reader, not an
official IPT API. The canary also executes a durable finite-seeding policy,
separately from file cleanup (details below).

Private torrents have unlimited share limits and `cleanup: false`, including
released/unlinked torrents. Public cleanup still requires `released`, `noHL`,
and its existing three-day rule. Recycle-bin expiry is suspended because it
already contains private data removed using unverified local seed counters.
This can increase disk usage; the canary's 250 GB free-space warning remains.
Do not re-enable private cleanup or recycle expiry until IPT obligations and
recycled data are reconciled. Working trackers alone never lift the hold.

The canary polls every 15 minutes after the guarded timer installation;
qbit_manage remains hourly. Account/passkey rejection alerts immediately when
observed. IPT's `unregistered torrent pass` response is an immediate **tracker
credential rejection**, not proof of an account-wide restriction. A bare word
`pass` does not classify a response as an authentication failure. Updating
trackers (API status 3) are unknown, not completed failed responses.
Temporary/unknown tracker failures need two distinct successful
qbit_manage scans. Recovery also needs two distinct working scans. Polls do
not count as scans. Missing torrents never count as recovery. Grouped alerts
include counts, UTC first-seen time and up to three titles. One opening alert
is sent per tracker incident; changing membership and additional noncritical
categories update the stored incident without generating more messages. An
explicit account/passkey failure may escalate a network incident immediately;
an explicit banned/disabled/jailed response may additionally escalate a prior
credential failure. Each escalation is limited to once per 24 hours. Unresolved
incidents remind once per 24 hours per tracker, not per torrent/category. Failed sends do not
acknowledge incidents. Raw URLs, credentials and exception bodies are omitted.
Recovery means at least one IPT endpoint works for each affected torrent;
other endpoints may still reject announcements. It does not prove credited
seed time or H&R clearance. Do not rotate a matching credential or delete
failing mirrors merely to make the monitor green.

Recovery checks every torrent observed during the incident, not just the most
recent failing subset. Missing torrents and unreadable APIs cannot establish
recovery. Verified, present, intentionally stopped torrents with matching
persisted obligation proof can resolve their part of an incident; the recovery
message explicitly distinguishes this from successful announcements.
Closed category history is retained for 24 hours after closure; recurrence
within that window reuses its original first-seen and cumulative membership.
Tracker-level delivery timestamps suppress repeated opening/recovery messages
within 24 hours, including across restarts. History is advanced on successful
Discord acknowledgement only; a failed send remains retryable. Report-only
loads may migrate old false labels in memory but never write them to disk.

State is `/home/twoplustwoone/.canary-tracker-health.json`, atomically replaced
with mode 0600. `--report` does not send, heartbeat or change state. First rollout
uses `--apply --initialize-state` once, after reviewing a report. Missing/corrupt
state on later runs is a monitoring failure: restore its backup instead of
silently resetting history. To acknowledge a deliberately removed private
torrent, back up state, then remove only that hash's entry from `incidents`;
never clear its outstanding tracker group as though it recovered.

The canary and release guard require a supported completed detection pass,
including its phase and command footer, without ERROR/CRITICAL/DRYRUN entries.
A scan over 90 minutes old fails health. Config/limit drift, an unexpected image,
API errors, or missing history are also failures. The canary's push-monitor
expiry is 45 minutes; failed alert delivery reports down and retries next run.
The release guard retains its separate heartbeat. Weekly Discord check-ins are
preserved. Discord/Kuma sharing a server/channel is a common failure point:
absence of the weekly check-in warrants manual inspection.

Accepted announcements are evidence of communication, not proof of credited
seed time, satisfied partial-download obligations, account eligibility, or
cleared hit-and-runs. Zero peers/upload traffic can be healthy. Current account
recovery and reconciliation remain separate operational work.

## Deployment and rollback

Run `python3 -B -m unittest discover -s scripts -p 'test_*.py'`.
Back up configuration, state and active script before deployment. Compare remote
Compose with the repository baseline before replacing it. Dry-run qbit_manage
with its own CLI (its QBT_RUN/QBT_LOGFILE environment overrides CLI flags; set
these explicitly for a one-shot test). Apply unlimited private limits before
starting normal runs. Verify a real tag scan, zero private removals, a grouped
Discord message, and both heartbeats. The actual systemd executable is the copy
at `/home/twoplustwoone/pipeline-canary.py`; deploy `tracker_health.py`,
`ipt_credit.py` and `torrent_retirement.py` beside it
as well as into repository scripts, and compare checksums.

Root timer installation, on yggdrasil only:

```sh
sudo bash /home/twoplustwoone/pi4-ultimate-plex-stack/scripts/install-tracker-monitoring-timer.sh
```

The same guarded script briefly restarts Kuma and backs up/updates only the
named canary push monitor, coupling its 45-minute expiry to the new timer.
It does not restart any media service.

Rollback must preserve `private_hold`, disabled unregistered removal, disabled
recycle purge and unlimited private torrent limits. Restore monitoring code and
its matching state together; never blindly restore the pre-rollout qbit_manage
configuration. Restore the timer drop-in backup if needed and reload systemd;
keep the 45-minute Kuma expiry only if the canary is running every 15 minutes.
No Plex, Gluetun or qBittorrent restart is required.

## Account-credit integration and retirement acceptance gates

The logged-in current Rules panel and linked topic 100481 confirm per-torrent
ratio 1:1 OR 336 hours of credited seed time. The detailed topic limits the
time exemption to fully downloaded torrents. IPT says its updates lag the
client by roughly 30 minutes, and describes a minimum 60-minute pause/check
before removal. Do not replace credited time with wall time or qBittorrent's
local timers. The overall account-ratio requirement is independent.

`scripts/ipt_credit.py` reads `/seeding_required.php` through the existing
Prowlarr indexer's cookie and user agent on the validated canonical HTTPS
origin. It validates authenticated-page markers, exact column headings,
record count, unique torrent IDs, amounts and credited hours. Redirects to
other origins refuse to forward credentials. Errors contain fixed categories,
not response bodies. Login failures use existing change/daily Discord delivery
and down heartbeat behavior. Report mode remains read-only.

The live account page on October 10 had 184 explicit credit records; its peers
page showed 257 seeds, 120 remaining-time countdowns and 137 blank time fields.
Blank/missing records are UNKNOWN, not completed. VIP/donation purging can make
records disappear independently of seeding completion. The server's saved
Prowlarr API cookie was a masked placeholder, not the actual credential.
The reader now resolves that one cookie from Prowlarr's read-only live database
and authenticated account reads work without a credential change. IPT source
omits table end tags and appends CSS-hidden last-seen tooltips; both formats are
covered by regression tests.

Exact hash-linked grab history maps 256 of 257 live IPT torrents. The canary
reads bounded paginated arr history and holds ambiguous/missing identities;
it does not match titles. It persists per-hash credited counters in the existing
atomic monitoring state under `ipt_credit`. Missing records and regressed
counters alert. After two hours of observed healthy active completed seeding,
an unchanged unsatisfied credit counter alerts independently of tracker
liveness. Failed/stale tracker scans do not accrue that healthy window.
This makes successful announcements with stalled credit visible too.

## Finite seeding rollout (October 10)

IPT's system conversation `/inbox/1015696` confirms a donation at
2026-10-10 15:22:25 UTC and removal of all current H&Rs/warning history within an
hour. The user explicitly accepted that waiver as satisfying obligations for
existing torrents, followed by a new 24-hour healthy-seeding buffer. At recording,
the authenticated account's H&R inventory was verified empty.

`record-ipt-waiver.py` recorded 257 exact completed pre-donation torrents in the
existing mode-0600 monitoring state. The immutable snapshot includes each hash,
added timestamp and completion timestamp, the cutoff, account identity, source
conversation and approval timestamp. New, incomplete or re-added incarnations
cannot inherit it. Re-running the recorder refuses to overwrite a waiver.
An exact hash waiver also covers the retained Odyssey torrent whose arr history
no longer provides an IPT ID; titles are not used as identity evidence.

`torrent_retirement.py` implements the policy:

- A full extra 24 hours is observed AFTER positive completion evidence or the
  accepted waiver. Previously accumulated local seed time cannot shortcut it.
- Each interval requires complete active seed state, a working IPT response,
  readable account APIs, valid configuration and a successful qbit_manage scan
  no older than 90 minutes. A failure or observation gap over 30 minutes resets
  the buffer; at least two distinct scans are required. Zero peers is healthy.
- This optional buffer uses consecutive observed healthy elapsed intervals,
  not IPT-earned credit or upload traffic. Most live idle torrents' `/info`
  `seeding_time` values remained unchanged; requiring those to advance would
  leave seeds running indefinitely. Local counter regression still holds and
  alerts. Never repurpose the buffer as proof of the 336-hour obligation.
- Before POST `/api/v2/torrents/stop`, persist proof and exact stop intent.
  HTTP success does not establish completion: an observed stopped state does.
  Pending intent survives asynchronous stopping, failed requests and restarts.
  Normal asynchronous confirmation has a 30-minute grace period; retries never
  reset its persisted start time. Overdue or corrupt intents alert even if a
  fresh stop retry is held. API failures still report immediately. A fresh
  accepted stop alone does not mark the canary heartbeat down.
  Only persisted, proven intentional stops are exempt from seeding-failure
  alerts. Unexpected resumes alert and are stopped again when monitoring is
  healthy. No API deletion or file operation is part of retirement.
- Public complete torrents stop at the existing three-day local target,
  independently of whether files remain linked into the library. Existing
  public cleanup still separately requires `released` and `noHL`.
- Every qbit_manage share-limit group explicitly has
  `resume_torrent_after_change: false`; drift is a monitoring error. Private
  limits remain unlimited and private cleanup/recycle expiry remain held.
- Confirmed stops produce grouped Discord notifications, acknowledged only
  after successful delivery. Account ratio below 1.0 is a separate daily/change
  finding, not proof of a dead monitor. Exact profile totals are read in memory;
  the profile's credential section is never emitted or persisted.

Verified on the real deployment: 12 public torrents stopped, all 35 selected
files still present, all 12 notification acknowledgements recorded, both Kuma
heartbeats up, all 257 private torrents actively seeding with unlimited limits,
all 257 buffers advancing, and no config drift. 56 focused tests pass locally
and the server test suite is deployed. Actual private retirement still awaits
the real 24-hour buffer and verification; do not accelerate it for a test.

### Remaining acceptance gates — goal is not complete yet

1. Verify the live timer accrues the private buffer across scheduler runs and
   completes real private stops after 24 healthy hours, with files retained,
   successful Discord delivery and no restart by qbit_manage/arr.
2. New downloads DO NOT inherit the accepted waiver. Positive ratio/time
   records are supported, but blank/disappearing credit cannot be assumed to
   mean earned completion. IPT's documented pause/wait-at-least-60-minutes/H&R
   check is a possible authoritative verification workflow; it is not yet
   implemented. During VIP, list absence cannot prove earned quota. Establish
   and test a reliable finite completion path for future downloads rather than
   claiming the current waiver solves them. Unknown evidence alerts and holds.
3. Other private trackers require verified tracker-specific rules; do not apply
   public or IPT rules to them. Private cleanup remains a separate reconciliation
   decision; stopping seeds is not permission to delete their files.

### Retirement rollback

Server backups are under `.local/retirement-rollout-20261010T161200Z/`; waiver
recording and the public-confirmation repair also made separate state backups.
Disable `retirement.enabled` under the same state lock to prevent further stops;
do not erase the waiver, existing proof, pending intent or notification history.
Do not automatically resume already-retired torrents. Restore matching modules
and canary copies together only after reconciling persisted intentional stops.
Keep private_hold, no-auto-resume, unlimited private limits, disabled destructive
removal and recycle preservation. No media-service restart is required.
