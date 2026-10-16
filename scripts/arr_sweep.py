"""Plan Sonarr/Radarr re-searches and dead-pending removals. Pure: no I/O.

The arrs search a back-catalogue item only when it is added or a human asks,
so a search that failed once (an indexer outage, a jailed account) is never
retried. Pending releases that never reached the download client are retried
forever instead of falling through to another release. Each rule below turns a
snapshot of one app into Decisions; arr-sweep.py fetches and executes them.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

STUCK_PENDING_HOURS = 24
AIR_GRACE_HOURS = 24
RESEARCH_AFTER_DAYS = 7
# Every grab from the private tracker is a 14-day seeding obligation.
SEARCHES_PER_RUN = 5

PENDING_STATUSES = frozenset({"delay", "downloadclientunavailable", "fallback"})
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Search:
    command: Mapping[str, object]


@dataclass(frozen=True)
class RemovePendingAndSearch:
    queue_ids: tuple[int, ...]
    search_command: Mapping[str, object]


@dataclass(frozen=True)
class Decision:
    app: str
    rule: str
    subject: str
    reason: str
    action: Search | RemovePendingAndSearch


@dataclass(frozen=True)
class QueueItem:
    id: int
    release: str
    status: str
    in_client: bool
    added: datetime | None
    indexer: str
    media_id: int | None
    subject: str


@dataclass(frozen=True)
class Wanted:
    """One missing episode (Sonarr) or movie (Radarr)."""
    id: int
    group: tuple[object, int]
    label: str
    part: str | None
    available_since: datetime | None
    last_search: datetime | None


@dataclass(frozen=True)
class Snapshot:
    app: str
    queue: tuple[QueueItem, ...]
    wanted: tuple[Wanted, ...]
    # (seriesId, seasonNumber) -> ((episodeId, airDateUtc), ...) for monitored episodes.
    seasons: Mapping[tuple[int, int], tuple[tuple[int, datetime | None], ...]]


@dataclass(frozen=True)
class Plan:
    decisions: tuple[Decision, ...]
    deferred: int


def parse_time(value):
    if not value:
        return None
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _season_label(series, season):
    return "%s S%02d" % (series, season)


def sonarr_snapshot(queue, missing, episodes_by_series):
    """Build from /queue?includeSeries, /wanted/missing?includeSeries and /episode?seriesId records."""
    items = tuple(QueueItem(
        id=r["id"], release=r.get("title") or "?", status=r.get("status") or "",
        in_client=bool(r.get("downloadId")), added=parse_time(r.get("added")),
        indexer=r.get("indexer") or "?", media_id=r.get("episodeId"),
        subject=_season_label((r.get("series") or {}).get("title", "series %s" % r.get("seriesId")),
                              r.get("seasonNumber") or 0))
        for r in queue)
    wanted = tuple(Wanted(
        id=r["id"], group=(r["seriesId"], r["seasonNumber"]),
        label=_season_label(r["series"]["title"], r["seasonNumber"]), part="E%02d" % r["episodeNumber"],
        available_since=parse_time(r.get("airDateUtc")), last_search=parse_time(r.get("lastSearchTime")))
        for r in missing)
    seasons = defaultdict(list)
    for episodes in episodes_by_series.values():
        for e in episodes:
            if e.get("monitored"):
                seasons[(e["seriesId"], e["seasonNumber"])].append((e["id"], parse_time(e.get("airDateUtc"))))
    return Snapshot("sonarr", items, wanted, {k: tuple(v) for k, v in seasons.items()})


def radarr_snapshot(queue, missing):
    """Build from /queue?includeMovie and /wanted/missing records."""
    def label(movie):
        return "%s (%s)" % (movie.get("title", "?"), movie.get("year", "?"))
    items = tuple(QueueItem(
        id=r["id"], release=r.get("title") or "?", status=r.get("status") or "",
        in_client=bool(r.get("downloadId")), added=parse_time(r.get("added")),
        indexer=r.get("indexer") or "?", media_id=r.get("movieId"),
        subject=label(r.get("movie") or {"title": "movie %s" % r.get("movieId")}))
        for r in queue)
    # Radarr reports availability as a flag, not a date; EPOCH clears the grace check.
    wanted = tuple(Wanted(
        id=r["id"], group=("movie", r["id"]), label=label(r), part=None,
        available_since=EPOCH if r.get("isAvailable") else None,
        last_search=parse_time(r.get("lastSearchTime")))
        for r in missing)
    return Snapshot("radarr", items, wanted, {})


def media_search(app, ids):
    ids = sorted(ids)
    if app == "sonarr":
        return {"name": "EpisodeSearch", "episodeIds": ids}
    return {"name": "MoviesSearch", "movieIds": ids}


def _ago(stamp, now):
    if stamp is None:
        return "never searched"
    return "last searched %dd ago" % ((now - stamp) / timedelta(days=1))


def stuck_pending(snap, now):
    releases = defaultdict(list)
    for q in snap.queue:
        if (not q.in_client and q.status.lower() in PENDING_STATUSES and q.added and q.media_id
                and now - q.added > timedelta(hours=STUCK_PENDING_HOURS)):
            releases[q.release].append(q)
    decisions = []
    for records in releases.values():
        first = min(records, key=lambda q: q.added)
        hours = (now - first.added) / timedelta(hours=1)
        decisions.append(Decision(
            snap.app, "stuck_pending", ", ".join(sorted({q.subject for q in records})),
            "pending %.0fh on %s (status %s, %d queue record(s)); never reached download client"
            % (hours, first.indexer, first.status, len(records)),
            RemovePendingAndSearch(tuple(sorted(q.id for q in records)),
                                   media_search(snap.app, {q.media_id for q in records}))))
    return decisions


def missing_search(snap, now):
    queued = {q.media_id for q in snap.queue}
    aired_before = now - timedelta(hours=AIR_GRACE_HOURS)
    searched_before = now - timedelta(days=RESEARCH_AFTER_DAYS)
    groups = defaultdict(list)
    for w in snap.wanted:
        if (w.id not in queued and w.available_since and w.available_since <= aired_before
                and (w.last_search is None or w.last_search < searched_before)):
            groups[w.group].append(w)

    def staleness(members):
        searched = [w.last_search for w in members if w.last_search]
        return (len(searched) == len(members), min(searched, default=EPOCH))

    decisions = []
    for group, members in sorted(groups.items(), key=lambda kv: staleness(kv[1])):
        ids = {w.id for w in members}
        aired = {e for e, at in snap.seasons.get(group, ()) if at and at <= aired_before}
        all_searched, oldest = staleness(members)
        ago = _ago(oldest if all_searched else None, now)
        subject = members[0].label
        parts = sorted(w.part for w in members if w.part)
        if ids == aired:
            # One season search can grab a pack: one torrent to seed instead of many.
            series_id, season = group
            reason = "all %d aired episodes missing; %s" % (len(ids), ago)
            command = {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season}
        else:
            if parts:
                subject += " (%s)" % ",".join(parts)
            reason = ("%d of %d aired episodes missing and due; %s" % (len(ids), len(aired), ago)
                      if aired else "available and missing; %s" % ago)
            command = media_search(snap.app, ids)
        decisions.append(Decision(snap.app, "missing_search", subject, reason, Search(command)))
    return decisions


@dataclass(frozen=True)
class Rule:
    name: str
    decide: Callable[[Snapshot, datetime], list[Decision]]
    budgeted: bool


RULES = (
    Rule("stuck_pending", stuck_pending, budgeted=False),
    Rule("missing_search", missing_search, budgeted=True),
)


def plan(snap, now):
    decisions, deferred, budget = [], 0, SEARCHES_PER_RUN
    for rule in RULES:
        found = rule.decide(snap, now)
        if rule.budgeted:
            deferred += max(0, len(found) - budget)
            found = found[:budget]
            budget -= len(found)
        decisions.extend(found)
    return Plan(tuple(decisions), deferred)
