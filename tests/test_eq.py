"""Unit tests for 10-band equalizer and audio filters."""

from __future__ import annotations

import unittest

from tune.eq import EQ_PRESETS, EQ_ORDER, format_mpv_eq, next_eq_preset


class TestEqualizer(unittest.TestCase):
    def test_presets_exist(self):
        self.assertIn("flat", EQ_PRESETS)
        self.assertIn("bass", EQ_PRESETS)
        self.assertIn("cyberpunk", EQ_PRESETS)
        self.assertEqual(len(EQ_ORDER), len(EQ_PRESETS))

    def test_format_mpv_eq(self):
        af = format_mpv_eq("bass")
        self.assertTrue(af.startswith("equalizer="))
        self.assertIn("g=7", af)

    def test_next_eq_preset(self):
        self.assertEqual(next_eq_preset("flat"), "bass")
        self.assertEqual(next_eq_preset("pop"), "flat")
        self.assertEqual(next_eq_preset("unknown"), "bass")


if __name__ == "__main__":
    unittest.main()
