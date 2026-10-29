import unittest

import plex_watched_to_jellyfin as pw

EP = "/share/tv/Show/Season 01/Show - S01E01.mkv"
JF_EP = "/media/tv/Show/Season 01/Show - S01E01.mkv"


class PlanTest(unittest.TestCase):
    def test_watched_and_in_progress_titles_move_across(self):
        movie = "/share/movies/Film (2020)/Film (2020).mkv"
        actions, unmatched = pw.plan([(EP, 2, 0, 1700000000), (movie, 0, 90000, 1700000500)],
                                     {JF_EP: ("ep", False, 0), "/media/movies/Film (2020)/Film (2020).mkv": ("film", False, 0)})
        self.assertEqual([(a[0], a[1], a[2]) for a in actions], [("played", "ep", None), ("resume", "film", 900000000)])
        self.assertEqual(unmatched, [])

    def test_progress_already_in_jellyfin_is_never_overwritten(self):
        rows = [(EP, 1, 0, 1700000000)]
        self.assertEqual(pw.plan(rows, {JF_EP: ("ep", True, 0)})[0], [])
        self.assertEqual(pw.plan(rows, {JF_EP: ("ep", False, 5)})[0], [])

    def test_titles_missing_from_jellyfin_are_reported_not_guessed(self):
        actions, unmatched = pw.plan([(EP, 1, 0, 1700000000), ("/elsewhere/x.mkv", 1, 0, 0)], {})
        self.assertEqual(actions, [])
        self.assertEqual(unmatched, [EP, "/elsewhere/x.mkv"])

    def test_a_title_with_two_plex_versions_is_acted_on_once(self):
        actions, _ = pw.plan([(EP, 1, 0, 1), (EP, 0, 500, 2)], {JF_EP: ("ep", False, 0)})
        self.assertEqual(len(actions), 1)


if __name__ == "__main__":
    unittest.main()
