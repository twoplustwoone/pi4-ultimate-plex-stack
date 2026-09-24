#!/usr/bin/env python3
"""
pipeline-canary.py — detect a request pipeline that is silently doing nothing.

Liveness checks cannot catch "every service is green but no media is arriving",
which is exactly how a stale download-client address went unnoticed for seven
weeks. This inspects the pipeline end to end and reports only when something is
actually wrong. Silent when healthy. The same applies to torrent cleanup, whose
own heartbeat stays green even when it has stopped deleting anything.

  --report   print findings and exit (no Discord message)
  --apply    post to Discord if there are findings
"""
import http.cookiejar, json, sqlite3, subprocess, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone

REPORT_ONLY   = "--apply" not in sys.argv
STUCK_REQ_DAYS = 3     # approved but still not available
STUCK_QUEUE_HRS = 24   # sat in the download queue this long
NO_GRAB_DAYS   = 21    # nothing successfully grabbed at all
MIN_FREE_GB    = 250

# Torrent cleanup. These mirror qbit_manage/config/config.yml; keep them in step.
QBIT = "http://127.0.0.1:8080"
ENV_FILE = "/home/twoplustwoone/pi4-ultimate-plex-stack/.env"
PRIVATE_RATIO, PRIVATE_DAYS, PRIVATE_MIN_DAYS = 1.0, 15, 3
PUBLIC_DAYS = 3
HNR_DAYS = 14          # IPTorrents: 1:1 or 14 days, per torrent
CLEANUP_GRACE_DAYS = 1 # qbit_manage runs hourly; a day late means it has stalled
STOPPED_STATE = "/home/twoplustwoone/.canary-stopped-released.json"

# Dead-man switch: an Uptime Kuma push monitor, pinged on EVERY completed run
# whether healthy or not. It proves the canary itself is alive. Kuma raises the
# alarm when the heartbeat stops - the one failure this script cannot report
# about itself.
PUSH_URL = "http://127.0.0.1:3001/api/push/16EK9URVJDqa7NhX"
SUMMARY_STATE = "/home/twoplustwoone/.canary-last-summary"
SUMMARY_EVERY_DAYS = 7

def sh(*a): return subprocess.check_output(a).decode().strip()
_arr_key_cache = {}
def arr_key(svc):
    if svc not in _arr_key_cache:
        _arr_key_cache[svc] = sh("docker","exec",svc,"sed","-n",
                  r"s#.*<ApiKey>\(.*\)</ApiKey>.*#\1#p","/config/config.xml")
    return _arr_key_cache[svc]
def get(url, key, hdr="X-Api-Key"):
    r = urllib.request.Request(url, headers={hdr: key})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.load(resp)

def radarr_release_pending(movie_id):
    """True if Radarr shows this movie as not yet released (nothing to deliver)."""
    try:
        m = get("http://127.0.0.1:7878/api/v3/movie/%d" % movie_id, arr_key("radarr"))
        return m.get("status") != "released"
    except Exception:
        return False  # fail open: never suppress a real finding on a lookup error

def sonarr_season_unaired(series_id, season_numbers):
    """True if every requested season hasn't aired yet (nothing to deliver)."""
    try:
        s = get("http://127.0.0.1:8989/api/v3/series/%d" % series_id, arr_key("sonarr"))
        if s.get("status") == "upcoming":
            return True
        seasons = {se.get("seasonNumber"): se for se in s.get("seasons", [])}
        for num in season_numbers:
            se = seasons.get(num)
            if not se:
                continue
            stats = se.get("statistics", {}) or {}
            if stats.get("nextAiring") and stats.get("episodeFileCount", 0) == 0:
                continue  # this season still pending airing
            return False  # at least one requested season is airing/aired, not just pending
        return True
    except Exception:
        return False  # fail open

def age_days(ts):
    if not ts: return None
    ts = ts.replace("Z", "+00:00")
    try: d = datetime.fromisoformat(ts)
    except ValueError: return None
    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).total_seconds() / 86400

findings = []

# 1. health errors reported by the apps themselves
for svc, port, ver in (("radarr",7878,"v3"), ("sonarr",8989,"v3"), ("prowlarr",9696,"v1")):
    try:
        for h in get("http://127.0.0.1:%d/api/%s/health" % (port, ver), arr_key(svc)):
            if h.get("type") == "error":
                findings.append("%s health: %s" % (svc, h.get("message","")[:110]))
    except Exception as e:
        findings.append("%s unreachable: %s" % (svc, type(e).__name__))

