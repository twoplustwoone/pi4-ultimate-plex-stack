#!/usr/bin/env python3
"""
reseed-hardlink.py — restore seeding for torrents whose files Sonarr/Radarr moved.

Rebuilds the expected download-folder layout using HARDLINKS to the already-imported
library files, then rechecks the torrents. Hardlinks consume no extra space and do
not modify the library copies. Safe to re-run.

Dry run by default; pass --apply to create links and recheck.
"""
import json, os, sqlite3, subprocess, sys, urllib.request, urllib.parse, http.cookiejar

APPLY = "--apply" in sys.argv
HOST_DOWNLOADS = "/mnt/library/downloads"
LIB_ROOTS = ["/mnt/library/tv", "/mnt/library/movies"]
CONTAINER_PREFIX = "/share/downloads/"

subprocess.run(["docker","cp","radarr:/config/radarr.db","/tmp/radarr.db"], check=True,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
s = json.loads(sqlite3.connect("/tmp/radarr.db").execute(
    "SELECT Settings FROM DownloadClients LIMIT 1").fetchone()[0])
base = "http://127.0.0.1:%s" % s.get("port")
cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.open(urllib.request.Request(base+"/api/v2/auth/login",
    data=urllib.parse.urlencode({"username": s.get("username"),"password": s.get("password")}).encode(),
    headers={"Referer": base}), timeout=20).read()

def get(path):
    return json.load(op.open(base + path, timeout=60))
def post(path, data):
    r = urllib.request.Request(base+path, data=urllib.parse.urlencode(data).encode(),
                               headers={"Referer": base})
    return op.open(r, timeout=60).status

# index library files by exact size
size_index = {}
for root in LIB_ROOTS:
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try: size_index.setdefault(os.path.getsize(p), []).append(p)
            except OSError: pass

broken = [t for t in get("/api/v2/torrents/info?filter=errored")]
print("torrents in error state: %d\n" % len(broken))

to_recheck, linked_total, skipped_total = [], 0, 0
for t in broken:
    print("== %s" % t["name"][:66])
    files = get("/api/v2/torrents/files?hash=%s" % t["hash"])
    linked = skipped = present = 0
    for f in files:
        rel = f["name"]                       # path relative to save_path
        dest = os.path.join(HOST_DOWNLOADS, rel)
        if os.path.exists(dest):
            present += 1
            continue
        cands = size_index.get(f["size"], [])
        if len(cands) != 1:
            skipped += 1
            if f["size"] > 10_000_000:        # only shout about real media files
                print("   SKIP (%d candidates, %.2f GB) %s" % (len(cands), f["size"]/1e9, os.path.basename(rel)[:44]))
            continue
        if APPLY:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                os.link(cands[0], dest)
                linked += 1
            except OSError as e:
                print("   LINK FAILED %s: %s" % (os.path.basename(rel)[:40], e)); skipped += 1
        else:
            linked += 1
    print("   would link=%d  already present=%d  skipped=%d" % (linked, present, skipped))
    linked_total += linked; skipped_total += skipped
    if linked: to_recheck.append(t["hash"])

print("\ntotal: link=%d skipped=%d" % (linked_total, skipped_total))
if APPLY and to_recheck:
    print("recheck ->", post("/api/v2/torrents/recheck", {"hashes": "|".join(to_recheck)}))
elif not APPLY:
    print("DRY RUN — re-run with --apply")
