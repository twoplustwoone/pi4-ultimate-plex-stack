"""Cheap intrusion tells for the canary. Pure decisions; the canary collects facts.

The 2026-10 Plex2Shell compromise ran a miner, an SSH server and a tunnel from
Plex's config folder and /tmp, sat on a ten-month-old pinned Plex build, and left
an unreadable folder that failed the nightly backup three nights running. Each
check here would have flagged one of those within a day.
"""
from datetime import datetime

# Folders the containers' own user cannot write (verified 2026-10-28). A program
# running from anywhere else (/config, /tmp, /share) was put there at runtime.
# Limit: the arr apps own the files inside their bin folders (for self-update),
# so overwriting an existing file there would go unnoticed; adding one cannot.
PROGRAM_DIRS = ("/usr/bin/", "/usr/sbin/", "/usr/local/bin/", "/bin/", "/sbin/", "/package/", "/command/",
                "/usr/lib/plexmediaserver/", "/app/radarr/bin/", "/app/sonarr/bin/", "/app/prowlarr/bin/",
                "/jellyfin/", "/usr/lib/jellyfin-ffmpeg/")
# Root processes cannot be read by the canary's user, so they are judged by name:
# the s6 supervision tree, shells, and the healthchecks docker runs as root.
ROOT_COMMANDS = {"s6-svscan", "s6-supervise", "s6-linux-init-s", "s6-ipcserverd", "s6-rc", "s6-log",
                 "bash", "sh", "sleep", "busybox", "docker-init", "curl", "wget"}
PLEX_MAX_IMAGE_DAYS = 21   # linuxserver rebuilds the image weekly
# Jellyfin's image changes only on a release, with gaps of up to six months, so its
# age says nothing. Compare with the registry instead; Watchtower pulls daily.
RELEASE_GRACE_DAYS = 3
BACKUP_MAX_AGE_HOURS = 36  # nightly job, plus slack


def foreign_process(exe, uid, comm):
    """Why a container process looks planted, or None. `exe` is None when unreadable."""
    if exe is not None:
        if exe.endswith(" (deleted)"):
            return "runs a deleted program %s" % exe[:-len(" (deleted)")]
        return None if exe.startswith(PROGRAM_DIRS) else "runs %s" % exe
    if uid == 0:
        return None if comm in ROOT_COMMANDS else "runs root process %r" % comm
    return "runs an unreadable process %r as uid %s" % (comm, uid)


def plex_update_problem(image_ref, created, now):
    """Why Plex is no longer taking updates, or None. `created` is the image's ISO time."""
    if "@sha256:" in image_ref:
        return "Plex is pinned to an image digest, so it cannot take security fixes"
    age = (now - datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds() / 86400
    if age > PLEX_MAX_IMAGE_DAYS:
        return "Plex image is %d days old: automatic updates have stopped" % age
    return None


def release_update_problem(name, image_ref, local_digests, latest_digest, latest_published, now):
    """Why a container is not on its registry's latest release, or None.

    `local_digests` are the running image's RepoDigests; `latest_*` describe the
    registry's current tag."""
    if "@sha256:" in image_ref:
        return "%s is pinned to an image digest, so it cannot take security fixes" % name
    if any(d.endswith("@" + latest_digest) for d in local_digests):
        return None
    age = (now - datetime.fromisoformat(latest_published.replace("Z", "+00:00"))).total_seconds() / 86400
    if age > RELEASE_GRACE_DAYS:
        return "%s has not taken a release published %d days ago: automatic updates have stopped" % (name, age)
    return None


def backup_problem(result, finished, now):
    """Why the nightly config backup cannot be trusted, or None. `finished` is unix time."""
    when = "never" if finished is None else datetime.fromtimestamp(finished).strftime("%Y-%m-%d %H:%M")
    if finished is None or now - finished > BACKUP_MAX_AGE_HOURS * 3600:
        return "nightly config backup has not finished since %s" % when
    if result != "success":
        return "nightly config backup failed (%s, %s)" % (result, when)
    return None