# 2. nothing grabbed recently, and 3. queue items stuck
for svc, port in (("radarr",7878), ("sonarr",8989)):
    try:
        k = arr_key(svc)
        # eventType=1 is "grabbed". Filtering server-side matters: a burst of
        # imports can push every grab off the first page of unfiltered history.
        h = get("http://127.0.0.1:%d/api/v3/history?eventType=1&pageSize=5&sortKey=date&sortDirection=descending" % port, k)
        grabs = h.get("records", [])
        if grabs:
            d = age_days(grabs[0].get("date"))
            if d and d > NO_GRAB_DAYS:
                findings.append("%s: no successful grab in %d days" % (svc, d))
        else:
            findings.append("%s: no grabs in recent history at all" % svc)

        q = get("http://127.0.0.1:%d/api/v3/queue?pageSize=100" % port, k)
        for r in q.get("records", []):
            if r.get("errorMessage"):
                findings.append("%s queue: %s -- %s" % (svc, (r.get("title") or "?")[:44], r["errorMessage"][:60]))
            d = age_days(r.get("added"))
            if d and d * 24 > STUCK_QUEUE_HRS:
                findings.append("%s queue: '%s' stuck %.0fh" % (svc, (r.get("title") or "?")[:44], d*24))
    except Exception as e:
        findings.append("%s history/queue check failed: %s" % (svc, type(e).__name__))

# 4. Overseerr requests approved but never delivered -- the user-visible failure
try:
    okey = json.loads(sh("docker","exec","seerr","cat","/app/config/settings.json"))["main"]["apiKey"]
    reqs = get("http://127.0.0.1:5055/api/v1/request?take=100&sort=added", okey)
    for r in reqs.get("results", []):
        media = r.get("media") or {}
        if r.get("status") == 2 and media.get("status") in (2, 3):   # approved, still pending/processing
            d = age_days(r.get("createdAt"))
            if d and d > STUCK_REQ_DAYS:
                media_type = r.get("type")
                ext_id = media.get("externalServiceId")
                if media_type == "movie" and ext_id and radarr_release_pending(ext_id):
                    continue
                if media_type == "tv" and ext_id:
                    season_nums = [s.get("seasonNumber") for s in r.get("seasons", [])]
                    if sonarr_season_unaired(ext_id, season_nums):
                        continue
                who = (r.get("requestedBy") or {}).get("displayName", "?")
                findings.append("overseerr: request #%s (%s, by %s) approved but not available after %.0f days"
                                % (r.get("id"), r.get("type"), who, d))
except Exception as e:
    findings.append("overseerr request check failed: %s" % type(e).__name__)

# 5. disk headroom
try:
    out = sh("df","-B1","--output=avail","/mnt/library").splitlines()[-1]
    free_gb = int(out) / 1e9
    if free_gb < MIN_FREE_GB:
        findings.append("library free space low: %.0f GB" % free_gb)
except Exception:
    pass

# 6-8. torrents: seeding owed to IPTorrents is being earned, cleanup keeps pace,
# and the qBittorrent settings both depend on are still in place.
def qbit_opener():
    env = dict(l.strip().split("=", 1) for l in open(ENV_FILE) if "=" in l and not l.startswith("#"))
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    form = urllib.parse.urlencode({"username": env["QBITTORRENT_USER"].strip('"'),
                                   "password": env["QBITTORRENT_PASS"].strip('"')}).encode()
    req = urllib.request.Request(QBIT + "/api/v2/auth/login", data=form, headers={"Referer": QBIT})
    if op.open(req, timeout=30).read() != b"Ok.":
        raise RuntimeError("qBittorrent login refused")
    return op

def cleanup_due(t, slack_days=0):
    """qbit_manage's rule for a released torrent, optionally `slack_days` past it."""
    days = t["seeding_time"] / 86400 - slack_days
    if t.get("private"):
        return days >= PRIVATE_MIN_DAYS and (t["ratio"] >= PRIVATE_RATIO or days >= PRIVATE_DAYS)
    return days >= PUBLIC_DAYS

