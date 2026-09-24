#!/usr/bin/env python3
"""
torrent-release-guard.py - decide which unlinked torrents qbit_manage may delete.

qbit_manage tags a torrent noHL when none of its files is linked into the
library. That covers titles deleted in Seerr or Plex and titles Radarr/Sonarr
have since upgraded, but also downloads that never imported, where the torrent
holds the only copy. qbit_manage cleans only torrents tagged `released`, and
this guard applies that tag:

    the arr monitors the title and has no file for it  -> arr-wanted (keep)
    deleted, unmonitored, or upgraded                   -> released

It fails closed: if qBittorrent or an arr can't be read, nothing is tagged.
Every run pings an Uptime Kuma push monitor, reporting down when this guard
fails or qbit_manage's log goes stale, so one monitor watches both.
"""
import http.cookiejar, json, os, time, traceback, urllib.parse, urllib.request

QBIT = "http://" + os.environ.get("QBIT_HOST", "gluetun:8080")
ARR = {"radarr": ("http://radarr:7878", os.environ["RADARR_KEY"]),
       "sonarr": ("http://sonarr:8989", os.environ["SONARR_KEY"])}
HEARTBEAT = os.environ.get("HEARTBEAT_URL", "")
QBM_LOG = "/qbm-logs/qbit_manage.log"
QBM_STALE = 3 * 3600  # qbit_manage runs hourly
INTERVAL = 1800
RELEASED, WANTED = "released", "arr-wanted"

opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def qbit(path, **form):
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request(QBIT + path, data=data, headers={"Referer": QBIT})
    with opener.open(req, timeout=30) as r:
        body = r.read().decode()
    return json.loads(body) if body[:1] in ("[", "{") else body

def arr(app, path):
    base, key = ARR[app]
    req = urllib.request.Request(base + "/api/v3/" + path, headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def linked_ids(app, torrent, field):
    """Titles tied to this torrent: its grab/import history by hash (exact), plus
    the arr's parse of its name (covers a grab whose history was pruned). Either
    alone can miss; a miss would read as "not wanted" and release it."""
    hist = arr(app, "history?pageSize=250&downloadId=" + torrent["hash"].upper())
    ids = {r[field] for r in hist["records"] if r.get(field)}
    parsed = arr(app, "parse?title=" + urllib.parse.quote(torrent["name"]))
    if app == "radarr" and parsed.get("movie"):
        ids.add(parsed["movie"]["id"])
    ids.update(e["id"] for e in parsed.get("episodes") or [])
    return ids

def is_wanted(torrent, movies):
    """True/False, or None for a category neither arr owns (left alone)."""
    if torrent["category"] == "radarr":
        ms = [movies[i] for i in linked_ids("radarr", torrent, "movieId") if i in movies]
        return any(m["monitored"] and not m["hasFile"] for m in ms)
    if torrent["category"] == "sonarr":
        ids = linked_ids("sonarr", torrent, "episodeId")
        eps = arr("sonarr", "episode?" + "&".join("episodeIds=%d" % i for i in ids)) if ids else []
        return any(e["monitored"] and not e["hasFile"] for e in eps)
    return None

def run():
    if qbit("/api/v2/auth/login", username=os.environ["QBIT_USER"],
            password=os.environ["QBIT_PASS"]) != "Ok.":
        raise RuntimeError("qBittorrent login refused")
    movies = {m["id"]: m for m in arr("radarr", "movie")}
    release, hold, clear = [], [], []
    for t in qbit("/api/v2/torrents/info"):
        tags = {x.strip() for x in t["tags"].split(",")}
        if "noHL" not in tags:
            if tags & {RELEASED, WANTED}:
                clear.append(t["hash"])
            continue
        wanted = is_wanted(t, movies)
        if wanted is True:
            hold.append(t)
        elif wanted is False:
            release.append(t)
    # Decide everything before changing anything, so a failure mid-scan
    # leaves the tags as they were.
    def tag(action, torrents_or_hashes, tags):
        hashes = [x if isinstance(x, str) else x["hash"] for x in torrents_or_hashes]
        if hashes:
            qbit("/api/v2/torrents/" + action, hashes="|".join(hashes), tags=tags)
    tag("addTags", release, RELEASED)
    tag("removeTags", release, WANTED)
    tag("addTags", hold, WANTED)
    tag("removeTags", hold, RELEASED)
    tag("removeTags", clear, RELEASED + "," + WANTED)
    for t in hold:
        print("keep (arr still wants it): %s" % t["name"], flush=True)
    return "released %d, kept %d" % (len(release), len(hold))

def heartbeat(status, msg):
    if not HEARTBEAT:
        return
    try:
        urllib.request.urlopen(HEARTBEAT + "?" + urllib.parse.urlencode(
            {"status": status, "msg": msg[:80]}), timeout=15).read()
    except Exception as e:
        print("heartbeat failed: %s" % type(e).__name__, flush=True)

while True:
    try:
        summary = run()
        print(time.strftime("%Y-%m-%d %H:%M ") + summary, flush=True)
        stale = time.time() - os.path.getmtime(QBM_LOG)
        if stale > QBM_STALE:
            heartbeat("down", "qbit_manage silent for %dh" % (stale // 3600))
        else:
            heartbeat("up", summary)
    except Exception as e:
        traceback.print_exc()
        heartbeat("down", "release guard failed: %s" % type(e).__name__)
    time.sleep(INTERVAL)
