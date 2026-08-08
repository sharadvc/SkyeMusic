"""Full-screen TUI for tune (curses).

A thin client: polls `status` every ~0.25s and sends key commands. Two modes:

- **NOW** — now-playing header, progress bar, queue, transport keys.
- **SEARCH** — press `/` to search YouTube from inside the UI; pick a result
  to play or add to the queue.

Quitting the TUI (q / Esc in NOW mode) leaves the daemon and mpv playing.
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

_STATE_MARK = {"playing": "▶", "paused": "⏸", "loading": "…", "idle": "·"}
_REPEAT_ORDER = ["off", "all", "one"]
_SEARCH_LIMIT = 80
_AMP_BLOCKS = "▁▂▃▄▅▆▇█"
_THEMES = {
    "default": (curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED),
    "ocean": (curses.COLOR_BLUE, curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_RED),
    "sunset": (curses.COLOR_MAGENTA, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_RED),
    "mono": (curses.COLOR_WHITE, curses.COLOR_WHITE, curses.COLOR_WHITE, curses.COLOR_RED),
}


def _load_theme() -> str:
    try:
        import json
        from pathlib import Path
        d = json.loads((Path.home() / ".config/tune/config.json").read_text())
        theme = d.get("theme", "default")
        return theme if theme in _THEMES else "default"
    except Exception:
        return "default"
_NOW_HELP = ("space pause · n/p next/prev · ↑/↓ select · d remove · enter jump · "
             "+/- vol · [ ] speed · ←/→ seek · l lyrics · s shuffle · r repeat · "
             "/ search · q quit")
_SEARCH_HELP = "enter play · tab add to queue · ↑/↓ move · backspace edit · esc back"


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
        c1, c2, c3, c4 = _THEMES.get(_load_theme(), _THEMES["default"])
        curses.init_pair(1, c1, -1)    # header
        curses.init_pair(2, c2, -1)    # current track
        curses.init_pair(3, c3, -1)    # meta / bar
        curses.init_pair(4, c4, -1)    # error

    status: dict = {"state": "idle"}

    # Background status poller: the UI never blocks on the daemon, even if it
    # is slow or down. The render loop only reads `status`.
    def _poller() -> None:
        nonlocal status
        while True:
            try:
                resp = send_cmd("status")
                status = resp["data"] if resp.get("ok") else {
                    "state": "idle", "error": resp.get("error")}
            except Exception:
                status = {"state": "idle", "error": "daemon unreachable"}
            time.sleep(0.2)

    threading.Thread(target=_poller, daemon=True).start()

    # search-mode state
    mode = "now"                      # "now" | "search" | "cmd"
    sq, sresults, ssel = "", [], 0
    ssearching, smsg = False, ""
    sbucket: dict = {}
    cmdq, cmdmsg = "", ""

    # UI state: queue selection, lyrics pane, amplifier clock
    ui = {"qsel": 0, "qsel_follow": True,
          "lyr_on": False, "lyr_lines": [], "lyr_loading": False, "lyr_note": ""}
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

        # queue selection follows the current track until the user moves it
        if mode == "now" and ui["qsel_follow"] and status.get("current_index", -1) >= 0:
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
                  amp_t, ui, cmdq)
        stdscr.refresh()

        ch = stdscr.getch()
        ch = _resolve_key(stdscr, ch)
        if mode == "cmd":
            mode, cmdq, cmdmsg = _cmd_key(ch, mode, cmdq, cmdmsg)
        elif mode == "search":
            mode, sq, sresults, ssel, ssearching, smsg, sbucket = _search_key(
                ch, mode, sq, sresults, ssel, ssearching, smsg, sbucket)
        else:
            mode = _now_key(ch, mode, status, ui)
        if ui.pop("art", False):
            _show_art(stdscr)
        if mode == "quit":
            break

        curses.napms(100)


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


def _bg_send(verb: str, arg: str = "") -> None:
    """Fire a command without blocking the UI (results arrive via status poll)."""
    threading.Thread(target=lambda: send_cmd(verb, arg), daemon=True).start()


def _now_key(ch: int, mode: str, status: dict, ui: dict) -> str:
    if ch == -1:
        return mode
    if ui["lyr_on"]:  # lyrics view — transport works, l/esc exits
        if ch in (ord("l"), 27):
            ui["lyr_on"] = False
        elif ch == ord(" "):
            _bg_send("toggle")
        elif ch in (ord("n"), ord("k")):
            _bg_send("next")
        elif ch == ord("p"):
            _bg_send("prev")
        elif ch in (ord("+"), ord("=")):
            _bg_send("volume", "+5")
        elif ch == ord("-"):
            _bg_send("volume", "-5")
        return mode
    n = status.get("queue_len", 0)
    if ch in (ord("q"), 27):
        mode = "quit"  # sentinel: quit the TUI (music keeps playing)
    elif ch == ord("l"):
        _toggle_lyrics(ui)
    elif ch == curses.KEY_UP:
        ui["qsel"] = max(0, ui["qsel"] - 1)
        ui["qsel_follow"] = False
    elif ch == curses.KEY_DOWN:
        ui["qsel"] = min(max(0, n - 1), ui["qsel"] + 1)
        ui["qsel_follow"] = False
    elif ch in (10, 13, curses.KEY_ENTER) and n > 0:
        _bg_send("playindex", str(ui["qsel"] + 1))
        ui["qsel_follow"] = False
    elif ch == ord("d") and n > 0:
        _bg_send("remove", str(ui["qsel"] + 1))
        ui["qsel"] = max(0, min(ui["qsel"], max(0, n - 2)))
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
        _bg_send("seek", "+10")
    elif ch == curses.KEY_LEFT:
        _bg_send("seek", "-10")
    elif ch == ord("s"):
        _bg_send("shuffle")
    elif ch == ord("r"):
        _cycle_repeat(status)
    elif ch == ord("a"):
        ui["art"] = True
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


def _toggle_lyrics(ui: dict) -> None:
    if ui["lyr_on"]:
        ui["lyr_on"] = False
        return
    ui["lyr_on"] = True
    if not ui["lyr_lines"] and not ui["lyr_loading"]:
        ui["lyr_loading"] = True

        def worker() -> None:
            try:
                resp = send_cmd("lyrics")
                if resp.get("ok"):
                    ui["lyr_lines"] = (resp.get("data") or {}).get("lines", [])
                    ui["lyr_note"] = (resp.get("data") or {}).get("note", "")
                else:
                    ui["lyr_note"] = resp.get("error", "lyrics unavailable")
            except Exception as e:
                ui["lyr_note"] = str(e)
            ui["lyr_loading"] = False

        threading.Thread(target=worker, daemon=True).start()


def _cur_lyr_line(status: dict, ui: dict) -> int:
    pos = status.get("position", 0)
    lines = ui["lyr_lines"]
    for i, ln in enumerate(lines):
        if ln.get("start", 0) <= pos <= ln.get("end", pos):
            return i
    # fall back to the nearest line at/before the position
    for i, ln in enumerate(lines):
        if ln.get("end", 0) <= pos:
            return i
    return max(0, len(lines) - 1)


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
    elif ch == 9:  # tab -> add selected to queue
        if sresults and not ssearching:
            _add_url(sresults[ssel]["url"])
    elif ch in (curses.KEY_BACKSPACE, 127, 8):
        sq = sq[:-1]
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

def _draw(stdscr, status, h, w, mode, sq, sresults, ssel, ssearching, smsg,
          amp_t: float, ui: dict, cmdq: str = "") -> None:
    _draw_header(stdscr, status, w)
    _draw_amp(stdscr, status, amp_t, w)
    if mode == "search":
        _draw_search(stdscr, h, w, sq, sresults, ssel, ssearching, smsg)
    elif mode == "cmd":
        _draw_queue(stdscr, status, h, w, ui["qsel"])
        try:
            stdscr.addstr(h - 1, 0, (":" + cmdq + "_")[: w - 1], curses.A_BOLD)
        except curses.error:
            pass
    elif ui["lyr_on"]:
        _draw_lyrics(stdscr, status, ui, h, w)
        try:
            stdscr.addstr(h - 1, 0, ("space pause · n/p next/prev · +/- vol · "
                                     "l/esc back · q quit")[: w - 1], curses.A_DIM)
        except curses.error:
            pass
    else:
        _draw_queue(stdscr, status, h, w, ui["qsel"])
        try:
            stdscr.addstr(h - 1, 0, _NOW_HELP[: w - 1], curses.A_DIM)
        except curses.error:
            pass


def _draw_lyrics(stdscr, status: dict, ui: dict, h: int, w: int) -> None:
    top = 8
    window = max(0, (h - 3) - top)
    if window <= 0:
        return
    if ui["lyr_loading"]:
        try:
            stdscr.addstr(top, 0, "loading lyrics…", curses.A_DIM)
        except curses.error:
            pass
        return
    if not ui["lyr_lines"]:
        try:
            stdscr.addstr(top, 0, (ui["lyr_note"] or "no lyrics available")[: w - 1],
                          curses.color_pair(4))
        except curses.error:
            pass
        return
    pos = status.get("position", 0)
    cur = _cur_lyr_line(status, ui)
    start = max(0, cur - window // 2)
    for i in range(start, min(len(ui["lyr_lines"]), start + window)):
        row = top + (i - start)
        text = ui["lyr_lines"][i]["text"][: w - 1]
        if i == cur:  # karaoke highlight: elapsed part is reversed
            ln = ui["lyr_lines"][i]
            dur = max(0.001, ln.get("end", ln.get("start", 0) + 1) - ln.get("start", 0))
            n = int((pos - ln.get("start", 0)) / dur * len(text))
            n = max(0, min(len(text), n))
            try:
                stdscr.addstr(row, 0, text[:n], curses.A_REVERSE)
                stdscr.addstr(row, n, text[n:], curses.A_BOLD)
            except curses.error:
                pass
        else:
            attr = curses.A_DIM if abs(i - cur) > 2 else 0
            try:
                stdscr.addstr(row, 0, text, attr)
            except curses.error:
                pass


def _draw_amp(stdscr, status: dict, amp_t: float, w: int) -> None:
    """Animated equalizer row: bounces while playing, freezes on pause,
    crawls while loading, lies flat when idle."""
    state = status.get("state", "idle")
    if state == "idle":
        line = " " * w
    else:
        amp = min(1.0, 0.35 + (status.get("volume") or 80) / 160.0)
        heights = []
        for i in range(w):
            v = (math.sin(amp_t * (1.6 + (i % 7) * 0.35) + i * 1.7) + 1) / 2.0
            if state == "loading":
                v = v * 0.4 + 0.3
            heights.append(int(round(v * amp * 7.0)))
        line = "".join(_AMP_BLOCKS[min(7, h)] if h > 0 else " " for h in heights)
    try:
        stdscr.addstr(4, 0, line[: w - 1], curses.color_pair(3))
    except curses.error:
        pass


def _draw_header(stdscr, status: dict, w: int) -> None:
    state = status.get("state", "idle")
    mark = _STATE_MARK.get(state, "·")
    try:
        stdscr.addstr(0, 0, f" tune  {mark} {state}"[: w - 1], curses.color_pair(1))
    except curses.error:
        pass

    title = status.get("title") or "(nothing playing)"
    pos_s = _fmt_time(status.get("position"))
    dur = status.get("duration")
    dur_s = _fmt_time(dur)
    time_str = f"{pos_s} / {dur_s}"
    try:
        stdscr.addstr(1, 0, title[: w - 1])
    except curses.error:
        pass
    try:
        stdscr.addstr(1, max(0, w - len(time_str) - 1), time_str, curses.color_pair(3))
    except curses.error:
        pass

    frac = (status.get("position") or 0) / dur if dur else 0.0
    bw = max(0, w - 2)
    filled = int(bw * min(1.0, max(0.0, frac)))
    bar = "#" * filled + "-" * (bw - filled)
    try:
        stdscr.addstr(2, 0, "[" + bar + "]", curses.color_pair(3))
    except curses.error:
        pass

    idx = status.get("current_index", -1)
    meta = (f"vol {status.get('volume')} · repeat {status.get('repeat')} · "
            f"shuffle {'on' if status.get('shuffle') else 'off'} · "
            f"queue {idx + 1 if idx >= 0 else 0}/{status.get('queue_len', 0)}")
    sp = status.get("speed")
    if sp and sp != 1:
        meta += f" · {sp}×"
    if status.get("fav"):
        meta += " · ♥"
    rem = status.get("sleep_remaining")
    if rem:
        m, _s = divmod(int(rem), 60)
        meta += f" · ⏰ {m}m"
    err = status.get("error")
    if err:
        meta += f"   [!] {err}"
    try:
        stdscr.addstr(3, 0, meta[: w - 1], curses.color_pair(4) if err else curses.color_pair(3))
    except curses.error:
        pass


def _draw_queue(stdscr, status: dict, h: int, w: int, qsel: int) -> None:
    queue = status.get("queue") or []
    cur = status.get("current_index", -1)
    top = 6  # below the amplifier row
    bottom = max(top + 1, h - 3)
    page = bottom - top
    if page <= 0:
        return
    focus = qsel if 0 <= qsel < len(queue) else max(0, cur)
    start = max(0, min(focus - page // 2, max(0, len(queue) - page)))
    for i in range(start, min(len(queue), start + page)):
        t = queue[i]
        is_cur, is_sel = i == cur, i == qsel
        mark = "▶" if is_cur else ("▸" if is_sel else " ")
        body = f"{mark} {i + 1:2}  {t['title'][: max(0, w - 18)]}"
        text = f"{body:<{max(0, w - 10)}}  {_fmt_time(t.get('duration')):>5}"
        if is_sel and is_cur:
            attr = curses.color_pair(2) | curses.A_REVERSE
        elif is_sel:
            attr = curses.A_REVERSE
        elif is_cur:
            attr = curses.color_pair(2)
        else:
            attr = 0
        try:
            stdscr.addstr(top + (i - start), 0, text[: w - 1], attr)
        except curses.error:
            pass


def _draw_search(stdscr, h, w, sq, sresults, ssel, ssearching, smsg) -> None:
    # input line
    line = "Search: " + sq + "_"
    try:
        stdscr.addstr(6, 0, line[: w - 1], curses.A_BOLD)
    except curses.error:
        pass

    row = 8
    if ssearching:
        try:
            stdscr.addstr(row, 0, f"searching {sq!r}…"[: w - 1])
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
            attr = curses.A_REVERSE if i == ssel else 0
            try:
                stdscr.addstr(row, 0, body[: w - 1], attr)
            except curses.error:
                pass
            row += 1
    else:
        try:
            stdscr.addstr(row, 0, "type a song (e.g. /search coldplay) and press enter"[: w - 1],
                          curses.A_DIM)
        except curses.error:
            pass

    try:
        stdscr.addstr(h - 1, 0, _SEARCH_HELP[: w - 1], curses.A_DIM)
    except curses.error:
        pass
