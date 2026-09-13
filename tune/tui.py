"""Full-screen TUI for tune (curses).

A thin client: polls `status` every ~0.25s and sends key commands. Modes:

- **NOW** — now-playing header, progress bar, queue, transport keys.
- **SEARCH** — press `/` to search YouTube from inside the UI; pick a result
  to play or add to the queue.
- **THEME** — press `t` to browse and apply color themes live.

Quitting the TUI (q / Esc in NOW mode) stops playback; the daemon stays alive
so re-opening tune is instant.
"""

from __future__ import annotations

import curses
import math
import sys
import termios
import threading
import time
import tty

from .client import send_cmd

_STATE_MARK = {"playing": "⚔️", "paused": "🗡️", "loading": "⚡", "idle": "👺"}
_STATE_KAOMOJI = {
    "playing": "TOTAL FOCUS",
    "paused": "SHEATHED",
    "loading": "TRAINING",
    "idle": "STANDBY",
}
_BREATHING_TECHNIQUES = [
    "WATER BREATHING I",
    "SUN BREATHING XIII",
    "THUNDER BREATHING",
    "FLAME BREATHING I",
    "BEAST BREATHING I",
]


_REPEAT_ORDER = ["off", "all", "one"]
_SEARCH_LIMIT = 80
_AMP_BLOCKS   = "▁▂▃▄▅▆▇█"
# ── Visualizer layout constants ──────────────────────────────────────────────
_VIZ_ROWS  = 5   # number of rows the spectrum occupies (rows 4 … 4+VIZ_ROWS-1)
_VIZ_ROW   = 4   # first row of the visualizer
_SEP_ROW   = _VIZ_ROW + _VIZ_ROWS      # QUEUE separator row  (= 9)
_QUEUE_ROW = _SEP_ROW + 1              # queue list starts here (= 10)
# Palette per theme: (header, current-track, accent, error, title, list).
# `list` colors body text (queue / search / lyrics / help). Error stays red in
# most themes because red is a semantic color.
_THEMES = {
    "default":        (curses.COLOR_GREEN, curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_CYAN),
    "tanjiro":       (curses.COLOR_GREEN, curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_CYAN),
    "rengoku":       (curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_WHITE),
    "zenitsu":       (curses.COLOR_YELLOW, curses.COLOR_WHITE, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_WHITE),
    "giyuu":         (curses.COLOR_BLUE, curses.COLOR_CYAN, curses.COLOR_WHITE, curses.COLOR_RED, curses.COLOR_BLUE, curses.COLOR_WHITE),
    "akaza":         (curses.COLOR_MAGENTA, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_RED, curses.COLOR_MAGENTA, curses.COLOR_WHITE),
    "mono":          (curses.COLOR_WHITE, curses.COLOR_WHITE, curses.COLOR_WHITE, curses.COLOR_WHITE, curses.COLOR_WHITE, curses.COLOR_WHITE),
    # A–Z
    "amber":       (curses.COLOR_YELLOW, curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_YELLOW),
    "blue":        (curses.COLOR_BLUE, curses.COLOR_CYAN, curses.COLOR_WHITE, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_WHITE),
    "candy":       (curses.COLOR_MAGENTA, curses.COLOR_CYAN, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_MAGENTA, curses.COLOR_YELLOW),
    "dracula":     (curses.COLOR_MAGENTA, curses.COLOR_GREEN, curses.COLOR_CYAN, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_CYAN),
    "emerald":     (curses.COLOR_GREEN, curses.COLOR_CYAN, curses.COLOR_WHITE, curses.COLOR_RED, curses.COLOR_GREEN, curses.COLOR_WHITE),
    "forest":      (curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_WHITE, curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_GREEN),
    "grape":       (curses.COLOR_MAGENTA, curses.COLOR_GREEN, curses.COLOR_WHITE, curses.COLOR_RED, curses.COLOR_MAGENTA, curses.COLOR_CYAN),
    "honey":       (curses.COLOR_YELLOW, curses.COLOR_WHITE, curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_YELLOW),
    "ice":         (curses.COLOR_CYAN, curses.COLOR_WHITE, curses.COLOR_BLUE, curses.COLOR_RED, curses.COLOR_CYAN, curses.COLOR_CYAN),
    "jungle":      (curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_CYAN, curses.COLOR_RED, curses.COLOR_GREEN, curses.COLOR_GREEN),
    "krypton":     (curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_WHITE, curses.COLOR_RED, curses.COLOR_GREEN, curses.COLOR_GREEN),
    "lavender":    (curses.COLOR_MAGENTA, curses.COLOR_WHITE, curses.COLOR_CYAN, curses.COLOR_RED, curses.COLOR_MAGENTA, curses.COLOR_CYAN),
    "midnight":    (curses.COLOR_BLUE, curses.COLOR_WHITE, curses.COLOR_CYAN, curses.COLOR_RED, curses.COLOR_BLUE, curses.COLOR_CYAN),
    "ninja":       (curses.COLOR_WHITE, curses.COLOR_GREEN, curses.COLOR_CYAN, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_WHITE),
    "ocean":       (curses.COLOR_BLUE, curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_CYAN, curses.COLOR_WHITE),
    "plum":        (curses.COLOR_MAGENTA, curses.COLOR_YELLOW, curses.COLOR_WHITE, curses.COLOR_RED, curses.COLOR_MAGENTA, curses.COLOR_CYAN),
    "quantum":     (curses.COLOR_CYAN, curses.COLOR_MAGENTA, curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_CYAN, curses.COLOR_CYAN),
    "rose":        (curses.COLOR_RED, curses.COLOR_MAGENTA, curses.COLOR_RED, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_WHITE),
    "sunset":      (curses.COLOR_MAGENTA, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_WHITE),
    "tokyo":       (curses.COLOR_MAGENTA, curses.COLOR_CYAN, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_WHITE),
    "ultraviolet": (curses.COLOR_MAGENTA, curses.COLOR_CYAN, curses.COLOR_WHITE, curses.COLOR_RED, curses.COLOR_CYAN, curses.COLOR_WHITE),
    "violet":      (curses.COLOR_MAGENTA, curses.COLOR_YELLOW, curses.COLOR_CYAN, curses.COLOR_RED, curses.COLOR_MAGENTA, curses.COLOR_CYAN),
    "watermelon":  (curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_MAGENTA, curses.COLOR_RED, curses.COLOR_GREEN, curses.COLOR_GREEN),
    "xray":        (curses.COLOR_CYAN, curses.COLOR_WHITE, curses.COLOR_MAGENTA, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_CYAN),
    "yellow":      (curses.COLOR_YELLOW, curses.COLOR_WHITE, curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_YELLOW),
    "zen":         (curses.COLOR_GREEN, curses.COLOR_WHITE, curses.COLOR_CYAN, curses.COLOR_RED, curses.COLOR_WHITE, curses.COLOR_GREEN),
}


def _load_theme() -> str:
    try:
        from .config import Config  # honors XDG_CONFIG_HOME like the rest of the app
        theme = Config().get("theme", "default")
        return theme if theme in _THEMES else "default"
    except Exception:
        return "default"


def _apply_theme(name: str) -> None:
    """(Re)initialize the color pairs for a theme. Safe to call any time, so
    the whole screen re-themes live while the user browses the picker."""
    colors = _THEMES.get(name, _THEMES["default"])
    if not curses.has_colors():
        return
    header, current, accent, error, title, list = colors
    curses.init_pair(1, header, -1)   # header bar
    curses.init_pair(2, current, -1)  # current track in the queue
    curses.init_pair(3, accent, -1)   # progress / time / meta / equalizer / input
    curses.init_pair(4, error, -1)    # errors / unavailable
    curses.init_pair(5, title, -1)    # now-playing title
    curses.init_pair(6, list, -1)     # body text (queue / search / lyrics / help)
_NOW_HELP = ("space pause · e eq · D download · M radio · tab layout · n/p next/prev · v viz · ↑/↓ select · d remove · enter jump · "
             "+/- vol · [ ] speed · ←/→ seek · ,/. sync · l lyrics · s shuffle · r repeat · "
             "t theme · / search · q quit")
_SEARCH_HELP = "enter play · tab add to queue · ↑/↓ move · backspace edit · esc back"
_VIZ_MODES = ("spectrum", "stereo", "wave", "bars", "matrix", "vu", "oscilloscope")


def _visualizer() -> str:
    try:
        from .config import Config
        v = str(Config().get("visualizer", "spectrum")).strip().lower()
        if v == "eq":
            return "spectrum"
        return v if v in _VIZ_MODES else "spectrum"
    except Exception:
        return "spectrum"


def _fmt_time(sec: float | None) -> str:
    if sec is None:
        return "LIVE"
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def run() -> None:
    curses.wrapper(_main)


def _main(stdscr) -> None:
    try:
        _loop(stdscr)
    except KeyboardInterrupt:
        pass


def _loop(stdscr) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)  # translate escape sequences (arrows, etc.)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        _apply_theme(_load_theme())

    status: dict = {"state": "idle"}

    # Background status poller: the UI never blocks on the daemon, even if it
    # is slow or down. The render loop only reads `status`.
    def _poller() -> None:
        nonlocal status
        while True:
            try:
                resp = send_cmd("status")
                if resp.get("ok"):
                    data = resp["data"]
                    data["_mono_t"] = time.monotonic()
                    data["_pos_base"] = float(data.get("position", 0.0) or 0.0)
                    status = data
                else:
                    status = {"state": "idle", "error": resp.get("error")}
            except Exception:
                status = {"state": "idle", "error": "daemon unreachable"}
            time.sleep(0.035 if status.get("state") == "playing" else 0.25)

    threading.Thread(target=_poller, daemon=True).start()

    # search-mode state
    mode = "now"                      # "now" | "search" | "cmd" | "theme" | "filter"
    sq, sresults, ssel = "", [], 0
    ssearching, smsg = False, ""
    sbucket: dict = {}
    cmdq, cmdmsg = "", ""
    qfilter = ""                      # queue filter (f key)

    # theme-picker state
    theme_names = list(_THEMES)
    theme_saved = _load_theme()
    theme_sel = theme_names.index(theme_saved) if theme_saved in theme_names else 0

    # UI state: queue selection, lyrics pane, layout, amplifier clock
    ui = {"qsel": 0, "qsel_follow": True, "layout": "studio",
          "lyr_on": False, "lyr_lines": [], "lyr_loading": False, "lyr_note": "",
          "lyr_url": "", "lyr_track_id": "", "lyr_fetched_track": "",
          "lyr_scroll": 0, "lyr_offset": 0.0}
    amp_t = 0.0
    last_tick = time.monotonic()

    while True:
        now_t = time.monotonic()
        dt = min(0.25, now_t - last_tick)
        last_tick = now_t
        st = status.get("state")
        if st == "playing":
            amp_t += dt
        elif st == "loading":
            amp_t += dt * 0.4  # slow crawl while buffering
        # paused / idle: freeze the amplifier

        # Real-time lyrics tracking: as soon as track changes, update lyrics instantly
        cur_url = status.get("url") or ""
        cur_track_id = cur_url
        if cur_track_id and status.get("state") != "loading":
            if cur_track_id != ui.get("lyr_fetched_track"):
                ui["lyr_fetched_track"] = cur_track_id
                ui["lyr_track_id"] = cur_track_id
                if ui.get("layout") in ("studio", "lyrics") or ui.get("lyr_on"):
                    _update_lyrics(ui, status)
                else:
                    ui["lyr_url"] = cur_url
                    ui["lyr_lines"] = []
                    ui["lyr_note"] = ""
                    ui["lyr_loading"] = False
                    ui["lyr_scroll"] = 0

        # Safety guard: clear loading state if network or daemon worker takes > 7.0s
        if ui.get("lyr_loading") and (now_t - ui.get("lyr_loading_t", now_t)) > 7.0:
            ui["lyr_loading"] = False
            if not ui.get("lyr_lines") and not ui.get("lyr_note"):
                ui["lyr_note"] = "no lyrics found for this track"

        # queue selection follows the current track until the user moves it
        if (mode in ("now", "filter") and ui["qsel_follow"] and not qfilter
                and status.get("current_index", -1) >= 0):
            ui["qsel"] = status["current_index"]

        # collect a finished background search
        if sbucket.get("done"):
            ssearching = False
            sresults = sbucket.get("results", [])
            smsg = sbucket.get("msg", "")
            ssel = 0
            sbucket = {}

        stdscr.erase()
        h, w = stdscr.getmaxyx()
        if h > 1 and w > 1:
            _draw(stdscr, status, h, w, mode, sq, sresults, ssel, ssearching, smsg,
                  amp_t, ui, cmdq, theme_sel, theme_names, qfilter, sbucket)
        stdscr.refresh()

        ch = stdscr.getch()
        ch = _resolve_key(stdscr, ch)
        # In DJ mode, only 'now' key handler runs — filter/search/cmd are disabled
        if status.get("dj_mode") and mode not in ("now", "quit"):
            mode = "now"
        if mode == "cmd":
            mode, cmdq, cmdmsg = _cmd_key(ch, mode, cmdq, cmdmsg)
        elif mode == "search":
            mode, sq, sresults, ssel, ssearching, smsg, sbucket = _search_key(
                ch, mode, sq, sresults, ssel, ssearching, smsg, sbucket)
        elif mode == "theme":
            mode, theme_sel, theme_saved = _theme_key(
                ch, mode, theme_sel, theme_names, theme_saved)
        elif mode == "filter":
            mode, qfilter = _filter_key(ch, mode, qfilter)
        else:
            mode = _now_key(ch, mode, status, ui, qfilter)

        if ui.pop("art", False):
            _show_art(stdscr)
        if mode == "quit":
            _stop_on_quit()
            break

        curses.napms(35)


