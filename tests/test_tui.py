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
    _display_width,
    _draw,
    _draw_amp,
    _draw_header,
    _draw_home,
    _draw_lyrics,
    _draw_mini_player,
    _draw_queue,
    _get_live_position,
    _home_key,
    _now_key,
    _pad_to_width,
    _search_key,
    _truncate_to_width,
)


class MockStdscr:
    def __init__(self, h=30, w=100):
        self.h = h
        self.w = w
        self.calls = []

    def addstr(self, y, x, string, *args):
        if y < 0 or y >= self.h:
            raise curses.error(f"Row {y} out of bounds (height={self.h})")
        dw = _display_width(string)
        if x < 0 or x + dw > self.w:
            raise curses.error(f"Col {x} + disp_w {dw} exceeds width {self.w}")
        self.calls.append((y, x, string, args))

    def addch(self, y, x, ch, *args):
        if y < 0 or y >= self.h:
            raise curses.error(f"Row {y} out of bounds (height={self.h})")
        if x < 0 or x >= self.w:
            raise curses.error(f"Col {x} exceeds width {self.w}")
        self.calls.append((y, x, str(ch), args))


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


class TestVisualizerContainment(unittest.TestCase):
    def setUp(self):
        self._orig_color_pair = getattr(curses, "color_pair", None)
        curses.color_pair = lambda n: n

    def tearDown(self):
        if self._orig_color_pair is not None:
            curses.color_pair = self._orig_color_pair

    def test_all_visualizer_modes_strict_container_containment(self):
        """Verify that every one of the 12 visualizer modes renders strictly
        within viz_top to viz_top + viz_h - 1 and within columns 0 to w - 2."""
        status = {
            "state": "playing",
            "title": "Neon Horizon",
            "volume": 85,
            "duration": 200.0,
            "position": 50.0,
        }
        viz_top = 4
        viz_h = 5
        W = 100
        H = 30

        for mode in _VIZ_MODES:
            with self.subTest(mode=mode):
                scr = MockStdscr(h=H, w=W)
                ui = {"viz_mode": mode}
                _draw_amp(scr, status, amp_t=1.45, w=W, ui=ui, viz_top=viz_top, viz_h=viz_h, total_h=H)

                self.assertGreater(len(scr.calls), 0, f"Mode {mode} produced no render calls")
                for y, x, string, *extra in scr.calls:
                    self.assertGreaterEqual(
                        y, viz_top,
                        f"Mode {mode} wrote above container at row {y} (viz_top={viz_top})"
                    )
                    self.assertLess(
                        y, viz_top + viz_h,
                        f"Mode {mode} wrote below container at row {y} (viz_bottom={viz_top + viz_h})"
                    )
                    self.assertGreaterEqual(x, 0)
                    dw = _display_width(string)
                    self.assertLessEqual(
                        x + dw, W - 1,
                        f"Mode {mode} wrote past right edge: col {x} + width {dw} = {x + dw} > {W - 1}"
                    )

    def test_all_modes_paused_state_containment(self):
        """Verify that paused state across all modes keeps badges and visualizers strictly inside boundaries."""
        status = {
            "state": "paused",
            "title": "Neon Horizon",
            "volume": 80,
            "duration": 200.0,
            "position": 50.0,
        }
        viz_top = 4
        viz_h = 5
        W = 90
        H = 28

        for mode in _VIZ_MODES:
            with self.subTest(mode=mode):
                scr = MockStdscr(h=H, w=W)
                ui = {"viz_mode": mode}
                _draw_amp(scr, status, amp_t=2.0, w=W, ui=ui, viz_top=viz_top, viz_h=viz_h, total_h=H)

                for y, x, string, *extra in scr.calls:
                    self.assertGreaterEqual(y, viz_top)
                    self.assertLess(y, viz_top + viz_h)
                    self.assertLessEqual(x + _display_width(string), W - 1)

    def test_idle_and_loading_states_containment(self):
        """Verify that idle and loading states never exceed container boundaries."""
        viz_top = 4
        viz_h = 5
        W = 85
        H = 25

        for state in ("idle", "loading"):
            scr = MockStdscr(h=H, w=W)
            status = {"state": state, "volume": 70}
            ui = {"viz_mode": "spectrum"}
            _draw_amp(scr, status, amp_t=0.5, w=W, ui=ui, viz_top=viz_top, viz_h=viz_h, total_h=H)

            for y, x, string, *extra in scr.calls:
                self.assertGreaterEqual(y, viz_top)
                self.assertLess(y, viz_top + viz_h)
                self.assertLessEqual(x + _display_width(string), W - 1)

    def test_mode_switch_clears_ghost_artifacts(self):
        """Switching from a taller mode (spectrum: 5 rows) to a shorter mode (vu: 2-3 rows)
        must cleanly blank all rows upfront so no leftover artifacts remain."""
        scr = MockStdscr(h=30, w=100)
        status = {"state": "playing", "volume": 80}
        viz_top = 4
        viz_h = 5

        # Render spectrum first
        _draw_amp(scr, status, amp_t=1.0, w=100, ui={"viz_mode": "spectrum"}, viz_top=viz_top, viz_h=viz_h, total_h=30)
        # Now render vu on the same screen (simulating frame updates)
        scr_calls_len = len(scr.calls)
        _draw_amp(scr, status, amp_t=1.1, w=100, ui={"viz_mode": "vu"}, viz_top=viz_top, viz_h=viz_h, total_h=30)

        # Ensure all rows in viz_top..viz_top+viz_h were written with blank spaces during vu frame
        vu_calls = scr.calls[scr_calls_len:]
        cleared_rows = {call[0] for call in vu_calls if call[2] == " " * (100 - 2)}
        expected_rows = set(range(viz_top, viz_top + viz_h))
        self.assertTrue(expected_rows.issubset(cleared_rows), "All visualizer rows must be cleared upfront on each frame")


