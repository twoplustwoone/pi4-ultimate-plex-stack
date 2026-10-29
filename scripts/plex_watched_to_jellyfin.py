#!/usr/bin/env python3
"""Copy watched and resume state from Plex to Jellyfin, matching titles by file path.

Reads a snapshot of Plex's library database, so plex.tv is never called: a
companion app speaking for the server's token is what made the Shield report
"Server is Outdated" (2026-10). Only ever moves Jellyfin forward: a title already
played or started in Jellyfin is left alone, so reruns are safe.

    plex_watched_to_jellyfin.py --map francisco.di=Frisco --map Brisita=Bree          # dry run
    plex_watched_to_jellyfin.py --map francisco.di=Frisco --map Brisita=Bree --apply
"""
import argparse, json, os, re, sqlite3, urllib.parse, urllib.request
from datetime import datetime, timezone

PLEX_DB = ("/home/pi/docker-configs/plex/config/Library/Application Support/Plex Media Server/"
           "Plug-in Support/Databases/com.plexapp.plugins.library.db")
ENV = os.path.expanduser("~/yggdrasil/.env")
JELLYFIN = "http://127.0.0.1:8096"
PLEX_TO_JELLYFIN_PATHS = (("/share/movies/", "/media/movies/"), ("/share/tv/", "/media/tv/"))
TICKS_PER_MS = 10000

PLEX_STATE = """
select a.name, p.file, s.view_count, s.view_offset, s.last_viewed_at
from metadata_item_settings s
join accounts a on a.id = s.account_id
join metadata_items i on i.guid = s.guid and i.metadata_type in (1, 4)
join media_items m on m.metadata_item_id = i.id
join media_parts p on p.media_item_id = m.id
where s.view_count > 0 or s.view_offset > 0
"""


def jellyfin_path(plex_path):
    for plex, jf in PLEX_TO_JELLYFIN_PATHS:
        if plex_path.startswith(plex):
            return jf + plex_path[len(plex):]
    return None


def plan(plex_rows, jellyfin_items):
    """Actions for one user. `plex_rows` are (path, view_count, view_offset_ms, last_viewed_epoch);
    `jellyfin_items` maps a Jellyfin path to (item_id, played, position_ticks).
    Returns (actions, unmatched_paths), an action being (kind, item_id, value, when)."""
    actions, unmatched, seen = [], [], set()
    for path, count, offset, viewed in plex_rows:
        item = jellyfin_items.get(jellyfin_path(path) or "")
        if item is None:
            unmatched.append(path)
            continue
        item_id, played, position = item
        if item_id in seen or played or position:
            continue
        seen.add(item_id)
        when = datetime.fromtimestamp(viewed or 0, timezone.utc)
        if count:
            actions.append(("played", item_id, None, when))
        else:
            actions.append(("resume", item_id, offset * TICKS_PER_MS, when))
    return actions, unmatched


def plex_rows_by_account():
    live = sqlite3.connect("file:%s?mode=ro" % PLEX_DB, uri=True)
    snap = sqlite3.connect(":memory:")
    live.backup(snap)
    live.close()
    rows = {}
    for name, path, count, offset, viewed in snap.execute(PLEX_STATE):
        rows.setdefault(name, []).append((path, count, offset or 0, viewed))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", action="append", required=True, metavar="PLEX=JELLYFIN",
                    help="Plex account name = Jellyfin user name")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    key = re.search(r"^JELLYFIN_API_KEY=(.+)$", open(ENV).read(), re.M).group(1)

    def call(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(JELLYFIN + path, data=data, method=method, headers={
            "Authorization": 'MediaBrowser Token="%s"' % key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    users = {u["Name"]: u["Id"] for u in call("GET", "/Users")}
    plex = plex_rows_by_account()
    for pair in args.map:
        plex_name, jf_name = pair.split("=", 1)
        uid = users[jf_name]
        items = call("GET", "/Items?Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path&EnableUserData=true"
                     "&userId=" + uid)["Items"]
        index = {i["Path"]: (i["Id"], i["UserData"]["Played"], i["UserData"]["PlaybackPositionTicks"])
                 for i in items if i.get("Path")}
        actions, unmatched = plan(plex.get(plex_name, []), index)
        played = sum(a[0] == "played" for a in actions)
        print("%s -> %s: %d to mark played, %d to resume, %d Plex entries with no file in Jellyfin"
              % (plex_name, jf_name, played, len(actions) - played, len(unmatched)))
        for path in unmatched:
            print("  unmatched:", path)
        if not args.apply:
            continue
        for kind, item_id, ticks, when in actions:
            q = "userId=%s" % uid
            if kind == "played":
                call("POST", "/UserPlayedItems/%s?%s&datePlayed=%s" % (item_id, q, urllib.parse.quote(when.isoformat())))
            else:
                call("POST", "/UserItems/%s/UserData?%s" % (item_id, q),
                     {"PlaybackPositionTicks": ticks, "LastPlayedDate": when.isoformat()})
        print("  applied")
    skipped = sorted(set(plex) - {p.split("=", 1)[0] for p in args.map})
    if skipped:
        print("Plex accounts not mapped, left alone:", ", ".join(skipped))


if __name__ == "__main__":
    main()