# --- key handling -----------------------------------------------------------

def _resolve_key(stdscr, ch: int) -> int:
    """Resolve an ESC-prefixed key under nodelay, where curses may not.

    Arrow keys arrive as escape sequences (e.g. ESC [ B for Down). With
    nodelay(True) curses can return just the ESC, so we peek briefly for the
    rest of the sequence and map it to a KEY_* code. A lone ESC passes through.
    """
    if ch != 27:
        return ch
    time.sleep(0.03)  # give any sequence bytes time to arrive
    nxt = stdscr.getch()
    if nxt == -1:
        return 27
    if nxt in (ord("["), ord("O")):
        fin = stdscr.getch()
        return {ord("A"): curses.KEY_UP, ord("B"): curses.KEY_DOWN,
                ord("C"): curses.KEY_RIGHT, ord("D"): curses.KEY_LEFT}.get(fin, 27)
    return 27


def _bg_send(verb: str, arg: object = "") -> None:
    """Fire a command without blocking the UI (results arrive via status poll)."""
    threading.Thread(target=lambda: send_cmd(verb, arg), daemon=True).start()


def _stop_on_quit() -> None:
    """Stop playback when the user quits the TUI. Synchronous (not _bg_send)
    so the command is delivered before this process exits."""
    try:
        send_cmd("stop")
    except Exception:
        pass  # daemon already gone / unreachable; nothing to stop


def _cycle_viz_mode(ui: dict) -> None:
    cur_m = ui.get("viz_mode") or _visualizer()
    if cur_m not in _VIZ_MODES:
        cur_m = "spectrum"
    nxt_m = _VIZ_MODES[(_VIZ_MODES.index(cur_m) + 1) % len(_VIZ_MODES)]
    ui["viz_mode"] = nxt_m
    ui["viz_toast"] = f"visualizer: {nxt_m.upper()}"
    ui["viz_toast_t"] = time.monotonic()
    # Reset physics caches so the new mode starts cleanly
    ui.pop("viz_bands", None)
    ui.pop("viz_peaks", None)
    ui.pop("viz_holds", None)
    _bg_send("config", f"visualizer {nxt_m}")


