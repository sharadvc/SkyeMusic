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

    def test_multiple_tags_one_line(self):
        text = "[00:01.00][00:05.00]Chorus\n"
        lines = parse_lrc(text)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["text"], "Chorus")
        self.assertEqual(lines[1]["text"], "Chorus")

    def test_empty(self):
        self.assertEqual(parse_lrc("no timestamps here"), [])


if __name__ == "__main__":
    unittest.main()
