#!/usr/bin/env python3
"""
setup-alerting.py — wire Radarr/Sonarr/Prowlarr health alerts to the existing
Discord webhook already used by Uptime Kuma.

Rationale: these apps compute the right diagnosis (e.g. "All download clients are
unavailable due to failures") but had no notification connection, so a seven-week
outage went unreported. Idempotent; dry run unless --apply.
"""
import json, sqlite3, subprocess, sys, urllib.request

APPLY = "--apply" in sys.argv
NAME = "Discord - Health Alerts"

# reuse the webhook Uptime Kuma already uses
subprocess.run(["docker","cp","uptime-kuma:/app/data/kuma.db","/tmp/kuma.db"], check=True,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
cfg = json.loads(sqlite3.connect("/tmp/kuma.db").execute(
    "SELECT config FROM notification WHERE id=1").fetchone()[0])
WEBHOOK = cfg["discordWebhookUrl"]
subprocess.run(["rm","-f","/tmp/kuma.db"])

SERVICES = [("radarr",7878,"v3"), ("sonarr",8989,"v3"), ("prowlarr",9696,"v1")]

def key(svc):
    return subprocess.check_output(["docker","exec",svc,"sed","-n",
        r"s#.*<ApiKey>\(.*\)</ApiKey>.*#\1#p","/config/config.xml"]).decode().strip()

for svc, port, ver in SERVICES:
    base, k = "http://127.0.0.1:%d" % port, key(svc)
    def req(path, method="GET", body=None):
        r = urllib.request.Request(base+path, method=method,
            headers={"X-Api-Key": k, "Content-Type": "application/json"},
            data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else None)

    _, existing = req("/api/%s/notification" % ver)
    if any(n["name"] == NAME for n in existing):
        print("  %-9s already configured" % svc); continue

    # start from the server's own schema so required fields/contracts are correct
    _, schema = req("/api/%s/notification/schema" % ver)
    tpl = next(x for x in schema if x.get("implementation") == "Discord")
    tpl["name"] = NAME
    for f in tpl.get("fields", []):
        if f["name"] == "webHookUrl": f["value"] = WEBHOOK
        if f["name"] == "username":   f["value"] = svc

    # alert on things that mean the pipeline is broken; stay quiet otherwise
    wanted = {
        "onHealthIssue": True,          # the one that would have caught the outage
        "onHealthRestored": True,
        "onApplicationUpdate": False,
        "onGrab": False,                # too chatty for a shared channel
        "onDownload": False,
        "onUpgrade": False,
        "onRename": False,
        "onMovieDelete": False, "onMovieFileDelete": False, "onMovieAdded": False,
        "onSeriesDelete": False, "onEpisodeFileDelete": False,
        "onImportFailure": True,
        "onDownloadFailure": True,
        "onManualInteractionRequired": True,
        "includeHealthWarnings": False, # errors only, not warnings
    }
    for kk, vv in wanted.items():
        if kk in tpl and tpl.get("supports" + kk[0].upper() + kk[1:]) is not False:
            tpl[kk] = vv
        elif kk in tpl:
            tpl[kk] = False

    if APPLY:
        st, _ = req("/api/%s/notification" % ver, "POST", tpl)
        print("  %-9s created (HTTP %s)" % (svc, st))
    else:
        on = [x for x in wanted if wanted[x] and tpl.get(x)]
        print("  %-9s would create %r -> %s" % (svc, NAME, ", ".join(on)))

print("\nDRY RUN — re-run with --apply" if not APPLY else "\nAPPLIED")
