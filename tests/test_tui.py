import curses
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tune.tui import (
    _VIZ_MODES,
    _cur_lyr_line,
    _cycle_viz_mode,
    _draw,
    _draw_lyrics,
    _draw_mini_player,
    _draw_queue,
    _get_live_position,
    _now_key,
    _search_key,
)


class MockStdscr:
    def __init__(self, h=30, w=100):
        self.h = h
        self.w = w
        self.calls = []

    def addstr(self, y, x, string, *args):
        if y < 0 or y >= self.h:
            raise curses.error(f"Row {y} out of bounds (height={self.h})")
        if x < 0 or x + len(string) > self.w:
            raise curses.error(f"Col {x} + len {len(string)} exceeds width {self.w}")
        self.calls.append((y, x, string, args))


class TestTuiLayout(unittest.TestCase):
    def test_tab_key_cycles_layouts(self):
        ui = {"layout": "studio", "lyr_on": False}
        status = {}

        # Tab (ascii 9) cycles studio -> queue -> lyrics -> mini -> studio
        _now_key(9, "now", status, ui)
        self.assertEqual(ui["layout"], "queue")
        self.assertFalse(ui["lyr_on"])

        _now_key(9, "now", status, ui)
        self.assertEqual(ui["layout"], "lyrics")
        self.assertTrue(ui["lyr_on"])

        _now_key(9, "now", status, ui)
        self.assertEqual(ui["layout"], "mini")

        _now_key(9, "now", status, ui)
        self.assertEqual(ui["layout"], "studio")
        self.assertFalse(ui["lyr_on"])

    def test_l_key_toggles_lyrics_mode(self):
        ui = {"layout": "studio", "lyr_on": False}
        status = {}

        _now_key(ord("l"), "now", status, ui)
        self.assertEqual(ui["layout"], "lyrics")
        self.assertTrue(ui["lyr_on"])

        _now_key(ord("l"), "now", status, ui)
        self.assertEqual(ui["layout"], "studio")
        self.assertFalse(ui["lyr_on"])

    def test_sync_nudge_keys(self):
        ui = {"layout": "studio", "lyr_offset": 0.0}
        status = {}

        # ',' fine nudges -0.05s
        _now_key(ord(","), "now", status, ui)
        self.assertEqual(ui["lyr_offset"], -0.05)

        # '.' fine nudges +0.05s
        _now_key(ord("."), "now", status, ui)
        self.assertEqual(ui["lyr_offset"], 0.0)

        # '<' coarse nudges -0.25s
        _now_key(ord("<"), "now", status, ui)
        self.assertEqual(ui["lyr_offset"], -0.25)

        # '>' coarse nudges +0.25s
        _now_key(ord(">"), "now", status, ui)
        self.assertEqual(ui["lyr_offset"], 0.0)

        # 'o' resets to 0.0s
        _now_key(ord("o"), "now", status, ui)
        self.assertEqual(ui["lyr_offset"], 0.0)

    def test_viz_mode_cycling(self):
        ui = {"viz_mode": "spectrum"}
        expected_modes = ["stereo", "wave", "bars", "matrix", "vu", "oscilloscope", "hyperdrive", "dna", "fire", "cyberpunk", "aurora", "spectrum"]
        for expected in expected_modes:
            _cycle_viz_mode(ui)
            self.assertEqual(ui["viz_mode"], expected)

    def test_live_position_interpolation(self):
        import time
        t0 = time.monotonic() - 0.5
        status = {"state": "playing", "_pos_base": 10.0, "_mono_t": t0, "speed": 1.0}
        ui = {"lyr_offset": 0.25}

        live_pos = _get_live_position(status, ui)
        # Position should be approx 10.0 + 0.5 + 0.25 = 10.75
        self.assertGreaterEqual(live_pos, 10.70)
        self.assertLessEqual(live_pos, 10.85)

    def test_pageup_pagedown_lyrics_scroll(self):
        ui = {"layout": "studio", "lyr_scroll": 10}
        status = {}

        _now_key(curses.KEY_PPAGE, "now", status, ui)
        self.assertEqual(ui["lyr_scroll"], 6)

        _now_key(curses.KEY_NPAGE, "now", status, ui)
        self.assertEqual(ui["lyr_scroll"], 10)

    def test_cur_lyr_line(self):
        lines = [
            {"start": 5.0, "end": 10.0, "text": "Intro"},
            {"start": 10.0, "end": 15.0, "text": "Verse 1"},
            {"start": 15.0, "end": 20.0, "text": "Chorus"},
        ]
        ui = {"lyr_lines": lines, "lyr_offset": 0.0}

        # Intro state before 5.0s
        self.assertEqual(_cur_lyr_line({"position": 2.5}, ui), -1)
        self.assertEqual(_cur_lyr_line({"position": 7.5}, ui), 0)
        self.assertEqual(_cur_lyr_line({"position": 12.0}, ui), 1)
        # Past last line
        self.assertEqual(_cur_lyr_line({"position": 25.0}, ui), 2)


