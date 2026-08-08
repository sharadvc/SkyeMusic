import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tune.resolver import _entry_to_track, _is_url_or_id, is_playlist_url  # noqa: E402


class TestResolver(unittest.TestCase):
    def test_is_url_or_id(self):
        self.assertTrue(_is_url_or_id("https://youtube.com/watch?v=abc"))
        self.assertTrue(_is_url_or_id("AbCdEfGhIjK"))
        self.assertFalse(_is_url_or_id("coldplay yellow"))

    def test_is_playlist_url(self):
        self.assertTrue(is_playlist_url("https://www.youtube.com/playlist?list=xyz"))
        self.assertFalse(is_playlist_url("https://youtube.com/watch?v=abc"))

    def test_entry_to_track(self):
        t = _entry_to_track({"id": "xyz", "title": "Song", "duration": 90,
                             "channel": "Artist"}, "query")
        self.assertEqual(t.url, "https://www.youtube.com/watch?v=xyz")
        self.assertEqual(t.title, "Song")
        self.assertEqual(t.channel, "Artist")

    def test_entry_to_track_missing_id(self):
        self.assertIsNone(_entry_to_track({"title": "no id"}, "query"))


if __name__ == "__main__":
    unittest.main()