try:
    op = qbit_opener()
    qget = lambda path: json.load(op.open(QBIT + path, timeout=30))
    # With qBittorrent < 5.2 its global action overrides qbit_manage's "Stop":
    # "remove with files" deletes outside the recycle bin and min-seed check.
    act = qget("/api/v2/app/preferences").get("max_ratio_act")
    if act != 0:
        findings.append("qBittorrent share-limit action is %s, not 0 (Stop): cleanup bypasses the recycle bin" % act)
    torrents = qget("/api/v2/torrents/info")
    tags = {t["hash"]: {x.strip() for x in t["tags"].split(",")} for t in torrents}
    stopped = {"stoppedUP", "pausedUP"}

    queued = [t for t in torrents if t["state"] == "queuedUP"]
    if queued:
        findings.append("qBittorrent: %d torrents queued instead of seeding (queue limits back?)" % len(queued))
    # Queued, stopped or errored torrents don't announce, so owed seeding time stalls.
    owing = [t for t in torrents if t.get("private") and t["progress"] == 1
             and t["ratio"] < 1 and t["seeding_time"] < HNR_DAYS * 86400
             and t["state"] not in ("uploading", "stalledUP", "forcedUP", "queuedUP")]
    if owing:
        findings.append("%d private torrents still owe seeding but aren't seeding (e.g. %s: %s)"
                        % (len(owing), owing[0]["name"][:40], owing[0]["state"]))

    # A released torrent that qBittorrent stopped at its limit should be gone by
    # qbit_manage's next hourly run. Stopped torrents stop accruing seeding time,
    # so track when each was first seen stopped.
    now = time.time()
    try:
        first_seen = json.load(open(STOPPED_STATE))
    except (OSError, ValueError):
        first_seen = {}
    first_seen = {t["hash"]: first_seen.get(t["hash"], now) for t in torrents
                  if "released" in tags[t["hash"]] and t["state"] in stopped}
    if not REPORT_ONLY:
        json.dump(first_seen, open(STOPPED_STATE, "w"))
    overdue = [t for t in torrents if "released" in tags[t["hash"]] and (
        now - first_seen.get(t["hash"], now) > CLEANUP_GRACE_DAYS * 86400
        or (t["state"] not in stopped and cleanup_due(t, CLEANUP_GRACE_DAYS)))]
    if overdue:
        findings.append("torrent cleanup stalled: %d released torrents past their seeding rule (e.g. %s)"
                        % (len(overdue), overdue[0]["name"][:50]))
except Exception as e:
    findings.append("torrent checks failed: %s" % type(e).__name__)

def heartbeat(msg):
    """Tell Kuma the canary ran. A failure here must never mask the real result."""
    if REPORT_ONLY:
        return
    try:
        urllib.request.urlopen(
            PUSH_URL + "?status=up&msg=" + urllib.parse.quote(msg[:60]), timeout=15).read()
        print("heartbeat sent")
    except Exception as e:
        print("heartbeat FAILED: %s" % type(e).__name__)

def discord(msg):
    subprocess.run(["docker","cp","uptime-kuma:/app/data/kuma.db","/tmp/kuma_c.db"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hook = json.loads(sqlite3.connect("/tmp/kuma_c.db").execute(
        "SELECT config FROM notification WHERE id=1").fetchone()[0])["discordWebhookUrl"]
    subprocess.run(["rm","-f","/tmp/kuma_c.db"])
    req = urllib.request.Request(hook, data=json.dumps({"content": msg[:1900]}).encode(),
        headers={"Content-Type":"application/json","User-Agent":"pipeline-canary"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status

def maybe_weekly_summary(ok):
    """Periodic proof of life. Covers BOTH this script and Kuma failing at once -
    a human notices the weekly message stopped arriving."""
    if REPORT_ONLY:
        return
    import os, time
    try:
        last = os.path.getmtime(SUMMARY_STATE)
    except OSError:
        last = 0
    if (time.time() - last) < SUMMARY_EVERY_DAYS * 86400:
        return
    try:
        discord("Weekly check-in: pipeline canary is running. " +
                ("No issues found." if ok else "Issues reported separately."))
        open(SUMMARY_STATE, "w").write(str(time.time()))
        print("weekly summary sent")
    except Exception as e:
        print("weekly summary failed: %s" % type(e).__name__)

if not findings:
    print("pipeline OK - nothing to report")
    heartbeat("OK")
    maybe_weekly_summary(True)
    sys.exit(0)

msg = "**Plex pipeline check failed**\n" + "\n".join("- " + f for f in findings[:15])
if len(findings) > 15:
    msg += "\n- ...and %d more" % (len(findings) - 15)
print(msg)

if not REPORT_ONLY:
    print("\nposted to Discord: HTTP", discord(msg))
    heartbeat("%d issue(s)" % len(findings))
    maybe_weekly_summary(False)
