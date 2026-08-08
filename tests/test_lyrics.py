import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tune.lyrics import parse_vtt  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
