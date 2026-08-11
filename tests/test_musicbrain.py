import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tune.musicbrain import (  # noqa: E402
    build_mood_session,
    detect_intent,
    parse_mood_arg,
    rank_tracks,
)
from tune.queue import Track  # noqa: E402


def T(url, title, channel=""):
    return Track(query=title, title=title, url=url, duration=100.0, channel=channel)


class TestDetectIntent(unittest.TestCase):
    def test_exact_url(self):
        self.assertEqual(detect_intent("https://youtu.be/abc")[0], "exact")

    def test_songs_like(self):
        kind, payload = detect_intent("songs like nights")
        self.assertEqual(kind, "radio")
        self.assertEqual(payload, "nights")

    def test_something_like(self):
        kind, payload = detect_intent("play something like frank ocean")
        self.assertEqual(kind, "radio")
        self.assertIn("frank ocean", payload)

    def test_similar_to(self):
        kind, payload = detect_intent("similar to frank ocean")
        self.assertEqual(kind, "similar")
        self.assertEqual(payload, "frank ocean")

    def test_single_mood(self):
        self.assertEqual(detect_intent("focus"), ("mood", "focus"))

    def test_sad_songs(self):
        self.assertEqual(detect_intent("sad songs"), ("mood", "sad"))

    def test_music_for_studying(self):
        self.assertEqual(detect_intent("music for studying"), ("mood", "focus"))

    def test_chill_rnb(self):
        self.assertEqual(detect_intent("chill rnb"), ("mood", "chill"))

    def test_plain_query(self):
        self.assertEqual(detect_intent("coldplay yellow"), ("search", "coldplay yellow"))

    def test_mood_lang_songs(self):
        self.assertEqual(detect_intent("sad hindi songs"), ("mood", "sad hindi"))

    def test_mood_songs(self):
        self.assertEqual(detect_intent("sad songs"), ("mood", "sad"))


class TestParseMoodArg(unittest.TestCase):
    def test_mood_only(self):
        self.assertEqual(parse_mood_arg("focus"), ("focus", None, None))

    def test_mood_lang(self):
        self.assertEqual(parse_mood_arg("sad hindi"), ("sad", "hindi", None))

    def test_mood_lang_artist(self):
        self.assertEqual(parse_mood_arg("sad hindi arijit singh"),
                         ("sad", "hindi", "arijit singh"))

    def test_mood_artist_no_lang(self):
        self.assertEqual(parse_mood_arg("sad arijit singh"),
                         ("sad", None, "arijit singh"))

    def test_artist_before_lang(self):
        self.assertEqual(parse_mood_arg("sad arijit hindi"),
                         ("sad", "hindi", "arijit"))

    def test_unknown_mood(self):
        self.assertEqual(parse_mood_arg("frobnicate"), (None, None, None))

    def test_empty(self):
        self.assertEqual(parse_mood_arg(""), (None, None, None))


class TestRankTracks(unittest.TestCase):
    def test_dedupe_and_avoid(self):
        out = rank_tracks([T("u1", "A"), T("u1", "A dup"), T("u2", "B")],
                          limit=10, avoid={"u2"})
        self.assertEqual([t.url for t in out], ["u1"])

    def test_limit(self):
        out = rank_tracks([T(f"u{i}", str(i)) for i in range(10)], limit=3)
        self.assertEqual(len(out), 3)

    def test_fav_artist_boost_front(self):
        out = rank_tracks([T("u1", "A", "Artist"), T("u2", "B", "Other")],
                          prefs={"fav_artists": {"artist"}, "top_artists": {}})
        self.assertEqual(out[0].url, "u1")  # fav boost (+2) always beats random

    def test_empty(self):
        self.assertEqual(rank_tracks([]), [])


class TestBuildMoodSession(unittest.TestCase):
    def test_uses_search_and_dedupes(self):
        def fake_search(seed, limit=6):
            return [T("u1", "A"), T("u2", "B"), T("u3", "C")][:limit]

        out = build_mood_session("chill", fake_search, limit=3)
        self.assertLessEqual(len(out), 3)
        urls = [t.url for t in out]
        self.assertEqual(len(set(urls)), len(urls))

    def test_unknown_mood_falls_back(self):
        def fake_search(seed, limit=6):
            return [T("u1", "A")][:limit]

        out = build_mood_session("doesnotexist", fake_search, limit=2)
        self.assertEqual([t.url for t in out], ["u1"])

    def test_lang_qualifies_seeds(self):
        seen = []
        def fake_search(seed, limit=6):
            seen.append(seed)
            return [T("u1", "A")][:limit]

        build_mood_session("sad", fake_search, limit=2, lang="hindi")
        self.assertTrue(all("hindi" in s for s in seen))

    def test_artist_qualifies_seeds_and_ranks(self):
        def fake_search(seed, limit=6):
            return [T("u1", "A", "Singer"), T("u2", "B", "Other")][:limit]

        out = build_mood_session("sad", fake_search, limit=2, artist="singer")
        # artist boost (+3) always beats the base random score
        self.assertEqual(out[0].url, "u1")


if __name__ == "__main__":
    unittest.main()
