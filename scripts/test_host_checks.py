import unittest
from datetime import datetime, timedelta, timezone

import host_checks as hc


class ForeignProcessTest(unittest.TestCase):
    def test_programs_from_writable_places_are_flagged(self):
        # The 2026-10 implants: miner in Plex's cache, dropbear in /config, curl copies in /tmp.
        for exe in ("/config/Library/Application Support/Plex Media Server/Cache/PlexMediaScanner",
                    "/config/.db/db", "/tmp/.pxb/curl", "/app/evil", "/share/movies/x"):
            self.assertIn(exe, hc.foreign_process(exe, 1000, "x"))

    def test_stock_programs_pass(self):
        for exe in ("/usr/lib/plexmediaserver/Plex Media Server", "/usr/lib/plexmediaserver/Plex Transcoder",
                    "/app/radarr/bin/Radarr", "/usr/local/bin/node", "/usr/bin/qbittorrent-nox"):
            self.assertIsNone(hc.foreign_process(exe, 1000, "x"))

    def test_deleted_binary_is_flagged_even_in_a_system_dir(self):
        self.assertIn("deleted", hc.foreign_process("/usr/bin/top (deleted)", 1000, "top"))

    def test_root_processes_judged_by_name(self):
        self.assertIsNone(hc.foreign_process(None, 0, "s6-supervise"))
        self.assertIsNone(hc.foreign_process(None, 0, "curl"))  # docker healthcheck
        self.assertIn("xmrig", hc.foreign_process(None, 0, "xmrig"))

    def test_unreadable_non_root_process_is_flagged(self):
        self.assertIn("uid 911", hc.foreign_process(None, 911, "db"))


class PlexUpdateTest(unittest.TestCase):
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)

    def test_fresh_image_passes(self):
        self.assertIsNone(hc.plex_update_problem("lscr.io/linuxserver/plex:latest", "2026-10-26T13:17:28.8Z", self.now))

    def test_stale_image_is_flagged(self):
        created = (self.now - timedelta(days=30)).isoformat()
        self.assertIn("30 days old", hc.plex_update_problem("lscr.io/linuxserver/plex:latest", created, self.now))

    def test_digest_pin_is_flagged_even_when_fresh(self):
        # How 1.42.2 stayed in place for ten months.
        self.assertIn("pinned", hc.plex_update_problem("lscr.io/linuxserver/plex@sha256:1720efa8", "2026-10-26T00:00:00Z", self.now))


class BackupTest(unittest.TestCase):
    now = 1_800_000_000

    def test_recent_success_passes(self):
        self.assertIsNone(hc.backup_problem("success", self.now - 3600, self.now))

    def test_failed_run_is_flagged(self):
        self.assertIn("failed (exit-code", hc.backup_problem("exit-code", self.now - 3600, self.now))

    def test_stopped_schedule_is_flagged_even_after_a_success(self):
        self.assertIn("has not finished since", hc.backup_problem("success", self.now - 40 * 3600, self.now))
        self.assertIn("never", hc.backup_problem("success", None, self.now))


if __name__ == "__main__": unittest.main()