class TestTuiDualPaneRendering(unittest.TestCase):
    def setUp(self):
        self._orig_color_pair = getattr(curses, "color_pair", None)
        curses.color_pair = lambda n: n

        self.status = {
            "title": "Test Song",
            "artist": "Test Artist",
            "state": "playing",
            "position": 7.5,
            "duration": 180.0,
            "volume": 80,
            "current_index": 0,
            "queue_len": 2,
            "queue": [
                {"title": "Track One", "channel": "Artist One", "duration": 180.0},
                {"title": "Track Two", "channel": "Artist Two", "duration": 210.0},
            ],
        }
        self.ui = {
            "layout": "studio",
            "lyr_on": False,
            "lyr_lines": [
                {"start": 0.0, "end": 5.0, "text": "First line", "synced": True},
                {"start": 5.0, "end": 10.0, "text": "Second line (active)", "synced": True},
                {"start": 10.0, "end": 15.0, "text": "Third line", "synced": True},
            ],
            "qsel": 0,
            "viz_mode": "spectrum",
        }

    def tearDown(self):
        if self._orig_color_pair is not None:
            curses.color_pair = self._orig_color_pair

    def test_dual_pane_split_at_width_100(self):
        scr = MockStdscr(h=30, w=100)
        _draw(
            scr, self.status, 30, 100, "now",
            sq="", sresults=[], ssel=0, ssearching=False, smsg="",
            amp_t=1.0, ui=self.ui
        )

        left_w = int(100 * 0.46)  # 46
        # Check that divider │ was drawn at column 46
        dividers = [call for call in scr.calls if call[1] == left_w and call[2] == "│"]
        self.assertGreater(len(dividers), 0)

        # Check separator header with ┬
        sep_calls = [call for call in scr.calls if "┬" in call[2]]
        self.assertEqual(len(sep_calls), 1)
        self.assertIn("QUEUE", sep_calls[0][2])
        self.assertIn("LIVE SYNCED KARAOKE", sep_calls[0][2])

        # Check that queue items stayed within column 0..left_w-1
        queue_calls = [call for call in scr.calls if "Track One" in call[2]]
        self.assertEqual(len(queue_calls), 1)
        self.assertEqual(queue_calls[0][1], 0)
        self.assertLessEqual(queue_calls[0][1] + len(queue_calls[0][2]), left_w)

        # Check that lyrics items were drawn at column left_w + 1 (47)
        lyrics_calls = [call for call in scr.calls if "First line" in call[2]]
        self.assertEqual(len(lyrics_calls), 1)
        self.assertEqual(lyrics_calls[0][1], left_w + 1)

    def test_narrow_terminal_fallback(self):
        # When terminal width < 80, studio mode gracefully falls back to single-pane
        scr = MockStdscr(h=24, w=70)
        _draw(
            scr, self.status, 24, 70, "now",
            sq="", sresults=[], ssel=0, ssearching=False, smsg="",
            amp_t=1.0, ui=self.ui
        )

        # No vertical divider │ should be drawn
        dividers = [call for call in scr.calls if call[2] == "│"]
        self.assertEqual(len(dividers), 0)

    def test_mini_player_rendering(self):
        scr = MockStdscr(h=24, w=80)
        status = {
            "state": "playing",
            "title": "Starboy - The Weeknd",
            "position": 45.0,
            "duration": 230.0,
            "volume": 85,
            "repeat": "all",
            "shuffle": True,
            "current_index": 2,
            "queue_len": 15,
        }
        ui = {
            "viz_mode": "fire",
            "lyr_lines": [{"start": 40.0, "end": 50.0, "text": "I'm a motherfuckin' starboy"}],
            "lyr_offset": 0.0,
        }
        _draw_mini_player(scr, status, ui, 24, 80, amp_t=1.5)

        rendered_text = " ".join(call[2] for call in scr.calls)
        self.assertIn("Skye Mini Player", rendered_text)
        self.assertIn("Starboy - The Weeknd", rendered_text)
        self.assertIn("I'm a motherfuckin' starboy", rendered_text)
        self.assertIn("VIZ: FIRE", rendered_text)
        self.assertIn("VOL 85%", rendered_text)
        self.assertIn("RPT ALL", rendered_text)
        self.assertIn("SHUF ON", rendered_text)
        self.assertIn("#3/15", rendered_text)


    def test_lyrics_layout_renders_queue_on_left_and_lyrics_on_right(self):
        scr = MockStdscr(h=30, w=100)
        ui = dict(self.ui)
        ui["layout"] = "lyrics"
        _draw(
            scr, self.status, 30, 100, "now",
            sq="", sresults=[], ssel=0, ssearching=False, smsg="",
            amp_t=1.0, ui=ui
        )

        left_w = int(100 * 0.46)  # 46
        # Queue item must be rendered at col 0
        queue_calls = [call for call in scr.calls if "Track One" in call[2]]
        self.assertEqual(len(queue_calls), 1)
        self.assertEqual(queue_calls[0][1], 0)

        # Lyrics item must be rendered at col left_w + 1 (47) and NEVER at col 0
        lyrics_calls = [call for call in scr.calls if "First line" in call[2]]
        self.assertEqual(len(lyrics_calls), 1)
        self.assertEqual(lyrics_calls[0][1], left_w + 1)
        self.assertNotEqual(lyrics_calls[0][1], 0)

    def test_draw_lyrics_direct_call_forces_right_pane(self):
        scr = MockStdscr(h=30, w=100)
        _draw_lyrics(scr, self.status, self.ui, 30, 100, top=6, left=0)

        left_w = int(100 * 0.46)  # 46
        lyrics_calls = [call for call in scr.calls if "First line" in call[2]]
        self.assertEqual(len(lyrics_calls), 1)
        self.assertEqual(lyrics_calls[0][1], left_w + 1)
        self.assertNotEqual(lyrics_calls[0][1], 0)


    def test_section_hiding_keybindings(self):
        ui = {}
        status = {}

        # H toggles hide_header
        _now_key(ord("H"), "now", status, ui)
        self.assertTrue(ui["hide_header"])
        _now_key(ord("H"), "now", status, ui)
        self.assertFalse(ui["hide_header"])

        # V toggles hide_viz
        _now_key(ord("V"), "now", status, ui)
        self.assertTrue(ui["hide_viz"])
        _now_key(ord("V"), "now", status, ui)
        self.assertFalse(ui["hide_viz"])

        # ? toggles hide_footer
        _now_key(ord("?"), "now", status, ui)
        self.assertTrue(ui["hide_footer"])
        _now_key(ord("?"), "now", status, ui)
        self.assertFalse(ui["hide_footer"])

        # Z activates Zen Mode (hides all 3)
        _now_key(ord("Z"), "now", status, ui)
        self.assertTrue(ui["hide_header"])
        self.assertTrue(ui["hide_viz"])
        self.assertTrue(ui["hide_footer"])

        # Z again restores all 3
        _now_key(ord("Z"), "now", status, ui)
        self.assertFalse(ui["hide_header"])
        self.assertFalse(ui["hide_viz"])
        self.assertFalse(ui["hide_footer"])

    def test_section_hiding_rendering(self):
        scr = MockStdscr(h=30, w=100)
        ui = dict(self.ui)
        ui["hide_header"] = True
        ui["hide_viz"] = True
        ui["hide_footer"] = True

        _draw(
            scr, self.status, 30, 100, "now",
            sq="", sresults=[], ssel=0, ssearching=False, smsg="",
            amp_t=1.0, ui=ui
        )

        rendered_text = " ".join(call[2] for call in scr.calls)
        # Header logo Skye Player should NOT be in rendered calls
        self.assertNotIn("Skye Player", rendered_text)
        # Footer help text should NOT be in rendered calls
        self.assertNotIn("space pause", rendered_text)

        # In Zen mode, queue starts at top (row 0)
        queue_calls = [call for call in scr.calls if "Track One" in call[2]]
        self.assertEqual(len(queue_calls), 1)
        self.assertEqual(queue_calls[0][0], 0)

    def test_inline_art_toggle(self):
        ui = {}
        status = {}
        _now_key(ord("a"), "now", status, ui)
        self.assertTrue(ui.get("art_mode"))
        _now_key(ord("a"), "now", status, ui)
        self.assertFalse(ui.get("art_mode"))


if __name__ == "__main__":
    unittest.main()