class TestContainerClippingAndWideUnicode(unittest.TestCase):
    def setUp(self):
        self._orig_color_pair = getattr(curses, "color_pair", None)
        curses.color_pair = lambda n: n

    def tearDown(self):
        if self._orig_color_pair is not None:
            curses.color_pair = self._orig_color_pair

    def test_queue_wide_unicode_never_crosses_divider(self):
        """Track titles with wide CJK, devanagari, or emojis must never overflow avail_w."""
        scr = MockStdscr(h=30, w=100)
        left_w = 46
        status = {
            "current_index": 0,
            "queue_len": 3,
            "queue": [
                {"title": "こんにちは世界 - 超長タイトルテスト用テキストです", "channel": "チャンネルアーティスト", "duration": 180.0},
                {"title": "⚡🔥🚀 Extreme Bass Drop · DJ Ultra Hyper Boost 🎧💥", "channel": "DJ Thunder ⚡", "duration": 240.0},
                {"title": "तेरे बिना - बहुत लम्बा गाना जो कभी खत्म नहीं होता", "channel": "ए आर रहमान", "duration": 310.0},
            ],
        }
        _draw_queue(scr, status, h=30, w=100, qsel=0, top=6, left=0, max_w=left_w, bottom=29)

        for y, x, string, *extra in scr.calls:
            dw = _display_width(string)
            self.assertLessEqual(
                x + dw, left_w,
                f"Queue row at y={y} exceeded divider left_w={left_w}: col {x} + disp_w {dw} = {x + dw}"
            )

    def test_lyrics_wide_unicode_never_crosses_right_margin(self):
        """Lyrics with CJK characters and karaoke highlight must strictly stay within right pane."""
        scr = MockStdscr(h=30, w=100)
        left_w = 46
        right_w = 100 - left_w - 1  # 53
        left = left_w + 1  # 47
        status = {"state": "playing", "position": 12.5, "duration": 200.0}
        ui = {
            "lyr_lines": [
                {"start": 10.0, "end": 15.0, "text": "桜の花びらが舞い散る夜空に君を想うよ", "synced": True},
                {"start": 15.0, "end": 20.0, "text": "長い長い道のりを歩いてここまで辿り着いた", "synced": True},
            ],
            "lyr_offset": 0.0,
        }
        _draw_lyrics(scr, status, ui, h=30, w=100, top=6, left=left, max_w=right_w, bottom=29)

        for y, x, string, *extra in scr.calls:
            dw = _display_width(string)
            self.assertGreaterEqual(x, left, f"Lyrics wrote into left pane at col {x}")
            self.assertLessEqual(
                x + dw, 100 - 1,
                f"Lyrics exceeded screen right margin: col {x} + disp_w {dw} = {x + dw} > {100 - 1}"
            )

    def test_header_wide_unicode_never_wraps(self):
        """Header row 0..3 with emojis and wide CJK titles must never exceed w - 2."""
        scr = MockStdscr(h=30, w=80)
        status = {
            "state": "playing",
            "title": "🎵 超長特大タイトル 🌸 桜の花が咲く頃に 🎧⚡ REMIX ULTRA BASS",
            "position": 125.0,
            "duration": 340.0,
            "volume": 90,
            "current_index": 0,
            "queue_len": 5,
            "dj_mode": True,
            "shuffle": True,
            "repeat": "all",
        }
        _draw_header(scr, status, w=80, amp_t=1.0)

        for y, x, string, *extra in scr.calls:
            dw = _display_width(string)
            self.assertLessEqual(
                x + dw, 80 - 1,
                f"Header row {y} exceeded terminal width: col {x} + disp_w {dw} = {x + dw} > {80 - 1}"
            )

    def test_full_draw_dual_pane_all_modes_no_container_bleed(self):
        """Run full _draw across all 12 visualizer modes in dual-pane studio layout:
        verifies that every screen element stays strictly within its container."""
        W = 100
        H = 30
        left_w = int(W * 0.46)  # 46
        status = {
            "state": "playing",
            "title": "テスト楽曲 - Wide Unicode & Emoji 🚀",
            "position": 15.0,
            "duration": 180.0,
            "volume": 80,
            "current_index": 0,
            "queue_len": 2,
            "queue": [
                {"title": "Track 1 - 日本語タイトル", "channel": "アーティスト", "duration": 180.0},
                {"title": "Track 2 - English Title", "channel": "Artist 2", "duration": 210.0},
            ],
        }

        for mode in _VIZ_MODES:
            with self.subTest(mode=mode):
                scr = MockStdscr(h=H, w=W)
                ui = {
                    "layout": "studio",
                    "viz_mode": mode,
                    "lyr_lines": [
                        {"start": 10.0, "end": 20.0, "text": "Karaoke active line 歌う", "synced": True}
                    ],
                    "qsel": 0,
                }
                _draw(
                    scr, status, H, W, "now",
                    sq="", sresults=[], ssel=0, ssearching=False, smsg="",
                    amp_t=1.5, ui=ui
                )

                # Check divider │ at col left_w
                dividers = [call for call in scr.calls if call[1] == left_w and call[2] == "│"]
                self.assertGreater(len(dividers), 0)

                # Check that no calls ever exceed W - 1 in display width
                for y, x, string, *extra in scr.calls:
                    dw = _display_width(string)
                    self.assertLessEqual(
                        x + dw, W - 1,
                        f"In mode {mode}, call at row {y}, col {x} with width {dw} exceeds {W - 1}"
                    )

