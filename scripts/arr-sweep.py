#!/usr/bin/env python3
"""
arr-sweep.py — re-search missing media and clear dead pending releases.

Reads Sonarr/Radarr over HTTP, asks arr_sweep.py what to do, and logs one
DECISION line per action so `journalctl -u arr-sweep` explains every search
and blocklisting.

  (default)  dry run: print decisions, change nothing, no heartbeat
  --apply    execute decisions and heartbeat Uptime Kuma
"""
import fcntl, json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from arr_sweep import RemovePendingAndSearch, plan, radarr_snapshot, sonarr_snapshot

APPLY = "--apply" in sys.argv
LOCK_PATH = os.environ.get("ARR_SWEEP_LOCK", "/home/twoplustwoone/.arr-sweep.lock")
PUSH_URL = "http://127.0.0.1:3001/api/push/aSw33pHb7xQ2kLm9"
PORTS = {"sonarr": 8989, "radarr": 7878}
REMOVE_PARAMS = "removeFromClient=true&blocklist=true&skipRedownload=true"
SEARCH_PAUSE_SECONDS = 5

_keys = {}
def api_key(svc):
    if svc not in _keys:
        _keys[svc] = subprocess.check_output(["docker", "exec", svc, "sed", "-n",
            r"s#.*<ApiKey>\(.*\)</ApiKey>.*#\1#p", "/config/config.xml"], stderr=subprocess.DEVNULL).decode().strip()
        if not _keys[svc]:
            raise RuntimeError("%s API key missing" % svc)
    return _keys[svc]

def request(svc, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORTS[svc], path), data=data, method=method,
                                 headers={"X-Api-Key": api_key(svc), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)

def get(svc, path):
    return request(svc, "GET", path)[1]

def records(svc, path, complete):
    page = get(svc, path)
    if complete and page["totalRecords"] > len(page["records"]):
        raise RuntimeError("%s %s exceeds one page" % (svc, path.split("?")[0]))
    return page["records"]

def sonarr():
    queue = records("sonarr", "/api/v3/queue?pageSize=1000&includeSeries=true", complete=True)
    # Sorted oldest-searched first, so a truncated page still holds everything the budget can use.
    missing = records("sonarr", "/api/v3/wanted/missing?monitored=true&sortKey=episodes.lastSearchTime"
                      "&sortDirection=ascending&pageSize=1000&includeSeries=true", complete=False)
    episodes = {s: get("sonarr", "/api/v3/episode?seriesId=%d" % s) for s in {r["seriesId"] for r in missing}}
    return sonarr_snapshot(queue, missing, episodes)

def radarr():
    queue = records("radarr", "/api/v3/queue?pageSize=1000&includeMovie=true", complete=True)
    missing = records("radarr", "/api/v3/wanted/missing?monitored=true&sortKey=movies.lastSearchTime"
                      "&sortDirection=ascending&pageSize=1000", complete=False)
    return radarr_snapshot(queue, missing)

def status_of(svc, method, path, body=None):
    try:
        return request(svc, method, path, body)[0]
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return "error:" + type(e).__name__

def describe(d):
    action = type(d.action).__name__
    detail = ({"queue_ids": list(d.action.queue_ids), "command": d.action.search_command}
              if isinstance(d.action, RemovePendingAndSearch) else {"command": d.action.command})
    return 'app=%s rule=%s subject="%s" action=%s reason="%s" detail=%s' % (
        d.app, d.rule, d.subject.replace('"', "'"), action, d.reason.replace('"', "'"),
        json.dumps(detail, separators=(",", ":")))

def execute(d):
    """Returns (ok, status text)."""
    if isinstance(d.action, RemovePendingAndSearch):
        # Removing one record of a pending season pack can drop the whole release,
        # so its sibling records may already be gone (404).
        removed = [status_of(d.app, "DELETE", "/api/v3/queue/%d?%s" % (i, REMOVE_PARAMS))
                   for i in d.action.queue_ids]
        remove_text = "remove=" + ",".join(map(str, removed))
        if 200 not in removed or any(s not in (200, 404) for s in removed):
            return False, remove_text + " search=skipped"
        searched = status_of(d.app, "POST", "/api/v3/command", d.action.search_command)
        return searched in (200, 201), "%s search=%s" % (remove_text, searched)
    searched = status_of(d.app, "POST", "/api/v3/command", d.action.command)
    return searched in (200, 201), str(searched)

def heartbeat(status, msg):
    try:
        urllib.request.urlopen(PUSH_URL + "?status=%s&msg=%s" % (status, urllib.parse.quote(msg[:100])),
                               timeout=15).read()
        print("heartbeat sent status=%s" % status)
    except Exception as e:
        print("heartbeat FAILED: %s" % type(e).__name__)

def sweep():
    now = datetime.now(timezone.utc)
    counts = {"stuck_pending": 0, "missing_search": 0}
    deferred = executed = failed = 0
    app_errors = []
    for app, fetch in (("sonarr", sonarr), ("radarr", radarr)):
        try:
            result = plan(fetch(), now)
        except Exception as e:
            app_errors.append(app)
            print("ERROR app=%s fetch failed: %s" % (app, type(e).__name__))
            continue
        deferred += result.deferred
        for d in result.decisions:
            counts[d.rule] += 1
            print("DECISION " + describe(d), flush=True)
            if not APPLY:
                continue
            if executed:
                time.sleep(SEARCH_PAUSE_SECONDS)
            ok, text = execute(d)
            executed += 1
            failed += not ok
            print("RESULT %s status=%s" % (describe(d), text), flush=True)
    summary = "decisions=%d stuck_pending=%d missing_search=%d deferred=%d executed=%d failed=%d app_errors=%s" % (
        sum(counts.values()), counts["stuck_pending"], counts["missing_search"], deferred, executed, failed,
        ",".join(app_errors) or "none")
    print("SUMMARY mode=%s %s" % ("apply" if APPLY else "dry-run", summary))
    return not (failed or app_errors), summary

def main():
    lock = open(LOCK_PATH, "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another arr-sweep run is active")
        return 0
    try:
        ok, summary = sweep()
    except Exception as e:
        print("ERROR unhandled: %s" % type(e).__name__)
        if APPLY:
            heartbeat("down", "arr-sweep crashed: " + type(e).__name__)
        raise
    if APPLY:
        heartbeat("up" if ok else "down", summary)
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
