#!/usr/bin/env python3
"""
pipeline-canary.py — detect a request pipeline that is silently doing nothing.

Liveness checks cannot catch "every service is green but no media is arriving",
which is exactly how a stale download-client address went unnoticed for seven
weeks. This inspects the pipeline end to end and reports only when something is
actually wrong. Silent when healthy.

  --report   print findings and exit (no Discord message)
  --apply    post to Discord if there are findings
"""
import json, sqlite3, subprocess, sys, urllib.request, urllib.parse
from datetime import datetime, timezone

REPORT_ONLY   = "--apply" not in sys.argv
STUCK_REQ_DAYS = 3     # approved but still not available
STUCK_QUEUE_HRS = 24   # sat in the download queue this long
NO_GRAB_DAYS   = 21    # nothing successfully grabbed at all
MIN_FREE_GB    = 250

def sh(*a): return subprocess.check_output(a).decode().strip()
def arr_key(svc):
    return sh("docker","exec",svc,"sed","-n",
              r"s#.*<ApiKey>\(.*\)</ApiKey>.*#\1#p","/config/config.xml")
def get(url, key, hdr="X-Api-Key"):
    r = urllib.request.Request(url, headers={hdr: key})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.load(resp)
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
    okey = json.loads(sh("docker","exec","overseerr","cat","/app/config/settings.json"))["main"]["apiKey"]
    reqs = get("http://127.0.0.1:5055/api/v1/request?take=100&sort=added", okey)
    for r in reqs.get("results", []):
        media = r.get("media") or {}
        if r.get("status") == 2 and media.get("status") in (2, 3):   # approved, still pending/processing
            d = age_days(r.get("createdAt"))
            if d and d > STUCK_REQ_DAYS:
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

if not findings:
    print("pipeline OK - nothing to report")
    sys.exit(0)

msg = "**Plex pipeline check failed**\n" + "\n".join("- " + f for f in findings[:15])
if len(findings) > 15:
    msg += "\n- ...and %d more" % (len(findings) - 15)
print(msg)

if not REPORT_ONLY:
    subprocess.run(["docker","cp","uptime-kuma:/app/data/kuma.db","/tmp/kuma_c.db"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hook = json.loads(sqlite3.connect("/tmp/kuma_c.db").execute(
        "SELECT config FROM notification WHERE id=1").fetchone()[0])["discordWebhookUrl"]
    subprocess.run(["rm","-f","/tmp/kuma_c.db"])
    req = urllib.request.Request(hook, data=json.dumps({"content": msg[:1900]}).encode(),
        headers={"Content-Type":"application/json","User-Agent":"pipeline-canary"})
    with urllib.request.urlopen(req, timeout=20) as r:
        print("\nposted to Discord: HTTP", r.status)
