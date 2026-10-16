import unittest
from datetime import datetime, timedelta, timezone
from arr_sweep import (RemovePendingAndSearch, Search, SEARCHES_PER_RUN, plan,
    radarr_snapshot, sonarr_snapshot)

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def ago(**delta):
    return (NOW - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pending(id, episode_id, title="Superstore.S05.1080p", added_hours=30, **changes):
    record = dict(id=id, title=title, status="delay", downloadId=None, added=ago(hours=added_hours),
                  indexer="YTS", episodeId=episode_id, seriesId=56, seasonNumber=5,
                  series={"title": "Superstore"})
    record.update(changes)
    return record


def episode(id, number, series_id=56, season=5, aired_days=400, monitored=True):
    return dict(id=id, seriesId=series_id, seasonNumber=season, episodeNumber=number,
                monitored=monitored, airDateUtc=ago(days=aired_days))


def missing(id, number, series_id=56, season=5, aired=None, searched=None, title="Superstore"):
    return dict(id=id, seriesId=series_id, seasonNumber=season, episodeNumber=number,
                airDateUtc=aired or ago(days=400), lastSearchTime=searched,
                series={"title": title})


def movie(id, title="Autumn in New York", available=True, searched=None):
    return dict(id=id, title=title, year=2000, isAvailable=available, lastSearchTime=searched)


def sonarr(queue=(), wanted=(), episodes=()):
    by_series = {}
    for e in episodes:
        by_series.setdefault(e["seriesId"], []).append(e)
    return list(plan(sonarr_snapshot(list(queue), list(wanted), by_series), NOW).decisions)


def radarr(queue=(), wanted=()):
    return list(plan(radarr_snapshot(list(queue), list(wanted)), NOW).decisions)


class StuckPendingTests(unittest.TestCase):
    def test_old_pending_release_is_removed_once_with_all_its_queue_records(self):
        decisions = sonarr(queue=[pending(11, 501), pending(12, 502), pending(13, 503)])
        self.assertEqual(len(decisions), 1)
        d = decisions[0]
        self.assertEqual((d.app, d.rule, d.subject), ("sonarr", "stuck_pending", "Superstore S05"))
        self.assertEqual(d.action, RemovePendingAndSearch(
            (11, 12, 13), {"name": "EpisodeSearch", "episodeIds": [501, 502, 503]}))
        self.assertIn("30h", d.reason)
        self.assertIn("YTS", d.reason)

    def test_radarr_pending_release_searches_the_movie(self):
        record = dict(id=7, title="Autumn.in.New.York.2000", status="downloadClientUnavailable",
                      downloadId=None, added=ago(hours=238), indexer="YTS", movieId=42,
                      movie={"title": "Autumn in New York", "year": 2000})
        [d] = radarr(queue=[record])
        self.assertEqual(d.subject, "Autumn in New York (2000)")
        self.assertEqual(d.action, RemovePendingAndSearch((7,), {"name": "MoviesSearch", "movieIds": [42]}))

    def test_pending_under_a_day_is_left_alone(self):
        self.assertEqual(sonarr(queue=[pending(11, 501, added_hours=23)]), [])

    def test_release_in_download_client_is_left_alone(self):
        self.assertEqual(sonarr(queue=[pending(11, 501, downloadId="ABC", status="queued")]), [])
        self.assertEqual(sonarr(queue=[pending(11, 501, downloadId="ABC")]), [])

    def test_pending_is_not_subject_to_search_budget(self):
        queue = [pending(i, 500 + i, title="release %d" % i) for i in range(SEARCHES_PER_RUN + 2)]
        wanted = [missing(900 + i, 1, series_id=900 + i) for i in range(SEARCHES_PER_RUN + 2)]
        decisions = sonarr(queue=queue, wanted=wanted)
        rules = [d.rule for d in decisions]
        self.assertEqual(rules.count("stuck_pending"), SEARCHES_PER_RUN + 2)
        self.assertEqual(rules.count("missing_search"), SEARCHES_PER_RUN)


class MissingSearchTests(unittest.TestCase):
    def test_fully_missing_season_is_one_season_search(self):
        [d] = sonarr(wanted=[missing(1, 1), missing(2, 2)], episodes=[episode(1, 1), episode(2, 2)])
        self.assertEqual(d.rule, "missing_search")
        self.assertEqual(d.subject, "Superstore S05")
        self.assertEqual(d.action, Search({"name": "SeasonSearch", "seriesId": 56, "seasonNumber": 5}))

    def test_partially_missing_season_searches_only_eligible_episodes(self):
        episodes = [episode(1, 1), episode(2, 2), episode(3, 3), episode(4, 4)]
        wanted = [missing(2, 2), missing(3, 3, searched=ago(days=2)), missing(4, 4)]
        [d] = sonarr(wanted=wanted, episodes=episodes)
        self.assertEqual(d.action, Search({"name": "EpisodeSearch", "episodeIds": [2, 4]}))
        self.assertEqual(d.subject, "Superstore S05 (E02,E04)")

    def test_unmonitored_episodes_do_not_make_a_season_partial(self):
        episodes = [episode(1, 1), episode(2, 2), episode(3, 3, monitored=False)]
        [d] = sonarr(wanted=[missing(1, 1), missing(2, 2)], episodes=episodes)
        self.assertEqual(d.action.command["name"], "SeasonSearch")

    def test_recently_searched_episode_is_skipped(self):
        self.assertEqual(sonarr(wanted=[missing(1, 1, searched=ago(days=2))], episodes=[episode(1, 1)]), [])

    def test_episode_searched_over_a_week_ago_is_searched_again(self):
        [d] = sonarr(wanted=[missing(1, 1, searched=ago(days=8))], episodes=[episode(1, 1)])
        self.assertIn("8d ago", d.reason)

    def test_episode_already_in_queue_is_skipped(self):
        downloading = pending(11, 1, downloadId="ABC", status="queued", added_hours=1)
        self.assertEqual(sonarr(queue=[downloading], wanted=[missing(1, 1)], episodes=[episode(1, 1)]), [])

    def test_unaired_and_just_aired_episodes_are_skipped(self):
        wanted = [missing(1, 1, aired=ago(hours=23)), missing(2, 2, aired=ago(hours=-48)),
                  missing(3, 3, aired=None)]
        wanted[2]["airDateUtc"] = None
        episodes = [episode(1, 1, aired_days=23 / 24), episode(2, 2, aired_days=-2)]
        self.assertEqual(sonarr(wanted=wanted, episodes=episodes), [])

    def test_unavailable_movie_is_skipped(self):
        self.assertEqual(radarr(wanted=[movie(1, available=False)]), [])

    def test_available_movie_is_searched(self):
        [d] = radarr(wanted=[movie(42)])
        self.assertEqual((d.subject, d.action), ("Autumn in New York (2000)",
                         Search({"name": "MoviesSearch", "movieIds": [42]})))

    def test_never_searched_comes_before_long_ago_searched(self):
        wanted = [movie(1, "Old", searched=ago(days=300)), movie(2, "Never"), movie(3, "Older", searched=ago(days=400))]
        self.assertEqual([d.action.command["movieIds"] for d in radarr(wanted=wanted)], [[2], [3], [1]])

    def test_budget_keeps_the_oldest_searches_per_app(self):
        wanted = [movie(i, "Movie %d" % i, searched=ago(days=10 + i)) for i in range(SEARCHES_PER_RUN + 3)]
        decisions = radarr(wanted=wanted)
        self.assertEqual(len(decisions), SEARCHES_PER_RUN)
        oldest_first = sorted(range(SEARCHES_PER_RUN + 3), reverse=True)[:SEARCHES_PER_RUN]
        self.assertEqual([d.action.command["movieIds"][0] for d in decisions], oldest_first)

    def test_deferred_count_reports_what_the_budget_dropped(self):
        wanted = [movie(i, searched=ago(days=10 + i)) for i in range(SEARCHES_PER_RUN + 3)]
        self.assertEqual(plan(radarr_snapshot([], wanted), NOW).deferred, 3)


if __name__ == "__main__":
    unittest.main()
