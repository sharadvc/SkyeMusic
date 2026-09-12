"""Unit tests for Smart Radio & Mood Generator."""

from __future__ import annotations

import unittest

from tune.radio import MOOD_PRESETS, resolve_radio_query


class TestRadio(unittest.TestCase):
    def test_presets(self):
        self.assertIn("lofi", MOOD_PRESETS)
        self.assertIn("phonk", MOOD_PRESETS)
        self.assertIn("bollywood", MOOD_PRESETS)

    def test_resolve_radio_query(self):
        self.assertEqual(resolve_radio_query("lofi"), MOOD_PRESETS["lofi"])
        self.assertEqual(resolve_radio_query("cyberpunk"), MOOD_PRESETS["cyberpunk"])
        self.assertEqual(resolve_radio_query("Arijit Singh"), "Arijit Singh music playlist mix")


if __name__ == "__main__":
    unittest.main()
