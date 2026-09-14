import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tune.lyrics import parse_lrc, parse_vtt  # noqa: E402


class TestParseVtt(unittest.TestCase):
    def test_two_cues(self):
        sample = """WEBVTT

00:00:01.250 --> 00:00:04.000
Hello &amp; welcome

00:00:04.50 --> 00:00:08.000
second <i>line</i> here
"""
        lines = parse_vtt(sample)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["text"], "Hello & welcome")
        self.assertAlmostEqual(lines[0]["start"], 1.25)
        self.assertAlmostEqual(lines[1]["start"], 4.5)  # 2-digit ms
        self.assertEqual(lines[1]["text"], "second line here")
        self.assertTrue(lines[0]["synced"])

    def test_cue_identifiers_and_settings(self):
        sample = """WEBVTT

1
01:20.500 --> 01:25.000 align:start
First cue with ID

2
01:25.000 --> 01:28.000
Second cue
"""
        lines = parse_vtt(sample)
        self.assertEqual(len(lines), 2)
        self.assertAlmostEqual(lines[0]["start"], 80.5)
        self.assertAlmostEqual(lines[0]["end"], 85.0)
        self.assertEqual(lines[0]["text"], "First cue with ID")

    def test_empty(self):
        self.assertEqual(parse_vtt(""), [])
        self.assertEqual(parse_vtt("WEBVTT\n\nnothing here"), [])


class TestParseLrc(unittest.TestCase):
    def test_two_lines(self):
        text = "[00:01.50]Hello world\n[00:03.00]Second line\n"
        lines = parse_lrc(text)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["text"], "Hello world")
        self.assertAlmostEqual(lines[0]["start"], 1.5)
        self.assertAlmostEqual(lines[0]["end"], 3.0)
        self.assertAlmostEqual(lines[1]["start"], 3.0)
        self.assertTrue(lines[0]["synced"])

    def test_multiple_tags_one_line(self):
        text = "[00:01.00][00:05.00]Chorus\n"
        lines = parse_lrc(text)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["text"], "Chorus")
        self.assertEqual(lines[1]["text"], "Chorus")

    def test_empty(self):
        self.assertEqual(parse_lrc("no timestamps here"), [])

    def test_extended_lrc_formats(self):
        # 3-digit ms and hour format
        text = "[00:01.250]Line 1\n[01:00:02.50]Line 2\n"
        lines = parse_lrc(text)
        self.assertEqual(len(lines), 2)
        self.assertAlmostEqual(lines[0]["start"], 1.25)
        self.assertAlmostEqual(lines[1]["start"], 3602.5)


from tune.lyrics import clean_track_info, synthesize_sync_for_plain, format_plain_lyrics, is_matching_song, fetch


class TestCleanTrackInfo(unittest.TestCase):
    def test_youtube_brackets_and_separators(self):
        title, artist = clean_track_info("Coldplay - Viva La Vida (Official Video)", "Coldplay")
        self.assertEqual(title, "Viva La Vida")
        self.assertEqual(artist, "Coldplay")

    def test_en_dash_and_remastered(self):
        title, artist = clean_track_info("Queen – Bohemian Rhapsody (Official Video Remastered)", "Queen Official")
        self.assertEqual(title, "Bohemian Rhapsody")
        self.assertEqual(artist, "Queen")

    def test_lyrics_tag_and_channel_topic(self):
        title, artist = clean_track_info("Dil Ibaadat (Lyrics) - Krishnakumar Kunnath", "KK - Topic")
        self.assertEqual(title, "Dil Ibaadat")
        self.assertEqual(artist, "KK")


class TestNoFakeLyrics(unittest.TestCase):
    def test_plain_text_has_no_fake_timestamps(self):
        text = "Line one\nLine two\nLine three\n"
        lines = synthesize_sync_for_plain(text, duration=100.0)
        self.assertEqual(len(lines), 3)
        self.assertFalse(lines[0].get("synced", True))
        self.assertIsNone(lines[0]["start"])
        self.assertIsNone(lines[0]["end"])
        self.assertEqual(lines[0]["text"], "Line one")

    def test_empty(self):
        self.assertEqual(synthesize_sync_for_plain(""), [])
        self.assertEqual(format_plain_lyrics(""), [])


class TestCandidateMatching(unittest.TestCase):
    def test_reject_cover_and_remix(self):
        ok, _ = is_matching_song("Bohemian Rhapsody (Acoustic Cover)", "Artist", 350,
                                 "Bohemian Rhapsody", "Queen", 354)
        self.assertFalse(ok)
        ok, _ = is_matching_song("Dil Ibaadat (Remix)", "DJ Shadow", 200,
                                 "Dil Ibaadat", "KK", 330)
        self.assertFalse(ok)

    def test_reject_live_version(self):
        ok, _ = is_matching_song("Bohemian Rhapsody (Live at Wembley)", "Queen", 320,
                                 "Bohemian Rhapsody", "Queen", 354)
        self.assertFalse(ok)

    def test_reject_duration_mismatch(self):
        # 120s vs 330s -> completely different length / cut
        ok, _ = is_matching_song("Dil Ibaadat", "KK", 120,
                                 "Dil Ibaadat", "KK", 330)
        self.assertFalse(ok)

    def test_accept_authentic_match(self):
        ok, score = is_matching_song("Viva La Vida", "Coldplay", 242,
                                     "Viva La Vida", "Coldplay", 242)
        self.assertTrue(ok)
        self.assertGreater(score, 100)