def _now_key(ch: int, mode: str, status: dict, ui: dict, qfilter: str = "") -> str:
    if ch == -1:
        return mode

    # Tab key: cycle layouts (studio -> queue -> lyrics)
    if ch == 9:
        cur_l = ui.get("layout", "studio")
        order = ["studio", "queue", "lyrics"]
        nxt_l = order[(order.index(cur_l) + 1) % len(order)] if cur_l in order else "studio"
        ui["layout"] = nxt_l
        ui["lyr_on"] = (nxt_l == "lyrics")
        ui["viz_toast"] = f"LAYOUT: {nxt_l.upper()}"
        ui["viz_toast_t"] = time.monotonic()
        if nxt_l in ("studio", "lyrics"):
            cur_url = status.get("url") or ""
            cur_track_id = cur_url or status.get("title") or ""
            if (not ui.get("lyr_lines") and not ui.get("lyr_loading")) or cur_track_id != ui.get("lyr_fetched_track"):
                _update_lyrics(ui, status)
        return mode

    # Dedicated full-screen lyrics mode
    if ui.get("layout") == "lyrics" or (ui.get("lyr_on") and ui.get("layout") != "studio"):
        if ch in (ord("l"), 27):
            ui["layout"] = "studio"
            ui["lyr_on"] = False
        elif ch == ord("r"):
            ui["lyr_fetched_track"] = ""
            _update_lyrics(ui, status)
        elif ch == ord("q"):
            mode = "quit"
        elif ch == ord("v"):
            _cycle_viz_mode(ui)
        elif ch == ord(" "):
            _bg_send("toggle")
        elif ch in (ord("n"),):
            _bg_send("next")
        elif ch == ord("p"):
            _bg_send("prev")
        elif ch in (ord("+"), ord("=")):
            _bg_send("volume", "+5")
        elif ch == ord("-"):
            _bg_send("volume", "-5")
        elif ch == curses.KEY_LEFT:
            _bg_send("seek", "-5")
        elif ch == curses.KEY_RIGHT:
            _bg_send("seek", "+5")
        elif ch == ord(","):
            ui["lyr_offset"] = round(ui.get("lyr_offset", 0.0) - 0.05, 2)
            ui["viz_toast"] = f"LYRICS SYNC: {ui['lyr_offset']:+.2f}s"
            ui["viz_toast_t"] = time.monotonic()
        elif ch == ord("<"):
            ui["lyr_offset"] = round(ui.get("lyr_offset", 0.0) - 0.25, 2)
            ui["viz_toast"] = f"LYRICS SYNC: {ui['lyr_offset']:+.2f}s"
            ui["viz_toast_t"] = time.monotonic()
        elif ch == ord("."):
            ui["lyr_offset"] = round(ui.get("lyr_offset", 0.0) + 0.05, 2)
            ui["viz_toast"] = f"LYRICS SYNC: {ui['lyr_offset']:+.2f}s"
            ui["viz_toast_t"] = time.monotonic()
        elif ch == ord(">"):
            ui["lyr_offset"] = round(ui.get("lyr_offset", 0.0) + 0.25, 2)
            ui["viz_toast"] = f"LYRICS SYNC: {ui['lyr_offset']:+.2f}s"
            ui["viz_toast_t"] = time.monotonic()
        elif ch == ord("o"):
            ui["lyr_offset"] = 0.0
            ui["viz_toast"] = "LYRICS SYNC: RESET (0.00s)"
            ui["viz_toast_t"] = time.monotonic()
        elif ch in (curses.KEY_UP, ord("k")):
            ui["lyr_scroll"] = max(0, ui.get("lyr_scroll", 0) - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            ui["lyr_scroll"] = ui.get("lyr_scroll", 0) + 1
        return mode

    vis = _visible_queue(status, qfilter)
    is_dj = status.get("dj_mode", False)

    if ch in (ord("q"), 27):
        mode = "quit"  # sentinel: quit the TUI and stop playback
    elif ch == ord("l"):
        if ui.get("layout") == "studio":
            ui["layout"] = "lyrics"
            ui["lyr_on"] = True
        else:
            ui["layout"] = "studio"
            ui["lyr_on"] = False
        cur_url = status.get("url") or ""
        cur_track_id = cur_url or status.get("title") or ""
        if (not ui.get("lyr_lines") and not ui.get("lyr_loading")) or cur_track_id != ui.get("lyr_fetched_track"):
            _update_lyrics(ui, status)
    elif ch == ord("r"):
        ui["lyr_fetched_track"] = ""
        _update_lyrics(ui, status)
    elif ch == ord(","):
        ui["lyr_offset"] = round(ui.get("lyr_offset", 0.0) - 0.05, 2)
        ui["viz_toast"] = f"LYRICS SYNC: {ui['lyr_offset']:+.2f}s"
        ui["viz_toast_t"] = time.monotonic()
    elif ch == ord("<"):
        ui["lyr_offset"] = round(ui.get("lyr_offset", 0.0) - 0.25, 2)
        ui["viz_toast"] = f"LYRICS SYNC: {ui['lyr_offset']:+.2f}s"
        ui["viz_toast_t"] = time.monotonic()
    elif ch == ord("."):
        ui["lyr_offset"] = round(ui.get("lyr_offset", 0.0) + 0.05, 2)
        ui["viz_toast"] = f"LYRICS SYNC: {ui['lyr_offset']:+.2f}s"
        ui["viz_toast_t"] = time.monotonic()
    elif ch == ord(">"):
        ui["lyr_offset"] = round(ui.get("lyr_offset", 0.0) + 0.25, 2)
        ui["viz_toast"] = f"LYRICS SYNC: {ui['lyr_offset']:+.2f}s"
        ui["viz_toast_t"] = time.monotonic()
    elif ch == ord("o"):
        ui["lyr_offset"] = 0.0
        ui["viz_toast"] = "LYRICS SYNC: RESET (0.00s)"
        ui["viz_toast_t"] = time.monotonic()
    elif ch == ord("v"):
        _cycle_viz_mode(ui)
    elif ch == ord("f"):
        if is_dj:
            _bg_send("dj", "fade")
            ui["viz_toast"] = "🎧 FADE → NEXT ✦"
            ui["viz_toast_t"] = time.monotonic()
        else:
            mode = "filter"

    elif ch == curses.KEY_UP:
        ui["qsel"] = max(0, ui["qsel"] - 1)
        ui["qsel_follow"] = False
    elif ch == curses.KEY_DOWN:
        ui["qsel"] = min(max(0, len(vis) - 1), ui["qsel"] + 1)
        ui["qsel_follow"] = False
    elif ch in (10, 13, curses.KEY_ENTER) and vis:
        _bg_send("playindex", str(vis[min(ui["qsel"], len(vis) - 1)][0] + 1))
        ui["qsel_follow"] = False
    elif ch in (ord("e"), ord("E")):
        if is_dj:
            _bg_send("dj", "filter")
            ui["viz_toast"] = "🎧 FILTER SWEEP ✦"
            ui["viz_toast_t"] = time.monotonic()
        else:
            _bg_send("eq", "next")
            ui["viz_toast"] = "✦ EQUALIZER PRESET CHANGED ✦"
            ui["viz_toast_t"] = time.monotonic()
    elif ch in (ord("b"), ord("B")) and is_dj:
        _bg_send("dj", "bass")
        ui["viz_toast"] = "🔊 BASS DROP ✦"
        ui["viz_toast_t"] = time.monotonic()
    elif ch == ord("F") and is_dj:
        _bg_send("dj", "fade")
        ui["viz_toast"] = "🎧 FADE → NEXT ✦"
        ui["viz_toast_t"] = time.monotonic()
    elif ch == ord("D"):
        _bg_send("download")
        ui["viz_toast"] = "✦ DOWNLOADING AUDIO... ✦"
        ui["viz_toast_t"] = time.monotonic()
    elif ch in (ord("j"), ord("J")):

        if is_dj:
            _bg_send("dj", "off")
            # Optimistic update: snap back to normal view immediately
            # (status poller will confirm within ~250ms)
            status["dj_mode"] = False
            ui["viz_toast"] = "✦ DJ MODE DISABLED ✦"
        else:
            _bg_send("dj", "")
            status["dj_mode"] = True
            ui["viz_toast"] = "🎧⚡ REAL DJ MODE ACTIVATED ✦"
        ui["viz_toast_t"] = time.monotonic()


    elif ch == ord("S"):
        _bg_send("dj", "scratch")
        ui["viz_toast"] = "🎧⚡ DJ SCRATCH FX ✦"
        ui["viz_toast_t"] = time.monotonic()
    elif ch == ord("M"):
        _bg_send("radio", "lofi")
        ui["viz_toast"] = "✦ SMART RADIO: LOFI MIX ✦"
        ui["viz_toast_t"] = time.monotonic()
    elif (ch == ord("d") or ch == ord("x") or ch == curses.KEY_DC) and vis:
        _bg_send("remove", str(vis[min(ui["qsel"], len(vis) - 1)][0] + 1))
        ui["qsel"] = max(0, min(ui["qsel"], max(0, len(vis) - 2)))
        ui["qsel_follow"] = False
    elif ch == ord(" "):
        _bg_send("toggle")
    elif ch in (ord("n"), ord("k")):
        _bg_send("next")
        ui["qsel_follow"] = True
    elif ch == ord("p"):
        _bg_send("prev")
        ui["qsel_follow"] = True
    elif ch in (ord("+"), ord("=")):
        _bg_send("volume", "+5")
    elif ch == ord("-"):
        _bg_send("volume", "-5")
    elif ch in (ord("["), ord("{")):
        _bg_send("speed", "0.9")
    elif ch in (ord("]"), ord("}")):
        _bg_send("speed", "1.1")
    elif ch == curses.KEY_RIGHT:
        _bg_send("seek", "+5")
    elif ch == curses.KEY_LEFT:
        _bg_send("seek", "-5")
    elif ch == ord("s"):
        if is_dj:
            _bg_send("dj", "scratch")
            ui["viz_toast"] = "🎧⚡ DJ SCRATCH FX ✦"
            ui["viz_toast_t"] = time.monotonic()
        else:
            _bg_send("shuffle")

    elif ch == ord("r"):
        _cycle_repeat(status)
    elif ch == ord("a"):
        ui["art"] = True
    elif ch in map(ord, "012345"):
        _bg_send("rate", str(ch - ord("0")))
    elif ch == curses.KEY_PPAGE:
        ui["lyr_scroll"] = max(0, ui.get("lyr_scroll", 0) - 4)
    elif ch == curses.KEY_NPAGE:
        ui["lyr_scroll"] = ui.get("lyr_scroll", 0) + 4
    elif ch == ord("t"):
        mode = "theme"
    elif ch == ord(":"):
        mode = "cmd"
    elif ch == ord("/"):
        mode = "search"
    return mode


def _cmd_key(ch: int, mode: str, cmdq: str, cmdmsg: str):
    if ch == -1:
        return mode, cmdq, cmdmsg
    if ch == 27:  # esc cancels
        return "now", "", cmdmsg
    if ch in (10, 13, curses.KEY_ENTER):
        cmd = cmdq.strip()
        if cmd:
            parts = cmd.split(None, 1)
            verb, arg = parts[0], parts[1] if len(parts) > 1 else ""
            if verb == "playlist":
                pa = cmd.split()
                if len(pa) >= 2:
                    _bg_send("playlist", [pa[1], pa[2] if len(pa) > 2 else ""])
                    cmdmsg = cmd
                else:
                    cmdmsg = "usage: playlist save|load|add|show|delete <name>"
            else:
                _bg_send(verb, arg)
                cmdmsg = cmd
        return "now", "", cmdmsg
    if ch in (curses.KEY_BACKSPACE, 127, 8):
        return mode, cmdq[:-1], cmdmsg
    if 32 <= ch < 127:
        return mode, cmdq + chr(ch), cmdmsg
    return mode, cmdq, cmdmsg


def _theme_key(ch, mode, sel, names, saved):
    """Theme picker: ↑/↓ preview live, enter applies+saves, esc reverts."""
    if ch == -1:
        return mode, sel, saved
    if ch in (27, ord("q")):  # esc / q cancels -> back to the saved theme
        _apply_theme(saved)
        return "now", sel, saved
    if ch in (10, 13, curses.KEY_ENTER):
        _bg_send("config", f"theme {names[sel]}")  # persist via the daemon
        _apply_theme(names[sel])
        return "now", sel, names[sel]
    if ch == curses.KEY_DOWN:
        sel = min(len(names) - 1, sel + 1)
        _apply_theme(names[sel])
    elif ch == curses.KEY_UP:
        sel = max(0, sel - 1)
        _apply_theme(names[sel])
    return mode, sel, saved


def _visible_queue(status: dict, qfilter: str) -> list[tuple[int, dict]]:
    """(real_index, track) pairs for the queue, filtered by a substring."""
    q = status.get("queue") or []
    needle = qfilter.strip().lower()
    if not needle:
        return [(i, t) for i, t in enumerate(q)]
    return [(i, t) for i, t in enumerate(q)
            if needle in (t.get("title") or "").lower()
            or needle in (t.get("query") or "").lower()]


def _filter_key(ch, mode, qfilter):
    """Queue filter: type to filter live, enter keeps it, esc clears+exits."""
    if ch == -1:
        return mode, qfilter
    if ch == 27:
        return "now", ""
    if ch in (10, 13, curses.KEY_ENTER):
        return "now", qfilter
    if ch in (curses.KEY_BACKSPACE, 127, 8):
        return mode, qfilter[:-1]
    if 32 <= ch < 127 and len(qfilter) < 60:
        return mode, qfilter + chr(ch)
    return mode, qfilter


def _update_lyrics(ui: dict, status: dict) -> None:
    """Fetch lyrics for the active track asynchronously with zero UI blocking."""
    cur_url = status.get("url") or ""
    if not cur_url:
        ui["lyr_url"] = ""
        ui["lyr_lines"] = []
        ui["lyr_note"] = "no track playing"
        ui["lyr_loading"] = False
        return

    # If already loading for this exact URL, avoid redundant duplicate workers
    if ui.get("lyr_loading") and ui.get("lyr_url") == cur_url:
        return

    cur_track_id = cur_url or status.get("title") or ""
    ui["lyr_url"] = cur_url
    ui["lyr_lines"] = []
    ui["lyr_loading"] = True
    ui["lyr_loading_t"] = time.monotonic()
    ui["lyr_note"] = ""
    ui["lyr_scroll"] = 0
    ui["lyr_fetched_track"] = cur_track_id

    target_url = cur_url

    def worker() -> None:
        try:
            resp = send_cmd("lyrics", target_url)
            if ui.get("lyr_url") != target_url:
                return  # track changed while fetching; discard stale result
            if resp.get("ok"):
                data = resp.get("data") or {}
                lines = data.get("lines", [])
                ui["lyr_lines"] = lines
                ui["lyr_note"] = data.get("note") or ("no lyrics found for this track" if not lines else "")
            else:
                ui["lyr_note"] = resp.get("error", "lyrics unavailable")
        except Exception as e:
            if ui.get("lyr_url") == target_url:
                ui["lyr_note"] = str(e)
        finally:
            if ui.get("lyr_url") == target_url:
                ui["lyr_loading"] = False

    threading.Thread(target=worker, daemon=True).start()


def _toggle_lyrics(ui: dict, status: dict) -> None:
    if ui["lyr_on"]:
        ui["lyr_on"] = False
        return
    ui["lyr_on"] = True
    cur_url = status.get("url") or ""
    cur_track_id = cur_url or status.get("title") or ""
    if (not ui.get("lyr_lines") and not ui.get("lyr_loading")) or cur_track_id != ui.get("lyr_fetched_track"):
        _update_lyrics(ui, status)


def _get_live_position(status: dict, ui: dict) -> float:
    """Return high-precision playback position with monotonic clock extrapolation and sync offset."""
    base = float(status.get("_pos_base", status.get("position", 0.0)) or 0.0)
    offset = float(ui.get("lyr_offset", 0.0) or 0.0)
    if status.get("state") == "playing":
        t0 = status.get("_mono_t")
        if t0 is not None:
            dt = max(0.0, time.monotonic() - t0)
            speed = float(status.get("speed", 1.0) or 1.0)
            return max(0.0, base + dt * speed + offset)
    return max(0.0, base + offset)


def _cur_lyr_line(status: dict, ui: dict, pos: float | None = None) -> int:
    if pos is None:
        pos = _get_live_position(status, ui)
    lines = ui.get("lyr_lines", [])
    if not lines:
        return 0
    # Before first vocal line: intro state (-1)
    first_start = lines[0].get("start")
    if first_start is not None and pos < first_start:
        return -1

    # Exact interval match
    for i, ln in enumerate(lines):
        start = ln.get("start")
        end = ln.get("end")
        if start is not None and end is not None and start <= pos <= end:
            return i
    # Nearest line before current position
    best = 0
    for i, ln in enumerate(lines):
        start = ln.get("start")
        if start is not None:
            if start <= pos:
                best = i
            else:
                break
    return best


def _show_art(stdscr) -> None:
    """Suspend curses, print truecolor album art, wait for a key, resume."""
    lines, err = [], None
    try:
        resp = send_cmd("art")
        if resp.get("ok"):
            lines = (resp.get("data") or {}).get("lines", [])
        if not lines:
            err = resp.get("error") or "no art available"
    except Exception as e:
        err = str(e)
    curses.endwin()
    if lines:
        print("\n".join(lines))
    else:
        print(f"tune: {err}")
    print("\n(press any key to return)")
    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        sys.stdin.read(1)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        time.sleep(0.5)
    stdscr.refresh()


def _search_key(ch, mode, sq, sresults, ssel, ssearching, smsg, sbucket):
    if ch == -1:
        pass
    elif ch == 27:  # esc
        if sq or sresults or ssearching:
            sq, sresults, ssel, smsg, ssearching, sbucket = "", [], 0, "", False, {}
        else:
            mode = "now"
    elif ch in (10, 13, curses.KEY_ENTER):
        q = _clean_query(sq)
        if sresults and not ssearching:
            _play_url(sresults[ssel]["url"])
            mode = "now"
        elif q and not ssearching:
            _start_search(q, sbucket)
            ssearching, smsg, sresults, ssel = True, "", [], 0
    elif ch == 9:  # tab
        if sresults and not ssearching:
            _add_url(sresults[ssel]["url"])  # add selected result to queue
        else:
            sugg = sbucket.get("suggest") or []
            if sugg:  # autofill the query from the top suggestion
                sq = sugg[0]
                sresults, ssel = [], 0
                _start_suggest(sq, sbucket)
    elif ch in (curses.KEY_BACKSPACE, 127, 8):
        sq = sq[:-1]
        _start_suggest(sq, sbucket)
    elif ch in (curses.KEY_DOWN,):
        if sresults:
            ssel = min(len(sresults) - 1, ssel + 1)
    elif ch in (curses.KEY_UP,):
        if sresults:
            ssel = max(0, ssel - 1)
    elif 32 <= ch < 127:
        # every printable char types — the search box owns the keyboard
        if len(sq) < _SEARCH_LIMIT:
            sq += chr(ch)
            sresults, ssel = [], 0  # query changed; old results are stale
            _start_suggest(sq, sbucket)
    return mode, sq, sresults, ssel, ssearching, smsg, sbucket


def _clean_query(q: str) -> str:
    """Normalize what the user typed into a search query.

    Tolerates the slash-command spellings people try: `/coldplay`,
    `/search coldplay`, `search coldplay` — all search for `coldplay`.
    """
    q = q.strip().lstrip("/").strip()
    if q.lower().startswith("search "):
        q = q[len("search "):].strip()
    return q


def _start_search(query: str, bucket: dict) -> None:
    def worker() -> None:
        try:
            resp = send_cmd("search", query)
            if resp.get("ok"):
                bucket["results"] = (resp.get("data") or {}).get("results", [])
                bucket["msg"] = "" if bucket["results"] else f"no results for {query!r}"
            else:
                bucket["msg"] = resp.get("error", "search failed")
        except Exception as e:  # daemon unreachable, etc.
            bucket["msg"] = str(e)
        bucket["done"] = True
    threading.Thread(target=worker, daemon=True).start()


def _start_suggest(query: str, bucket: dict) -> None:
    """Fetch history-based autocomplete suggestions for the search box."""
    if not query.strip():
        bucket["suggest"] = []
        return

    def worker() -> None:
        try:
            resp = send_cmd("suggest", query)
            bucket["suggest"] = ((resp.get("data") or {}).get("suggestions", [])
                                 if resp.get("ok") else [])
        except Exception:
            bucket["suggest"] = []
    threading.Thread(target=worker, daemon=True).start()


def _play_url(url: str) -> None:
    # Background so the UI stays responsive while the daemon resolves+loads.
    threading.Thread(target=lambda: send_cmd("play", url), daemon=True).start()


def _add_url(url: str) -> None:
    threading.Thread(target=lambda: send_cmd("add", url), daemon=True).start()


def _cycle_repeat(status: dict) -> None:
    cur = status.get("repeat", "off")
    nxt = _REPEAT_ORDER[(_REPEAT_ORDER.index(cur) + 1) % 3] if cur in _REPEAT_ORDER else "all"
    _bg_send("repeat", nxt)


# --- drawing ----------------------------------------------------------------

def _viz_height(h: int, mode: str) -> int:
    if mode == "search":
        return 0
    if mode == "theme":
        return 2
    if h <= 17:
        return 2
    elif h <= 23:
        return 3
    elif h <= 29:
        return 4
    else:
        return 5


def _draw(stdscr, status, h, w, mode, sq, sresults, ssel, ssearching, smsg,
          amp_t: float, ui: dict, cmdq: str = "",
          theme_sel: int = 0, theme_names: list | None = None,
          qfilter: str = "", sbucket: dict | None = None) -> None:
    _draw_header(stdscr, status, w, amp_t)
    # In DJ mode the deck takes over the full screen below the header (row 4..h-2)
    is_dj = bool(status.get("dj_mode"))
    viz_h = (h - 5) if is_dj else _viz_height(h, mode)
    if viz_h > 0:
        _draw_amp(stdscr, status, amp_t, w, ui, viz_top=4, viz_h=viz_h, total_h=h)
        sep_row = 4 + viz_h
        content_top = sep_row + 1
    else:
        sep_row = 4
        content_top = 4


    # Determine whether Dual-Pane Studio layout is active
    layout = ui.get("layout", "studio")
    is_split = (mode in ("now", "filter", "cmd") and w >= 80 and layout == "studio")
    left_w = int(w * 0.46) if is_split else w
    right_w = (w - left_w - 1) if is_split else 0

    # Section label separator (skip in DJ mode — deck owns full screen)
    if sep_row < h - 1 and mode != "search" and not is_dj:

        err = status.get("error")
        if err:
            try:
                stdscr.addstr(sep_row, 0, (f" ✖ {err}")[:w - 1], curses.color_pair(4) | curses.A_BOLD)
            except curses.error:
                pass
        elif is_split:
            cur_idx = status.get("current_index", -1)
            q_len = status.get("queue_len", 0)
            q_pos = f"{cur_idx + 1 if cur_idx >= 0 else 0}/{q_len}"
            toast = ui.get("viz_toast")
            toast_t = ui.get("viz_toast_t", 0.0)
            if toast and (time.monotonic() - toast_t) < 1.8:
                left_tag = f" ✦ {toast.upper()} ✦ "
            elif qfilter:
                left_tag = f" ♯ QUEUE ({q_pos}) · filter: {qfilter} "
            else:
                cur_m = ui.get("viz_mode") or _visualizer()
                left_tag = f" ♯ QUEUE ({q_pos}) · {cur_m.upper()} "

            left_fill = max(0, left_w - len(left_tag))
            left_bar = (left_tag + "─" * left_fill)[:left_w]

            lines = ui.get("lyr_lines", [])
            is_synced = any(ln.get("synced", True) and ln.get("start") is not None for ln in lines)
            off = ui.get("lyr_offset", 0.0)
            off_str = f" [{off:+.2f}s]" if abs(off) > 0.01 else ""
            if ui.get("lyr_loading"):
                right_tag = " [● LOADING LYRICS…] "
            elif is_synced:
                right_tag = f" [● LIVE SYNCED KARAOKE{off_str}] "
            elif lines:
                right_tag = " [● LYRICS] "
            else:
                right_tag = " [LYRICS] "

            right_fill = max(0, right_w - len(right_tag))
            right_bar = (right_tag + "─" * right_fill)[:right_w]

            full_sep = (left_bar + "┬" + right_bar)[:w - 1]
            try:
                stdscr.addstr(sep_row, 0, full_sep, curses.color_pair(1))
            except curses.error:
                pass
        else:
            toast = ui.get("viz_toast")
            toast_t = ui.get("viz_toast_t", 0.0)
            if toast and (time.monotonic() - toast_t) < 1.8:
                label = f"✦ {toast.upper()} ✦"
            elif qfilter:
                label = f"QUEUE  ·  filter: {qfilter}"
            elif layout == "lyrics" or ui.get("lyr_on"):
                lines = ui.get("lyr_lines", [])
                is_synced = any(ln.get("synced", True) and ln.get("start") is not None for ln in lines)
                off = ui.get("lyr_offset", 0.0)
                off_str = f" [{off:+.2f}s]" if abs(off) > 0.01 else ""
                label = f"LIVE SYNCED KARAOKE{off_str}" if is_synced else "LYRICS"
            else:
                cur_m = ui.get("viz_mode") or _visualizer()
                label = f"QUEUE  ·  {cur_m.upper()}"
            sep = f" {label} " + "─" * max(0, w - len(label) - 3)
            try:
                stdscr.addstr(sep_row, 0, sep[:w - 1], curses.color_pair(1))
            except curses.error:
                pass

    if mode == "search":
        _draw_search(stdscr, h, w, sq, sresults, ssel, ssearching, smsg, sbucket, top=content_top)
    elif mode == "theme":
        _draw_queue(stdscr, status, h, w, ui["qsel"], qfilter, top=content_top)
        _draw_theme_picker(stdscr, h, w, theme_sel, theme_names or list(_THEMES), top=content_top)
        try:
            stdscr.addstr(h - 1, 0, "↑/↓ browse (live) · enter apply · esc cancel"[:w - 1],
                          curses.color_pair(6) | curses.A_DIM)
        except curses.error:
            pass
    elif is_dj:
        # Full-screen DJ deck already drew itself via _draw_amp/_draw_dj_deck_tui.
        # Just stamp the keybind hint on the very last row.
        try:
            hint = " 🎧 [s/S] scratch · [e/E] filter · [b/B] bass drop · [f/F] fade next · [J] exit DJ "

            stdscr.addstr(h - 1, 0, hint[:w - 1],
                          curses.color_pair(1) | curses.A_BOLD)
        except curses.error:
            pass

    elif is_split:
        _draw_queue(stdscr, status, h, w, ui["qsel"], qfilter, top=content_top, left=0, max_w=left_w)
        for r in range(content_top, max(content_top + 1, h - 1)):
            try:
                stdscr.addstr(r, left_w, "│", curses.color_pair(1))
            except curses.error:
                pass
        _draw_lyrics(stdscr, status, ui, h, w, top=content_top, left=left_w + 1, max_w=right_w)
        if mode == "cmd":
            try:
                stdscr.addstr(h - 1, 0, (":" + cmdq + "_")[:w - 1],
                              curses.A_BOLD | curses.color_pair(3))
            except curses.error:
                pass
        elif mode == "filter":
            try:
                stdscr.addstr(h - 1, 0, "type to filter · enter keep · esc clear"[:w - 1],
                              curses.color_pair(6) | curses.A_DIM)
            except curses.error:
                pass
        else:
            try:
                stdscr.addstr(h - 1, 0, _NOW_HELP[:w - 1],
                              curses.color_pair(6) | curses.A_DIM)
            except curses.error:
                pass
    elif layout == "lyrics" or (ui.get("lyr_on") and layout != "studio"):
        _draw_lyrics(stdscr, status, ui, h, w, top=content_top)
        try:
            stdscr.addstr(h - 1, 0, ("space pause · tab layout · n/p next/prev · v viz · +/- vol · "
                                     "l/esc back · q quit")[:w - 1],
                          curses.color_pair(6) | curses.A_DIM)
        except curses.error:
            pass
    else:
        _draw_queue(stdscr, status, h, w, ui["qsel"], qfilter, top=content_top)
        if mode == "cmd":
            try:
                stdscr.addstr(h - 1, 0, (":" + cmdq + "_")[:w - 1],
                              curses.A_BOLD | curses.color_pair(3))
            except curses.error:
                pass
        elif mode == "filter":
            try:
                stdscr.addstr(h - 1, 0, "type to filter · enter keep · esc clear"[:w - 1],
                              curses.color_pair(6) | curses.A_DIM)
            except curses.error:
                pass
        else:
            try:
                stdscr.addstr(h - 1, 0, _NOW_HELP[:w - 1],
                              curses.color_pair(6) | curses.A_DIM)
            except curses.error:
                pass


def _draw_lyrics(stdscr, status: dict, ui: dict, h: int, w: int, top: int = 6,
                 left: int = 0, max_w: int | None = None) -> None:
    """Karaoke-style lyrics pane with zero-latency audio clock interpolation,
    vocal cadence modeling, intro countdown, and live sync offset."""
    window = max(0, (h - 3) - top)
    if window <= 0:
        return
    avail_w = max(1, min(max_w if max_w is not None else w, w - left - 1))
    if ui.get("lyr_loading"):
        try:
            stdscr.addstr(top, left, "  loading lyrics…"[:avail_w], curses.color_pair(6) | curses.A_DIM)
        except curses.error:
            pass
        return
    lines = ui.get("lyr_lines", [])
    if not lines:
        note = ui.get("lyr_note") or "no lyrics available"
        try:
            stdscr.addstr(top, left, ("  " + note)[:avail_w], curses.color_pair(4))
        except curses.error:
            pass
        return

    is_synced = any(ln.get("synced", True) and ln.get("start") is not None for ln in lines)
    pos = _get_live_position(status, ui)

    if is_synced:
        cur = _cur_lyr_line(status, ui, pos=pos)

        # ── INTRO STATE (playback before first vocal line) ──
        if cur == -1:
            first_s = lines[0].get("start", 0.0)
            rem = max(0.0, first_s - pos)
            pulse_idx = int((time.monotonic() * 2.5) % 3)
            dots = "● " * (pulse_idx + 1) + "○ " * (2 - pulse_idx)
            off = ui.get("lyr_offset", 0.0)
            off_tag = f"  [{off:+.2f}s]" if abs(off) > 0.01 else ""
            intro_msg = f"  ♫ [INTRO]  vocals in {int(round(rem))}s  {dots.strip()}{off_tag}"
            try:
                stdscr.addstr(top, left, intro_msg[:avail_w], curses.color_pair(5) | curses.A_BOLD)
            except curses.error:
                pass
            for i in range(0, min(len(lines), window - 1)):
                row = top + 1 + i
                text = ("  " + lines[i]["text"])[:avail_w]
                attr = curses.color_pair(6)
                if i > 2:
                    attr |= curses.A_DIM
                try:
                    stdscr.addstr(row, left, text, attr)
                except curses.error:
                    pass
            return

        # ── ACTIVE PLAYBACK & KARAOKE SWEEP ──
        start = max(0, cur - window // 2)
        for i in range(start, min(len(lines), start + window)):
            row = top + (i - start)
            text_raw = lines[i]["text"]
            pad = "  "
            text_disp = (pad + text_raw)[:avail_w]
            ln = lines[i]
            if i == cur:
                start_t = ln.get("start", 0.0)
                end_t = ln.get("end", start_t + 1.0)
                next_t = ln.get("next_start", end_t)
                dur = max(0.001, end_t - start_t)

                if pos < start_t:
                    n_raw = 0
                elif pos <= end_t:
                    frac = min(1.0, max(0.0, (pos - start_t) / dur))
                    n_raw = int(round(frac * len(text_raw)))
                else:
                    n_raw = len(text_raw)

                n_raw = max(0, min(len(text_raw), n_raw))
                n_disp = len(pad) + n_raw if n_raw > 0 else 0
                n_disp = min(len(text_disp), n_disp)
                try:
                    if n_disp > 0:
                        stdscr.addstr(row, left, text_disp[:n_disp], curses.color_pair(5) | curses.A_REVERSE)
                    if n_disp < len(text_disp):
                        stdscr.addstr(row, left + n_disp, text_disp[n_disp:], curses.color_pair(5) | curses.A_BOLD)

                    # Subtle beat pulse during instrumental pause between lines
                    if pos > end_t and (next_t - pos) >= 1.2:
                        pulse = int((time.monotonic() * 2.0) % 3)
                        p_str = " ·" * (pulse + 1)
                        col_p = left + len(text_disp) + 1
                        if col_p + len(p_str) < avail_w:
                            stdscr.addstr(row, col_p, p_str, curses.color_pair(5) | curses.A_DIM)
                except curses.error:
                    pass
            elif i < cur:
                # Past lines: Dimmed
                try:
                    stdscr.addstr(row, left, text_disp, curses.color_pair(6) | curses.A_DIM)
                except curses.error:
                    pass
            else:
                # Upcoming lines
                attr = curses.color_pair(6)
                if (i - cur) > 2:
                    attr |= curses.A_DIM
                try:
                    stdscr.addstr(row, left, text_disp, attr)
                except curses.error:
                    pass
    else:
        # Authentic plain unsynced lyrics — NO fake timestamps, NO fake sweeps
        scroll = max(0, min(max(0, len(lines) - window), ui.get("lyr_scroll", 0)))
        for i in range(scroll, min(len(lines), scroll + window)):
            row = top + (i - scroll)
            text = ("  " + lines[i]["text"])[:avail_w]
            try:
                stdscr.addstr(row, left, text, curses.color_pair(6))
            except curses.error:
                pass


def _draw_dj_deck_tui(stdscr, status: dict, amp_t: float, w: int,
                      viz_top: int, total_h: int) -> None:
    """Full-screen Pioneer CDJ/DDJ-style dual deck console drawn in DJ mode.

    Takes the entire terminal from viz_top → total_h-2.
    Layout:
      LED light strip
      Deck A title | Mixer header | Deck B title
      [Platter rings] | [EQ/VU] | [Platter rings]
      Waveform strip A | Crossfader | Waveform strip B
      Progress/time   | BPM sync  | Pitch/cue
      Bottom borders
      Performance pads row
      LED light strip
    """
    aw = max(1, w - 1)
    state      = status.get("state", "idle")
    is_playing = state == "playing"
    pos        = float(status.get("position", 0) or 0)
    dur        = float(status.get("duration", 1) or 1)
    speed      = float(status.get("speed", 1.0) or 1.0)
    vol        = int(status.get("volume", 80) or 80)

    cur_idx      = status.get("current_index", 0) or 0
    queue        = status.get("queue") or []
    deck_a_title = (status.get("title") or "NO TRACK").upper()
    deck_b_title = (queue[cur_idx + 1]["title"]
                    if cur_idx + 1 < len(queue) else "CUE NEXT").upper()

    bpm       = int((124 + ((cur_idx * 7 + int(pos)) % 16)) * speed)
    pitch_pct = (speed - 1.0) * 100.0
    t         = amp_t

    # ── helper: safe addstr ──────────────────────────────────────────────────
    C_H  = curses.color_pair(1) | curses.A_BOLD   # white bold
    C_G  = curses.color_pair(2) | curses.A_BOLD   # green bold  (deck A)
    C_C  = curses.color_pair(3) | curses.A_BOLD   # cyan bold   (mixer)
    C_Y  = curses.color_pair(5) | curses.A_BOLD   # yellow bold (deck B)
    C_R  = curses.color_pair(4) | curses.A_BOLD   # red bold    (leds / alert)
    C_D  = curses.color_pair(6) | curses.A_DIM    # dim

    def put(row: int, col: int, text: str, attr=None) -> None:
        if row < viz_top or row >= total_h - 1 or col >= aw:
            return
        text = text[:max(0, aw - col)]
        if not text:
            return
        try:
            stdscr.addstr(row, col, text, attr if attr is not None else C_C)
        except curses.error:
            pass

    def fill(row: int, ch: str = " ", attr=None) -> None:
        if row < viz_top or row >= total_h - 1:
            return
        try:
            stdscr.addstr(row, 0, (ch * aw)[:aw], attr if attr is not None else C_D)
        except curses.error:
            pass

    # ── animation helpers ────────────────────────────────────────────────────
    def vu_bar(phase: float, length: int = 8) -> str:
        v = (math.sin(t * 7.0 + phase) + 1.0) / 2.0 if is_playing else 0.08
        n = int(v * length)
        return "▓" * n + "░" * (length - n)

    def waveform(phase_off: float, length: int) -> str:
        chars = "▁▂▃▄▅▆▇█▇▆▅▄▃▂▁"
        if not is_playing:
            return "▂" * length
        result = []
        for i in range(length):
            v  = math.sin(t * 4.2 + i * 0.38 + phase_off) * 0.6
            v += math.sin(t * 9.1 + i * 0.7  + phase_off) * 0.4
            result.append(chars[int((v + 1.0) / 2.0 * (len(chars) - 1))])
        return "".join(result)

    def progress_bar(length: int) -> str:
        p = min(1.0, pos / max(1, dur))
        n = int(p * length)
        return "━" * max(0, n - 1) + ("◆" if n > 0 else "") + "─" * max(0, length - n)

    def fmt_t(s: float) -> str:
        s = int(s); return f"{s // 60:02d}:{s % 60:02d}"

    PLATTER_FRAMES = [
        ["  ╭─────╮  ", " ╱ ◜─◝  ╲ ", "│ │  ◈  │ │", " ╲ ◟─◞  ╱ ", "  ╰─────╯  "],
        ["  ╭─────╮  ", " ╱  ◠─◝ ╲ ", "│ │  ◈  │ │", " ╲  ◡─◞ ╱ ", "  ╰─────╯  "],
        ["  ╭─────╮  ", " ╱  ─◝◜ ╲ ", "│ │  ◈  │ │", " ╲  ─◞◟ ╱ ", "  ╰─────╯  "],
        ["  ╭─────╮  ", " ╱  ◝◜─ ╲ ", "│ │  ◈  │ │", " ╲  ◞◟─ ╱ ", "  ╰─────╯  "],
    ]
    PLATTER_H = 5
    PLATTER_W = 12

    spin_frame_a = int(t * 10) % len(PLATTER_FRAMES)
    spin_frame_b = int(t *  9.7) % len(PLATTER_FRAMES)

    # Layout columns
    avail   = total_h - viz_top - 1
    if avail < 6 or aw < 36:
        fill(viz_top, " ")
        put(viz_top, 0, f" DJ  A:{deck_a_title[:24]}  ⟺  B:{deck_b_title[:24]}", C_H)
        return

    deck_w  = max(14, (aw - 12) // 2)
    mix_w   = aw - 2 * deck_w
    ax      = 0
    mx      = deck_w
    bx      = deck_w + mix_w

    row = viz_top

    # ── clear zone ───────────────────────────────────────────────────────────
    for r in range(viz_top, total_h - 1):
        fill(r)

    # ── TOP LED STRIP ────────────────────────────────────────────────────────
    LED = "◆◇◈●○◉⊙◎"
    if is_playing:
        led = "".join(
            LED[int((math.sin(t * 6 + i * 2 * math.pi / aw) + 1) / 2 * (len(LED) - 1))]
            for i in range(aw)
        )
        put(row, 0, led[:aw], C_G)
    else:
        put(row, 0, ("·" * aw)[:aw], C_D)
    row += 1

    # ── DECK HEADER BOXES ────────────────────────────────────────────────────
    put(row, ax, ("╔" + "═" * (deck_w - 2) + "╗")[:deck_w], C_H)
    put(row, mx, ("╔" + "═" * (mix_w  - 2) + "╗")[:mix_w],  C_H)
    put(row, bx, ("╔" + "═" * (deck_w - 2) + "╗")[:deck_w], C_H)
    row += 1

    # deck A status line
    st_a = "▶ PLAYING" if is_playing else "■ PAUSED "
    st_b = "▶  CUED  "
    inner = deck_w - 4
    da_head = f"║ A {st_a:<{inner}} ║"
    db_head = f"║ B {st_b:<{inner}} ║"
    mx_head = f"║ {'MIXER':^{mix_w - 4}} ║"
    put(row, ax, da_head[:deck_w], C_G if is_playing else C_D)
    put(row, mx, mx_head[:mix_w],  C_C)
    put(row, bx, db_head[:deck_w], C_Y)
    row += 1

    # track name
    ta = deck_a_title[:inner]
    tb = deck_b_title[:inner]
    put(row, ax, f"║ ♫ {ta:<{inner}} ║"[:deck_w], C_G if is_playing else C_D)
    put(row, mx, f"║ {'BPM ' + str(bpm):^{mix_w - 4}} ║"[:mix_w], C_C)
    put(row, bx, f"║ ♫ {tb:<{inner}} ║"[:deck_w], C_Y)
    row += 1

    # ── PLATTERS ─────────────────────────────────────────────────────────────
    pa = PLATTER_FRAMES[spin_frame_a] if is_playing else PLATTER_FRAMES[0]
    pb = PLATTER_FRAMES[spin_frame_b] if is_playing else PLATTER_FRAMES[0]
    plat_off_a = max(0, (deck_w - PLATTER_W) // 2)
    plat_off_b = max(0, (deck_w - PLATTER_W) // 2)

    eq_labels = ["HI ", "MID", "LOW", "VOL", "   "]
    for pi in range(PLATTER_H):
        if row >= total_h - 2:
            break
        # borders
        put(row, ax, "║", C_H)
        put(row, ax + deck_w - 1, "║", C_H)
        put(row, bx, "║", C_H)
        put(row, bx + deck_w - 1, "║", C_H)
        # platter art
        put(row, ax + plat_off_a, pa[pi][:PLATTER_W], C_G if is_playing else C_D)
        put(row, bx + plat_off_b, pb[pi][:PLATTER_W], C_Y)
        # mixer column: EQ knobs + VU
        eq_lbl = eq_labels[pi] if pi < len(eq_labels) else "   "
        vu      = vu_bar(pi * 1.1, 6)
        if pi == 3:  # vol fader
            gain_n  = int((vol / 130.0) * 8)
            vu      = "▓" * gain_n + "░" * (8 - gain_n)
            vu      = vu[:8]
            mx_line = f"║ {eq_lbl}[{vu}]║"
        elif pi == 4:  # crossfader
            xf_n    = int((math.sin(t * 0.25) + 1) / 2 * (mix_w - 8))
            xf_bar  = "─" * xf_n + "◆" + "─" * max(0, mix_w - 8 - xf_n)
            mx_line = f"║[{xf_bar[:mix_w - 4]}]║"
        else:
            mx_line = f"║ {eq_lbl}[{vu_bar(pi * 1.1, mix_w - 8)[:mix_w - 8]}]║"
        put(row, mx, mx_line[:mix_w], C_C)
        row += 1

    # ── WAVEFORM ROW ─────────────────────────────────────────────────────────
    if row < total_h - 4:
        ww  = deck_w - 2
        wfa = waveform(0.0,   ww)
        wfb = waveform(math.pi, ww)
        put(row, ax, f"║{wfa[:ww]}║"[:deck_w], C_G)
        put(row, bx, f"║{wfb[:ww]}║"[:deck_w], C_Y)
        put(row, mx, f"║{'─' * (mix_w - 2)}║"[:mix_w], C_C)
        row += 1

    # ── PROGRESS + TIME ───────────────────────────────────────────────────────
    if row < total_h - 3:
        pb_w   = deck_w - 12
        prog   = progress_bar(max(1, pb_w))
        t_a    = fmt_t(pos)
        t_d    = fmt_t(dur)
        put(row, ax, f"║ {t_a} {prog[:pb_w]} {t_d} ║"[:deck_w],    C_G if is_playing else C_D)
        put(row, bx, f"║  PITCH: {pitch_pct:+.1f}%  CUE READY  ║"[:deck_w], C_Y)
        put(row, mx, f"║ {'⚡ SYNC':^{mix_w - 4}} ║"[:mix_w], C_C)
        row += 1

    # ── BOTTOM BORDERS ────────────────────────────────────────────────────────
    if row < total_h - 2:
        put(row, ax, ("╚" + "═" * (deck_w - 2) + "╝")[:deck_w], C_H)
        put(row, mx, ("╚" + "═" * (mix_w  - 2) + "╝")[:mix_w],  C_H)
        put(row, bx, ("╚" + "═" * (deck_w - 2) + "╝")[:deck_w], C_H)
        row += 1

    # ── PERFORMANCE PADS ──────────────────────────────────────────────────────
    if row < total_h - 1:
        pads    = ["CUE1", "CUE2", "CUE3", "CUE4", "LOOP", "SMPL", "FX-1", "FX-2"]
        beat    = is_playing and (int(t * bpm / 60) % 2 == 0)
        pad_str = " ".join(
            f"[{'◆' if beat and i % 3 == 0 else '·'}{p}]"
            for i, p in enumerate(pads)
        )
        put(row, 0, f" PADS: {pad_str}"[:aw], C_Y if beat else C_D)
        row += 1

    # ── BOTTOM LED STRIP ──────────────────────────────────────────────────────
    if row < total_h - 1:
        if is_playing:
            bot_led = "".join(
                LED[int((math.sin(t * 8 + i * 2 * math.pi / aw + math.pi) + 1) / 2 * (len(LED) - 1))]
                for i in range(aw)
            )
            put(row, 0, bot_led[:aw], C_R)
        else:
            put(row, 0, ("·" * aw)[:aw], C_D)





def _draw_amp(stdscr, status: dict, amp_t: float, w: int, ui: dict,
              viz_top: int = 4, viz_h: int = 4, total_h: int = 0) -> None:
    """Multi-row animated Hi-Fi equalizer / oscilloscope visualizer.

    Modes:
      - 'spectrum': Multi-band graphic equalizer with floating peak hold dots and color tiers
      - 'stereo':   Dual-channel Left/Right mirrored mastering spectrum analyzer
      - 'wave':     High-resolution Braille sub-pixel vector oscilloscope
      - 'bars':     Continuous dense liquid-mercury sound wave
    """
    if viz_h <= 0 or w < 4:
        return

    if status.get("dj_mode"):
        _draw_dj_deck_tui(stdscr, status, amp_t, w, viz_top, total_h or viz_top + viz_h)
        return

    mode = ui.get("viz_mode") or _visualizer()
    if mode not in _VIZ_MODES:
        mode = "spectrum"

    state = status.get("state", "idle")
    vol = status.get("volume", 80) if status.get("volume") is not None else 80
    amp = min(1.0, 0.35 + (vol / 160.0))
    aw = max(1, w - 1)

    # ── State 1: IDLE (ambient breathing wave) ──────────────────────────
    if state == "idle":
        for r in range(viz_h):
            row_idx = viz_top + (viz_h - 1 - r)
            if r == 0:
                chars = []
                for x in range(aw):
                    v = (math.sin(amp_t * 0.9 + x * 0.12) + 1.0) * 0.5
                    idx = int(v * 3.0)
                    chars.append(_AMP_BLOCKS[idx])
                try:
                    stdscr.addstr(row_idx, 0, "".join(chars)[:aw], curses.color_pair(6) | curses.A_DIM)
                except curses.error:
                    pass
            else:
                try:
                    stdscr.addstr(row_idx, 0, " " * aw, curses.color_pair(6))
                except curses.error:
                    pass
        return

    # ── State 2: LOADING (sleek scanning sonar pulse) ───────────────────
    if state == "loading":
        sweep = (math.sin(amp_t * 3.2) + 1.0) * 0.5 * (aw - 1)
        for r in range(viz_h):
            row_idx = viz_top + (viz_h - 1 - r)
            chars = []
            for x in range(aw):
                dist = abs(x - sweep)
                if dist < 3.0:
                    h_val = max(0, min(7, int((3.0 - dist) / 3.0 * 7)))
                    chars.append(_AMP_BLOCKS[h_val])
                elif dist < 9.0:
                    chars.append("·")
                else:
                    chars.append(" ")
            color = curses.color_pair(3) | (curses.A_BOLD if r == 0 else curses.A_DIM)
            try:
                stdscr.addstr(row_idx, 0, "".join(chars)[:aw], color)
            except curses.error:
                pass
        return

    # ── State 3: PLAYING or PAUSED ───────────────────────────────────────
    is_paused = (state == "paused")

    # ── MODE: WAVE (Braille sub-pixel vector oscilloscope) ───────────────
    if mode == "wave":
        px_h = viz_h * 4
        px_w = aw * 2
        canvas = [[0 for _ in range(aw)] for _ in range(viz_h)]
        DOTS = [
            [0x01, 0x08],
            [0x02, 0x10],
            [0x04, 0x20],
            [0x40, 0x80],
        ]

        beat = (math.sin(amp_t * 2.05 * math.pi) + 1.0) * 0.5
        for x in range(px_w):
            y1 = (math.sin(amp_t * 3.8 + x * 0.07) * 0.65 +
                  math.cos(amp_t * 7.0 + x * 0.14) * 0.25 * beat)
            y2 = (math.sin(amp_t * -3.1 + x * 0.09 + 1.2) * 0.5 +
                  math.sin(amp_t * 10.5 + x * 0.21) * 0.18)

            for y_val in (y1, y2):
                py = int(round((y_val * 0.42 * amp + 0.5) * (px_h - 1)))
                py = max(0, min(px_h - 1, py))
                inv_y = (px_h - 1) - py
                cx = x // 2
                cy = inv_y // 4
                sub_x = x % 2
                sub_y = inv_y % 4
                if 0 <= cy < viz_h and 0 <= cx < aw:
                    canvas[cy][cx] |= DOTS[sub_y][sub_x]

        for r in range(viz_h):
            row_idx = viz_top + r
            line = "".join(chr(0x2800 + canvas[r][c]) for c in range(aw))
            if is_paused:
                attr = curses.color_pair(6) | curses.A_DIM
            elif r == 0:
                attr = curses.color_pair(5) | curses.A_BOLD
            elif r == viz_h - 1:
                attr = curses.color_pair(1) | curses.A_BOLD
            else:
                attr = curses.color_pair(3)
            try:
                stdscr.addstr(row_idx, 0, line[:aw], attr)
            except curses.error:
                pass

        if is_paused:
            p_badge = " [ PAUSED · SHEATHED ] "
            p_col = max(0, (aw - len(p_badge)) // 2)
            try:
                stdscr.addstr(viz_top + viz_h // 2, p_col, p_badge,
                              curses.color_pair(2) | curses.A_REVERSE | curses.A_BOLD)
            except curses.error:
                pass
        return

    # ── MODE: STEREO (Dual Channel L/R Mirrored Mastering Display) ───────
    if mode == "stereo":
        center_x = (aw - 1) // 2
        slot = 3
        bar_w = 2
        gap = 1
        side_bands = max(3, min(32, (center_x - 3) // slot))
        max_sub = viz_h * 8

        beat_kick = max(0.0, math.cos(amp_t * 2.05 * math.pi)) ** 3.5

        left_h = []
        right_h = []
        for i in range(side_bands):
            frac = i / max(1, side_bands - 1)
            b_l = (math.sin(amp_t * 4.2 + i * 0.42) * 0.4 + 0.6) * (1.0 - frac * 0.28 + 0.42 * beat_kick)
            b_r = (math.sin(amp_t * 4.2 + i * 0.42 + 0.5) * 0.4 + 0.6) * (1.0 - frac * 0.28 + 0.42 * beat_kick)
            left_h.append(min(1.0, max(0.05, b_l * amp)) * max_sub)
            right_h.append(min(1.0, max(0.05, b_r * amp)) * max_sub)

        left_h = left_h[::-1]

        for r in range(viz_h - 1, -1, -1):
            row_idx = viz_top + (viz_h - 1 - r)
            left_cells = []
            for h_val in left_h:
                if h_val >= (r + 1) * 8:
                    c = "█" * bar_w
                elif h_val <= r * 8:
                    c = " " * bar_w
                else:
                    c = _AMP_BLOCKS[min(7, max(0, int(h_val - r * 8) - 1))] * bar_w
                left_cells.append(c)
            l_str = (" " * gap).join(left_cells)

            right_cells = []
            for h_val in right_h:
                if h_val >= (r + 1) * 8:
                    c = "█" * bar_w
                elif h_val <= r * 8:
                    c = " " * bar_w
                else:
                    c = _AMP_BLOCKS[min(7, max(0, int(h_val - r * 8) - 1))] * bar_w
                right_cells.append(c)
            r_str = (" " * gap).join(right_cells)

            pad_l = max(0, center_x - len(l_str) - 2)
            div = " ▌▐ " if r == 0 else (" ║ " if r == 1 else " │ ")
            full_line = (" " * pad_l) + l_str + div + r_str

            if is_paused:
                attr = curses.color_pair(6) | curses.A_DIM
            elif r == viz_h - 1 and viz_h > 2:
                attr = curses.color_pair(4) | curses.A_BOLD
            elif r >= viz_h // 2:
                attr = curses.color_pair(2) | curses.A_BOLD
            else:
                attr = curses.color_pair(3)
            try:
                stdscr.addstr(row_idx, 0, full_line[:aw], attr)
            except curses.error:
                pass

        if is_paused:
            p_badge = " [ PAUSED · SHEATHED ] "
            p_col = max(0, (aw - len(p_badge)) // 2)
            try:
                stdscr.addstr(viz_top + viz_h // 2, p_col, p_badge,
                              curses.color_pair(2) | curses.A_REVERSE | curses.A_BOLD)
            except curses.error:
                pass
        return

    # ── MODE: BARS (Continuous dense fluid sound wave) ───────────────────
    if mode == "bars":
        max_sub = viz_h * 8
        heights = []
        beat = max(0.0, math.cos(amp_t * 2.05 * math.pi)) ** 3.0
        for x in range(aw):
            v = (math.sin(amp_t * 3.3 + x * 0.14) * 0.32 +
                 math.cos(amp_t * 5.1 - x * 0.08) * 0.26 +
                 math.sin(amp_t * 1.5 + x * 0.04) * 0.22 + 0.5)
            v = min(1.0, max(0.05, (v * 0.74 + beat * 0.26) * amp))
            heights.append(int(round(v * max_sub)))

        for r in range(viz_h - 1, -1, -1):
            row_idx = viz_top + (viz_h - 1 - r)
            row_chars = []
            for h_val in heights:
                if h_val >= (r + 1) * 8:
                    cell = "█"
                elif h_val <= r * 8:
                    cell = " "
                else:
                    cell = _AMP_BLOCKS[min(7, max(0, int(h_val - r * 8) - 1))]
                row_chars.append(cell)
            line = "".join(row_chars)[:aw]

            if is_paused:
                attr = curses.color_pair(6) | curses.A_DIM
            elif r == viz_h - 1 and viz_h > 2:
                attr = curses.color_pair(4) | curses.A_BOLD
            elif r >= viz_h // 2:
                attr = curses.color_pair(2) | curses.A_BOLD
            else:
                attr = curses.color_pair(3)
            try:
                stdscr.addstr(row_idx, 0, line, attr)
            except curses.error:
                pass

        if is_paused:
            p_badge = " [ PAUSED · SHEATHED ] "
            p_col = max(0, (aw - len(p_badge)) // 2)
            try:
                stdscr.addstr(viz_top + viz_h // 2, p_col, p_badge,
                              curses.color_pair(2) | curses.A_REVERSE | curses.A_BOLD)
            except curses.error:
                pass
        return

    # ── MODE: MATRIX (Cyberpunk Falling Matrix Rain) ────────────────────
    if mode == "matrix":
        CHAR_POOL = "0123456789ABCDEFｦｱｳｴｵｶｷｹｺｻｼｽｾｿﾀﾂﾃﾅﾆﾇﾈﾊﾋﾎﾏﾐﾑﾒﾓﾔﾕﾗﾘﾜ"
        for r in range(viz_h):
            row_idx = viz_top + r
            for c in range(aw):
                seed = c * 13 + r * 7
                drop_pos = int((amp_t * (10 + (c % 7) * 4) + seed) % (viz_h + 10)) - 5
                dist = r - drop_pos
                if dist == 0:
                    ch = CHAR_POOL[int((amp_t * 15 + seed) % len(CHAR_POOL))]
                    attr = (curses.color_pair(5) | curses.A_BOLD) if not is_paused else (curses.color_pair(6) | curses.A_DIM)
                elif 0 < dist < 4:
                    ch = CHAR_POOL[int((amp_t * 8 + seed + dist) % len(CHAR_POOL))]
                    attr = (curses.color_pair(2) | curses.A_BOLD) if not is_paused else (curses.color_pair(6) | curses.A_DIM)
                elif 4 <= dist < 8:
                    ch = CHAR_POOL[int((seed + dist) % len(CHAR_POOL))]
                    attr = curses.color_pair(1) | curses.A_DIM
                else:
                    ch = " "
                    attr = curses.color_pair(6)
                try:
                    stdscr.addch(row_idx, c, ch, attr)
                except curses.error:
                    pass
        if is_paused:
            p_badge = " [ MATRIX PAUSED ] "
            p_col = max(0, (aw - len(p_badge)) // 2)
            try:
                stdscr.addstr(viz_top + viz_h // 2, p_col, p_badge,
                              curses.color_pair(2) | curses.A_REVERSE | curses.A_BOLD)
            except curses.error:
                pass
        return

    # ── MODE: VU (Dual Stereo Analog dB Level Meters) ───────────────────
    if mode == "vu":
        beat_l = (math.sin(amp_t * 3.6) * 0.35 + math.cos(amp_t * 7.2) * 0.2 + 0.45) * amp
        beat_r = (math.sin(amp_t * 3.6 + 0.4) * 0.35 + math.sin(amp_t * 6.8) * 0.2 + 0.45) * amp
        if is_paused:
            beat_l = beat_r = 0.0

        gauge_w = max(10, aw - 34)
        db_l = -40.0 + (beat_l * 43.0)
        db_r = -40.0 + (beat_r * 43.0)

        l_fill = int(max(0.0, min(1.0, (db_l + 40.0) / 43.0)) * gauge_w)
        r_fill = int(max(0.0, min(1.0, (db_r + 40.0) / 43.0)) * gauge_w)

        l_bar = "█" * l_fill + "░" * (gauge_w - l_fill)
        r_bar = "█" * r_fill + "░" * (gauge_w - r_fill)

        line_l = f"  LEFT   [{l_bar}] {db_l:+5.1f} dB  ▲ {db_l + 1.2:+4.1f} dB"
        line_r = f"  RIGHT  [{r_bar}] {db_r:+5.1f} dB  ▲ {db_r + 1.5:+4.1f} dB"
        ticks  = "          -40     -20    -10    -6     -3     0     +3 dB"

        rows = [ticks, line_l, line_r]
        for idx, text in enumerate(rows):
            if idx < viz_h:
                row_idx = viz_top + idx
                attr = curses.color_pair(2) | curses.A_BOLD if idx > 0 else curses.color_pair(3)
                if is_paused:
                    attr = curses.color_pair(6) | curses.A_DIM
                try:
                    stdscr.addstr(row_idx, 0, text[:aw], attr)
                except curses.error:
                    pass
        return

    # ── MODE: OSCILLOSCOPE (Lissajous Vector Phase-Space Curve) ─────────
    if mode == "oscilloscope":
        px_h = viz_h * 4
        px_w = aw * 2
        canvas = [[0 for _ in range(aw)] for _ in range(viz_h)]
        DOTS = [
            [0x01, 0x08],
            [0x02, 0x10],
            [0x04, 0x20],
            [0x40, 0x80],
        ]
        phase = amp_t * 3.5
        points = 240
        for i in range(points):
            t = (i / points) * 2 * math.pi
            x_val = math.sin(2 * t + phase) * 0.85
            y_val = math.sin(3 * t + phase * 1.3) * 0.85
            if not is_paused:
                x_val += math.cos(5 * t + phase * 2.1) * 0.12 * amp
                y_val += math.sin(4 * t + phase * 1.8) * 0.12 * amp

            py = int(round((y_val * 0.42 * amp + 0.5) * (px_h - 1)))
            py = max(0, min(px_h - 1, py))
            px = int(round((x_val * 0.42 * amp + 0.5) * (px_w - 1)))
            px = max(0, min(px_w - 1, px))

            inv_y = (px_h - 1) - py
            cx = px // 2
            cy = inv_y // 4
            sub_x = px % 2
            sub_y = inv_y % 4
            if 0 <= cy < viz_h and 0 <= cx < aw:
                canvas[cy][cx] |= DOTS[sub_y][sub_x]

        for r in range(viz_h):
            row_idx = viz_top + r
            line = "".join(chr(0x2800 + canvas[r][c]) for c in range(aw))
            attr = (curses.color_pair(3) | curses.A_BOLD) if not is_paused else (curses.color_pair(6) | curses.A_DIM)
            try:
                stdscr.addstr(row_idx, 0, line[:aw], attr)
            except curses.error:
                pass
        return

    # ── MODE: SPECTRUM (Default - Multi-band Graphic Equalizer + Peaks) ───
    slot = 3
    bar_w = 2
    gap = 1
    num_bands = max(4, min(64, (aw - 2) // slot))
    pad = max(0, (aw - (num_bands * slot - gap)) // 2)
    max_sub = viz_h * 8

    bands_state = ui.get("viz_bands")
    if not isinstance(bands_state, list) or len(bands_state) != num_bands:
        bands_state = [0.0] * num_bands
        ui["viz_bands"] = bands_state

    peaks_state = ui.get("viz_peaks")
    if not isinstance(peaks_state, list) or len(peaks_state) != num_bands:
        peaks_state = [0.0] * num_bands
        ui["viz_peaks"] = peaks_state

    holds_state = ui.get("viz_holds")
    if not isinstance(holds_state, list) or len(holds_state) != num_bands:
        holds_state = [0] * num_bands
        ui["viz_holds"] = holds_state

    beat_kick = max(0.0, math.cos(amp_t * 2.05 * math.pi)) ** 3.5
    beat_snare = max(0.0, math.sin(amp_t * 2.05 * math.pi + 1.5)) ** 4.0

    for i in range(num_bands):
        frac = i / max(1, num_bands - 1)
        if frac < 0.25:
            b = (math.sin(amp_t * 3.8 + i * 0.4) * 0.3 + 0.7) * (0.4 + 0.6 * beat_kick)
        elif frac < 0.65:
            b = (math.sin(amp_t * 5.4 + i * 0.6) * 0.35 +
                 math.cos(amp_t * 2.8 - i * 0.3) * 0.35 + 0.3) * (0.5 + 0.5 * beat_snare)
        else:
            flutter = math.sin(amp_t * 15.2 + i * 1.2) * 0.4 + 0.6
            b = flutter * (0.3 + 0.4 * beat_kick + 0.3 * beat_snare)

        target = min(1.0, max(0.05, b * amp)) * max_sub

        if is_paused:
            curr = bands_state[i]
        else:
            prev = bands_state[i]
            if target > prev:
                curr = prev + (target - prev) * 0.72
            else:
                curr = max(0.0, prev - max(0.65, prev * 0.15))
            bands_state[i] = curr

            pk = peaks_state[i]
            hld = holds_state[i]
            if curr >= pk:
                peaks_state[i] = curr
                holds_state[i] = 5
            else:
                if hld > 0:
                    holds_state[i] = hld - 1
                else:
                    peaks_state[i] = max(0.0, pk - 0.55)

    for r in range(viz_h - 1, -1, -1):
        row_idx = viz_top + (viz_h - 1 - r)
        if is_paused:
            attr_bar = curses.color_pair(6) | curses.A_DIM
            attr_peak = curses.color_pair(6) | curses.A_DIM
        elif r == viz_h - 1 and viz_h > 2:
            attr_bar = curses.color_pair(4) | curses.A_BOLD
            attr_peak = curses.color_pair(5) | curses.A_BOLD
        elif r >= viz_h // 2:
            attr_bar = curses.color_pair(2) | curses.A_BOLD
            attr_peak = curses.color_pair(5) | curses.A_BOLD
        else:
            attr_bar = curses.color_pair(3)
            attr_peak = curses.color_pair(5) | curses.A_BOLD

        row_str = [" " * pad]
        peak_positions = []
        col_offset = pad

        for i in range(num_bands):
            h_val = bands_state[i]
            p_val = peaks_state[i]
            p_row = int(p_val // 8)

            if h_val >= (r + 1) * 8:
                cell = "█" * bar_w
            elif h_val <= r * 8:
                if p_row == r and p_val > 1.5:
                    cell = "▔" * bar_w
                    peak_positions.append(col_offset)
                else:
                    cell = " " * bar_w
            else:
                rem = int(h_val - r * 8)
                cell = _AMP_BLOCKS[min(7, max(0, rem - 1))] * bar_w

            row_str.append(cell)
            col_offset += bar_w
            if i < num_bands - 1:
                row_str.append(" " * gap)
                col_offset += gap

        full_row = "".join(row_str)[:aw]
        try:
            stdscr.addstr(row_idx, 0, full_row, attr_bar)
            if not is_paused:
                for px in peak_positions:
                    if px + bar_w <= aw:
                        stdscr.addstr(row_idx, px, "▔" * bar_w, attr_peak)
        except curses.error:
            pass

    if is_paused:
        p_badge = " [ PAUSED · SHEATHED ] "
        p_col = max(0, (aw - len(p_badge)) // 2)
        try:
            stdscr.addstr(viz_top + viz_h // 2, p_col, p_badge,
                          curses.color_pair(2) | curses.A_REVERSE | curses.A_BOLD)
        except curses.error:
            pass



def _draw_header(stdscr, status: dict, w: int, amp_t: float = 0.0) -> None:
    """5-row header: brand bar · title+time · progress bar · meta · visualizer."""
    W = w - 1   # safe write width
    state = status.get("state", "idle")
    mark  = _STATE_MARK.get(state, "·")
    label = _STATE_KAOMOJI.get(state, state.upper())

    # ── Row 0: brand bar ─────────────────────────────────────────────────
    # Left: logo + state.  Right: state badge right-aligned.
    left0  = f" ♪  Skye Player"
    right0 = f" 🎧⚡ DJ MODE · {mark} {label} " if status.get("dj_mode") else f" {mark} {label} "
    pad0   = max(0, W - len(left0) - len(right0))
    try:
        stdscr.addstr(0, 0, (left0 + " " * pad0 + right0)[:W],
                      curses.color_pair(1) | curses.A_BOLD)
    except curses.error:
        pass

    # ── Row 1: title (bold) + time right-aligned ─────────────────────────
    title    = status.get("title") or "— nothing playing —"
    pos_s    = _fmt_time(status.get("position"))
    dur      = status.get("duration")
    dur_s    = _fmt_time(dur)
    time_str = f"  {pos_s} / {dur_s}  "
    title_w  = max(0, W - len(time_str))
    try:
        stdscr.addstr(1, 0, (" " + title)[:title_w], curses.color_pair(5) | curses.A_BOLD)
    except curses.error:
        pass
    try:
        stdscr.addstr(1, max(0, W - len(time_str)), time_str, curses.color_pair(3))
    except curses.error:
        pass

    # ── Row 2: progress bar ───────────────────────────────────────────────
    frac   = (status.get("position") or 0) / dur if dur else 0.0
    frac   = min(1.0, max(0.0, frac))
    bw     = max(0, W - 2)
    filled = int(bw * frac)
    bar    = "━" * filled + "─" * (bw - filled)
    # tick mark at playhead
    if 0 < filled < bw:
        bar = bar[:filled - 1] + "●" + bar[filled:]
    try:
        stdscr.addstr(2, 0, "▕" + bar[:bw] + "▏", curses.color_pair(3))
    except curses.error:
        pass

    # ── Row 3: meta info ──────────────────────────────────────────────────
    idx      = status.get("current_index", -1)
    vol      = status.get("volume") or 0
    n_blocks = max(0, min(10, round(vol / 10)))
    vol_bar  = "▰" * n_blocks + "▱" * (10 - n_blocks)
    rpt      = status.get("repeat", "off")
    rpt_icon = {"off": "↩", "all": "↻", "one": "⟳"}.get(rpt, rpt)
    shuf     = "⇄" if status.get("shuffle") else "→"
    q_pos    = f"{idx + 1 if idx >= 0 else 0}/{status.get('queue_len', 0)}"
    meta     = f"  {vol_bar} {vol}%  {rpt_icon} repeat  {shuf} shuffle  ♯ {q_pos}"
    sp = status.get("speed")
    if sp and sp != 1:
        meta += f"  ×{sp}"
    if status.get("fav"):
        meta += "  ♥"
    rem = status.get("sleep_remaining")
    if rem:
        m, _ = divmod(int(rem), 60)
        meta += f"  ⏰ {m}m"
    mood = status.get("mood")
    if mood:
        meta += f"  🎧 {mood}"
    if status.get("smart_queue"):
        meta += "  ⚡ smart"
    if status.get("dj_mode"):
        meta += "  🎧⚡ DJ MODE"
    err = status.get("error")
    if err:
        meta += f"  ✖ {err}"
    try:
        stdscr.addstr(3, 0, meta[:W], curses.color_pair(4) if err else curses.color_pair(3))
    except curses.error:
        pass


def _draw_queue(stdscr, status: dict, h: int, w: int, qsel: int, qfilter: str = "",
                top: int = 6, left: int = 0, max_w: int | None = None) -> None:
    """Render the queue starting at row top, two rows per track (title + channel)."""
    vis = _visible_queue(status, qfilter)
    cur = status.get("current_index", -1)
    bottom = max(top + 1, h - 3)
    page = bottom - top
    if page <= 0:
        return
    avail_w = max(1, min(max_w if max_w is not None else w, w - left - 1))
    if not vis:
        empty_msg = ("  (queue is empty  ·  / to search)" if not qfilter else f"  (no tracks matching '{qfilter}')")
        try:
            stdscr.addstr(top, left, empty_msg[:avail_w], curses.color_pair(6) | curses.A_DIM)
        except curses.error:
            pass
        return

    items_page = max(1, page // 2)
    focus = qsel if 0 <= qsel < len(vis) else 0
    start = max(0, min(focus - items_page // 2, max(0, len(vis) - items_page)))

    for k in range(start, min(len(vis), start + items_page)):
        real_idx, t = vis[k]
        is_cur = real_idx == cur
        is_sel = k == qsel
        row    = top + (k - start) * 2

        # Track row
        num     = f"{real_idx + 1:>3}"
        icon    = "▶" if is_cur else ("›" if is_sel else " ")
        dur     = _fmt_time(t.get("duration"))
        dur_str = f"  {dur}" if avail_w > 26 else ""
        prefix  = f"{icon}{num}  "
        title_avail = max(1, avail_w - len(prefix) - len(dur_str))
        title_text  = (t.get("title") or "")[:title_avail]
        line1   = f"{prefix}{title_text:<{title_avail}}{dur_str}"

        if is_sel and is_cur:
            attr1 = curses.color_pair(2) | curses.A_REVERSE
        elif is_sel:
            attr1 = curses.color_pair(6) | curses.A_REVERSE
        elif is_cur:
            attr1 = curses.color_pair(2) | curses.A_BOLD
        else:
            attr1 = curses.color_pair(6)
        try:
            stdscr.addstr(row, left, line1[:avail_w], attr1)
        except curses.error:
            pass

        # Channel / artist row
        if row + 1 < bottom:
            ch = (t.get("channel") or "").strip()
            if ch:
                ch_line = f"     ↳ {ch}"
                ch_attr = (curses.color_pair(2) | curses.A_DIM) if is_cur else (curses.color_pair(6) | curses.A_DIM)
                try:
                    stdscr.addstr(row + 1, left, ch_line[:avail_w], ch_attr)
                except curses.error:
                    pass


def _draw_theme_picker(stdscr, h: int, w: int, sel: int, names: list[str], top: int = 4) -> None:
    try:
        stdscr.addstr(top, 0, " Theme  ·  ↑/↓ browse (live) · enter apply · esc cancel"[: w - 1],
                      curses.color_pair(1) | curses.A_BOLD)
    except curses.error:
        pass
    # scroll so the selected row stays visible on short terminals
    avail = max(1, (h - 3) - top - 1)
    start = max(0, min(sel - avail // 2, max(0, len(names) - avail)))
    for i in range(start, min(len(names), start + avail)):
        row = top + 2 + (i - start)
        name = names[i]
        col = 0
        if curses.has_colors():  # swatch in the theme's own accent color
            accent = _THEMES.get(name, _THEMES["default"])[2]
            curses.init_pair(7, accent, -1)  # one reusable pair, any # of themes
            try:
                stdscr.addstr(row, 0, "▮▮▮ ", curses.color_pair(7))
                col = 4
            except curses.error:
                pass
        # selected row uses pair 1, which is the PREVIEWED theme's header color
        attr = (curses.color_pair(1) | curses.A_BOLD) if i == sel else curses.color_pair(6)
        try:
            stdscr.addstr(row, col, (("▸" if i == sel else " ") + name)[: max(0, w - 1 - col)],
                          attr)
        except curses.error:
            pass


def _draw_search(stdscr, h, w, sq, sresults, ssel, ssearching, smsg,
                 sbucket: dict | None = None, top: int = 4) -> None:
    # input line
    line = "Search: " + sq + "_"
    try:
        stdscr.addstr(top, 0, line[: w - 1], curses.A_BOLD | curses.color_pair(3))
    except curses.error:
        pass

    row = top + 2
    if ssearching:
        try:
            stdscr.addstr(row, 0, f"searching {sq!r}…"[: w - 1], curses.color_pair(6))
        except curses.error:
            pass
    elif smsg:
        try:
            stdscr.addstr(row, 0, smsg[: w - 1], curses.color_pair(4))
        except curses.error:
            pass
    elif sresults:
        for i, r in enumerate(sresults):
            if row >= h - 2:
                break
            dur = _fmt_time(r.get("duration"))
            channel = r.get("channel") or ""
            mark = "▸" if i == ssel else " "
            title_w = max(1, w - 36)
            body = f"{mark} {i + 1}.  {r['title'][:title_w]:<{title_w}}  [{dur:>5}]  {channel[:16]}"
            attr = curses.color_pair(6) | (curses.A_REVERSE if i == ssel else 0)
            try:
                stdscr.addstr(row, 0, body[: w - 1], attr)
            except curses.error:
                pass
            row += 1
    else:
        sugg = (sbucket or {}).get("suggest") or []
        if sugg:
            for i, s in enumerate(sugg[:5]):
                if row >= h - 2:
                    break
                mark = "→" if i == 0 else " "
                try:
                    stdscr.addstr(row, 0, (f"{mark} {s}")[: w - 1],
                                  curses.color_pair(6) | (curses.A_REVERSE if i == 0 else 0))
                except curses.error:
                    pass
                row += 1
            try:
                stdscr.addstr(row, 0, "tab autofill · enter search"[: w - 1],
                              curses.color_pair(6) | curses.A_DIM)
            except curses.error:
                pass
        else:
            try:
                stdscr.addstr(row, 0, "type a song (e.g. /search coldplay) and press enter"[: w - 1],
                              curses.color_pair(6) | curses.A_DIM)
            except curses.error:
                pass

    try:
        stdscr.addstr(h - 1, 0, _SEARCH_HELP[: w - 1], curses.color_pair(6) | curses.A_DIM)
    except curses.error:
        pass