class TestLaunchpadHome(unittest.TestCase):
    def setUp(self):
        self._orig_color_pair = getattr(curses, "color_pair", None)
        curses.color_pair = lambda n: n

    def tearDown(self):
        if self._orig_color_pair is not None:
            curses.color_pair = self._orig_color_pair

    def test_draw_home_bounds(self):
        sizes = [(30, 100), (24, 80), (16, 60), (12, 40)]
        for h, w in sizes:
            with self.subTest(size=f"{h}x{w}"):
                scr = MockStdscr(h=h, w=w)
                ui = {"viz_toast": "TEST TOAST"}
                status = {"state": "idle"}
                _draw_home(scr, status, h=h, w=w, amp_t=1.0, ui=ui)
                self.assertGreater(len(scr.calls), 0)
                for y, x, string, *extra in scr.calls:
                    dw = _display_width(string)
                    self.assertLessEqual(
                        x + dw, w - 1,
                        f"Size {h}x{w}: Call at row {y}, col {x} with width {dw} exceeds {w - 1}"
                    )

    def test_draw_home_content(self):
        scr = MockStdscr(h=30, w=100)
        ui = {}
        status = {"state": "idle"}
        _draw_home(scr, status, h=30, w=100, amp_t=1.0, ui=ui)
        all_text = " ".join(call[2] for call in scr.calls)
        self.assertIn("S K Y E   M U S I C   O S", all_text)
        self.assertIn("SPOTLIGHT SEARCH", all_text)
        self.assertIn("STANDBY AUDIO RADAR", all_text)
        self.assertIn("Quick Moods", all_text)

    def test_home_key_navigation_and_actions(self):
        from unittest.mock import patch
        ui = {}
        status = {"state": "idle"}

        # ESC and Tab switch to now
        self.assertEqual(_home_key(27, "home", status, ui, "", [], 0, False, "", {})[0], "now")
        self.assertEqual(_home_key(9, "home", status, ui, "", [], 0, False, "", {})[0], "now")

        # 'q' quits
        self.assertEqual(_home_key(ord("q"), "home", status, ui, "", [], 0, False, "", {})[0], "quit")

        # 't' switches to theme
        self.assertEqual(_home_key(ord("t"), "home", status, ui, "", [], 0, False, "", {})[0], "theme")

        # '1' - '5' triggers mood and switches to now
        with patch("tune.tui._bg_send") as mock_bg:
            res = _home_key(ord("1"), "home", status, ui, "", [], 0, False, "", {})
            self.assertEqual(res[0], "now")
            mock_bg.assert_called_with("mood", "lofi beats chill")

        with patch("tune.tui._bg_send") as mock_bg:
            res = _home_key(ord("2"), "home", status, ui, "", [], 0, False, "", {})
            self.assertEqual(res[0], "now")
            mock_bg.assert_called_with("mood", "synthwave retrowave 80s")

        with patch("tune.tui._bg_send") as mock_bg:
            res = _home_key(ord("r"), "home", status, ui, "", [], 0, False, "", {})
            self.assertEqual(res[0], "now")
            mock_bg.assert_called_with("radio")

        with patch("tune.tui._bg_send") as mock_bg:
            res = _home_key(ord("f"), "home", status, ui, "", [], 0, False, "", {})
            self.assertEqual(res[0], "now")
            mock_bg.assert_called_with("favs", "play")

        with patch("tune.tui._bg_send") as mock_bg:
            res = _home_key(ord("j"), "home", status, ui, "", [], 0, False, "", {})
            self.assertEqual(res[0], "now")
            mock_bg.assert_called_with("dj", "")

        with patch("tune.tui._bg_send") as mock_bg:
            res = _home_key(ord("d"), "home", status, ui, "", [], 0, False, "", {})
            self.assertEqual(res[0], "now")
            mock_bg.assert_called_with("focus", "lofi beats")

    def test_home_key_search_transitions(self):
        from unittest.mock import patch
        ui = {}
        status = {"state": "idle"}

        # '/' switches to search
        res = _home_key(ord("/"), "home", status, ui, "", [], 0, False, "", {})
        self.assertEqual(res[0], "search")
        self.assertTrue(ui.get("_return_to_home"))

        # Enter (10) switches to search
        ui.clear()
        res = _home_key(10, "home", status, ui, "", [], 0, False, "", {})
        self.assertEqual(res[0], "search")
        self.assertTrue(ui.get("_return_to_home"))

        # Printable character transitions to search immediately with that query
        ui.clear()
        with patch("tune.tui._start_suggest"):
            res = _home_key(ord("s"), "home", status, ui, "", [], 0, False, "", {})
            self.assertEqual(res[0], "search")
            self.assertEqual(res[1], "s")
            self.assertTrue(ui.get("_return_to_home"))

    def test_now_key_to_home_transitions(self):
        ui = {}
        # 'h' switches to home
        status = {"state": "playing", "queue": [{"title": "Track 1"}]}
        self.assertEqual(_now_key(ord("h"), "now", status, ui), "home")

        # ESC with empty queue switches to home
        status_empty = {"state": "idle", "queue": []}
        self.assertEqual(_now_key(27, "now", status_empty, ui), "home")

        # ESC with active queue returns quit
        self.assertEqual(_now_key(27, "now", status, ui), "quit")

    def test_cli_smart_routing(self):
        from unittest.mock import patch
        import tune.cli as cli

        with patch("tune.cli.ensure_daemon"), \
             patch("tune.cli.send_cmd", return_value={"ok": True, "data": {"title": "Starboy"}}), \
             patch("sys.stdout"):
            code = cli.run(["starboy"])
            self.assertEqual(code, 0)

        with patch("tune.cli.ensure_daemon"), \
             patch("tune.tui.run") as mock_tui:
            code = cli.run(["home"])
            self.assertEqual(code, 0)
            mock_tui.assert_called_with(initial_mode="home")


if __name__ == "__main__":
    unittest.main()
