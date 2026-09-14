import time
import curses
import sys
import datetime
from .client import send_cmd
from .tui import _resolve_key

def _render_focus_tui(stdscr, query: str, duration_mins: int):
    # Moody dark background
    sys.stdout.write("\033]11;#0f0f13\007")
    sys.stdout.flush()
    
    curses.curs_set(0)
    stdscr.nodelay(True)
    curses.use_default_colors()
    
    # Initialize colors
    curses.init_pair(1, curses.COLOR_MAGENTA, -1)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_WHITE, -1)
    
    start_time = time.monotonic()
    end_time = start_time + (duration_mins * 60)
    
    # Start the music
    if query:
        send_cmd("mood", query)
    
    last_poll = 0
    status = {}
    
    while True:
        now = time.monotonic()
        
        # Poll daemon every second
        if now - last_poll > 1.0:
            resp = send_cmd("status")
            if resp.get("ok"):
                status = resp.get("data", {})
            last_poll = now
            
        remaining = max(0.0, end_time - now)
        if remaining <= 0:
            break
            
        m, s = divmod(int(remaining), 60)
        time_str = f"{m:02d}:{s:02d}"
        
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        
        # Draw central timer
        box_w = 40
        box_h = 7
        top = (h - box_h) // 2
        left = (w - box_w) // 2
        
        try:
            # Banner
            stdscr.addstr(top, left + 10, "✦ DEEP FOCUS OS ✦", curses.color_pair(1) | curses.A_BOLD)
            
            # Big Timer
            stdscr.addstr(top + 3, left + 15, time_str, curses.color_pair(3) | curses.A_BOLD)
            
            # Progress bar
            pct = 1.0 - (remaining / (duration_mins * 60))
            bar_len = int(pct * (box_w - 4))
            bar = "━" * bar_len + "─" * (box_w - 4 - bar_len)
            stdscr.addstr(top + 5, left + 2, bar, curses.color_pair(2))
            
            # Current Track Info
            track_title = status.get("title", "No track playing")
            if len(track_title) > w - 10:
                track_title = track_title[:max(0, w-13)] + ("..." if w > 13 else "")
            stdscr.addstr(h - 3, max(0, (w - len(track_title)) // 2), track_title, curses.color_pair(3) | curses.A_DIM)
            
            stdscr.addstr(h - 1, (w - 20) // 2, "Press 'q' to exit focus", curses.color_pair(1) | curses.A_DIM)
        except curses.error:
            pass
            
        stdscr.refresh()
        
        ch = stdscr.getch()
        ch = _resolve_key(stdscr, ch)
        if ch in (ord('q'), ord('Q'), 27):
            break
            
        curses.napms(50)

def start_focus(query: str, duration_mins: int = 60):
    try:
        curses.wrapper(_render_focus_tui, query, duration_mins)
    finally:
        # Restore terminal background
        sys.stdout.write("\033]111;\007")
        sys.stdout.flush()
        print("Focus session ended. Great work!")