class TestMultiSourceFetch(unittest.TestCase):
    def test_fetch_uses_lrclib_when_available(self):
        # Test that fetch works and returns verified synced lyrics list
        lines = fetch(title="Viva La Vida", artist="Coldplay", duration=240.0)
        self.assertTrue(len(lines) > 0)
        self.assertTrue(any("rule the world" in ln["text"].lower() for ln in lines))
        self.assertTrue(all(ln.get("synced", True) for ln in lines))


from tune.tui import _cur_lyr_line, _update_lyrics


class TestEnhancedNoiseAndPipedTitles(unittest.TestCase):
    def test_unbracketed_with_lyrics(self):
        from tune.lyrics import _clean_title_noise
        self.assertEqual(_clean_title_noise("Character Dheela With Lyrics"), "Character Dheela")
        self.assertEqual(_clean_title_noise("Starboy with lyrics"), "Starboy")
        self.assertEqual(_clean_title_noise("Tum Hi Ho - Lyrical Video"), "Tum Hi Ho")
        self.assertEqual(_clean_title_noise("Shape of You - Full Song"), "Shape of You")

    def test_piped_title_artist_extraction(self):
        title = "Character Dheela With Lyrics | Ready I Salman Khan I Zarine Khan | Pritam"
        song, artist = clean_track_info(title, "T-Series")
        self.assertEqual(song, "Character Dheela")
        self.assertEqual(artist, "Pritam")

    def test_matching_with_raw_title_and_channel_label(self):
        ok, score = is_matching_song(
            cand_title="Character Dheela",
            cand_artist="Pritam, Neeraj Shridhar, Amrita Kak",
            cand_dur=227.0,
            target_title="Character Dheela",
            target_artist="T-Series",
            target_dur=245.0,
            raw_title="Character Dheela With Lyrics | Ready I Salman Khan I Zarine Khan | Pritam"
        )
        self.assertTrue(ok)
        self.assertGreater(score, 50.0)


class TestTuiLyricsIntegration(unittest.TestCase):
    def test_cur_lyr_line_position_tracking(self):
        lines = [
            {"start": 10.0, "end": 15.0, "text": "First line"},
            {"start": 18.0, "end": 22.0, "text": "Second line after gap"},
            {"start": 25.0, "end": 30.0, "text": "Third line"},
        ]
        ui = {"lyr_lines": lines}

        # Before any line: returns -1 (intro state)
        self.assertEqual(_cur_lyr_line({"position": 5.0}, ui), -1)
        # Inside first line
        self.assertEqual(_cur_lyr_line({"position": 12.0}, ui), 0)
        # Inside gap (16.0s) -> holds first line
        self.assertEqual(_cur_lyr_line({"position": 16.0}, ui), 0)
        # Inside second line
        self.assertEqual(_cur_lyr_line({"position": 20.0}, ui), 1)
        # Inside third line
        self.assertEqual(_cur_lyr_line({"position": 28.0}, ui), 2)
        # After all lines
        self.assertEqual(_cur_lyr_line({"position": 40.0}, ui), 2)

    def test_cur_lyr_line_empty(self):
        self.assertEqual(_cur_lyr_line({"position": 10.0}, {"lyr_lines": []}), 0)

    def test_update_lyrics_guard(self):
        ui = {"lyr_loading": True, "lyr_url": "https://example.com/song"}
        # Should not raise or re-trigger when already loading same url
        _update_lyrics(ui, {"url": "https://example.com/song"})
        self.assertTrue(ui["lyr_loading"])


class TestQueryAwareLyrics(unittest.TestCase):
    def test_candidate_queries_with_user_query(self):
        from tune.lyrics import get_candidate_queries
        title = "तुम ही हो  आशिकी 2 पूरा गाना बोल के साथ  | आदित्य रॉय कपूर, श्रद्धा कपूर"
        queries = get_candidate_queries(title, artist="T-Series", query="Tum Hi Ho")
        self.assertEqual(queries[0], "Tum Hi Ho")
        self.assertIn("Tum Hi Ho T-Series", queries)

    def test_is_matching_song_with_devanagari_title_and_latin_query(self):
        from tune.lyrics import is_matching_song
        title = "तुम ही हो  आशिकी 2 पूरा गाना बोल के साथ  | आदित्य रॉय कपूर, श्रद्धा कपूर"
        ok, score = is_matching_song(
            cand_title="Tum Hi Ho",
            cand_artist="Arijit Singh",
            cand_dur=267.0,
            target_title=title,
            target_artist="T-Series",
            target_dur=268.0,
            raw_title=title,
            query="Tum Hi Ho"
        )
        self.assertTrue(ok)
        self.assertGreater(score, 100.0)


class TestHinglishTransliteration(unittest.TestCase):
    def test_devanagari_to_hinglish(self):
        from tune.lyrics import devanagari_to_hinglish
        text = "हम तेरे बिन अब रह नहीं सकते"
        res = devanagari_to_hinglish(text)
        self.assertIn("hum tere bin ab reh nahi", res)
        self.assertFalse(any("\u0900" <= c <= "\u097F" for c in res))

    def test_english_and_other_languages_passthrough(self):
        from tune.lyrics import devanagari_to_hinglish
        eng = "I am in love with the shape of you"
        span = "Despacito quiero respirar tu cuello despacito"
        jap = "アイドル (Idol)"
        self.assertEqual(devanagari_to_hinglish(eng), eng)
        self.assertEqual(devanagari_to_hinglish(span), span)
        self.assertEqual(devanagari_to_hinglish(jap), jap)


if __name__ == "__main__":
    unittest.main()


