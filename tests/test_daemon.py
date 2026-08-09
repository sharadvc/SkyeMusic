import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import _env  # noqa: E402
from tune.daemon import Daemon  # noqa: E402
from tune.queue import Track  # noqa: E402


def T(url, title, channel=""):
    return Track(query=title, title=title, url=url, duration=100.0, channel=channel)


class TestDaemonHandlers(unittest.TestCase):
    def setUp(self):
        _env.clean()  # fresh daemon state; don't leak config/queue between tests
        self.d = Daemon()
        self.d.player = None

    def test_add_dedupes(self):
        self.d.q.tracks = [T("u1", "First")]
        self.d._resolve_all = lambda args, fast=False: [T("u1", "First"), T("u2", "Second")]
        resp = self.d._h_add("song")
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["data"]["added"], 1)
        self.assertEqual(resp["data"]["skipped"], 1)
        self.assertEqual([t.url for t in self.d.q.tracks], ["u1", "u2"])

    def test_add_after_queue_end_starts(self):
        # queue finished (index -1) -> add should start playback
        self.d.q.tracks = [T("u1", "A")]
        self.d.q.index = -1
        self.d._resolve_all = lambda args, fast=False: [T("u2", "B")]
        resp = self.d._h_add("song")
        self.assertTrue(resp["ok"])
        self.assertEqual(self.d.q.index, 1)
        self.assertEqual(self.d.q.current().url, "u2")

    def test_speed_persists(self):
        resp = self.d._h_speed("1.5")
        self.assertTrue(resp["ok"])
        self.assertEqual(self.d.q.speed, 1.5)
        self.assertEqual(self.d._speed, 1.5)

    def test_undo_replace(self):
        old = [T("u1", "A"), T("u2", "B")]
        self.d.q.tracks = [T("u3", "C")]
        self.d._undo.append(("replace", [t.as_json() for t in old], 1))
        resp = self.d._h_undo("")
        self.assertTrue(resp["ok"])
        self.assertEqual([t.url for t in self.d.q.tracks], ["u1", "u2"])
        self.assertEqual(self.d.q.index, 1)

    def test_undo_shuffle_restores_order(self):
        order = [T("u1", "A"), T("u2", "B"), T("u3", "C")]
        self.d.q.tracks = [order[2], order[0], order[1]]
        self.d._undo.append(("shuffle", [t.as_json() for t in order]))
        resp = self.d._h_undo("")
        self.assertTrue(resp["ok"])
        self.assertEqual([t.url for t in self.d.q.tracks], ["u1", "u2", "u3"])

    def test_move(self):
        self.d.q.tracks = [T("u1", "A"), T("u2", "B"), T("u3", "C")]
        resp = self.d._h_move("1 3")
        self.assertTrue(resp["ok"])
        self.assertEqual([t.url for t in self.d.q.tracks], ["u2", "u3", "u1"])

    def test_move_updates_current_index(self):
        self.d.q.tracks = [T("u1", "A"), T("u2", "B"), T("u3", "C")]
        self.d.q.index = 2  # on C
        self.d._h_move("3 1")
        self.assertEqual(self.d.q.index, 0)
        self.assertEqual(self.d.q.current().url, "u3")

    def test_move_bad_arg(self):
        resp = self.d._h_move("banana")
        self.assertFalse(resp["ok"])

    def test_list_filter(self):
        self.d.q.tracks = [T("u1", "Yellow"), T("u2", "Paradise"), T("u3", "Yellowish")]
        resp = self.d._h_list("yellow")
        titles = [t["title"] for t in resp["data"]["tracks"]]
        self.assertEqual(titles, ["Yellow", "Yellowish"])

    def test_h_config_bool_coercion(self):
        resp = self.d._h_config("autoplay on")
        self.assertTrue(resp["ok"])
        self.assertIs(self.d.cfg.get("autoplay"), True)

    def test_h_config_int(self):
        resp = self.d._h_config("http_port 9000")
        self.assertTrue(resp["ok"])
        self.assertEqual(self.d.cfg.get("http_port"), 9000)

    def test_h_config_bad_int(self):
        resp = self.d._h_config("http_port abc")
        self.assertFalse(resp["ok"])

    def test_prev_walks_back_stack(self):
        self.d.q.tracks = [T("u1", "A"), T("u2", "B")]
        self.d.q.index = -1
        self.d._back_stack = ["u1"]
        resp = self.d._h_prev("")
        self.assertTrue(resp["data"].get("back"))
        self.assertEqual(self.d.q.index, 0)
        self.assertEqual(self.d.q.current().url, "u1")

    def test_next_pushes_back(self):
        self.d.q.tracks = [T("u1", "A"), T("u2", "B")]
        self.d.q.index = 0
        self.d._h_next("")
        self.assertIn("u1", self.d._back_stack)
        self.assertEqual(self.d.q.index, 1)

    def test_play_records_undo_and_clears_back(self):
        self.d.q.tracks = [T("u1", "A"), T("u2", "B")]
        self.d.q.index = 1
        self.d._back_stack = ["u0"]
        self.d._resolve_all = lambda args, fast=False: [T("u9", "New")]
        resp = self.d._h_play("song")
        self.assertTrue(resp["ok"])
        self.assertEqual(self.d.q.current().url, "u9")
        self.assertEqual(self.d._back_stack, [])
        self.assertEqual(self.d._undo[-1][0], "replace")

    def test_fast_path_placeholder_for_url(self):
        # a direct URL is handed to mpv immediately (no yt-dlp), then enriched
        tracks = self.d._tracks_for_arg("https://www.youtube.com/watch?v=abc", fast=True)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].url, "https://www.youtube.com/watch?v=abc")
        self.assertIn(tracks[0].url, self.d._pending_enrich)

    def test_placeholder_title_shortened(self):
        t = self.d._placeholder_track("https://www.youtube.com/watch?v=abcDEF12345")
        self.assertTrue(t.title.startswith("youtube:"))

    def test_enrich_placeholder_updates_track(self):
        url = "https://www.youtube.com/watch?v=abc"
        self.d.q.tracks = [self.d._placeholder_track(url)]
        self.d._cached_resolve = lambda arg: T(url, "Real Title", "Artist")
        self.d._enrich_placeholder(url)
        t = self.d.q.tracks[0]
        self.assertEqual(t.title, "Real Title")
        self.assertEqual(t.channel, "Artist")
        self.assertNotIn(url, self.d._pending_enrich)

    def test_cached_direct(self):
        self.d._direct_cache["u1"] = (time.time(), "https://stream.example/1")
        self.assertEqual(self.d._cached_direct("u1"), "https://stream.example/1")

    def test_cached_direct_expired(self):
        self.d._direct_cache["u1"] = (time.time() - 99999, "https://stale.example/1")
        self.assertIsNone(self.d._cached_direct("u1"))
        self.assertNotIn("u1", self.d._direct_cache)

    def test_prefetch_next_locked_targets_next_two(self):
        import tune.daemon as D
        seen = []
        D.get_direct_url = lambda url: seen.append(url) or "https://stream.example/x"
        self.d.q.tracks = [T("u1", "A"), T("u2", "B"), T("u3", "C")]
        self.d.q.index = 0
        self.d._prefetch_direct = lambda url: seen.append(url)
        self.d._prefetch_next_locked()
        self.assertEqual(seen, ["u2", "u3"])


if __name__ == "__main__":
    unittest.main()
