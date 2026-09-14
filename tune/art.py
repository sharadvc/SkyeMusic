"""Album art rendering engine for skyemusic.

Renders album art using the best available method:
1. Kitty terminal graphics protocol (true pixel art, native resolution)
2. iTerm2 inline image protocol
3. ANSI truecolor half-block art (universal fallback)

When no art is available, renders the skyemusic cat mascot instead.
"""

from __future__ import annotations

import base64
import io
import os
import re
import struct
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

_ID_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")

# ─── Mascot ───────────────────────────────────────────────────────────────────
# Our cute skyemusic cat, shown when no track art is available
MASCOT_LINES = [
    "                                    ",
    "        /\\_____/\\                  ",
    "       /  o   o  \\   s k y e      ",
    "      ( ==  ^  == )   m u s i c   ",
    "       )         (                 ",
    "      (           )                ",
    "     ( (  )   (  ) )               ",
    "    (__(__)___(__)__)               ",
    "                                    ",
    "    ♪  nothing playing yet  ♪      ",
    "    press / to search a song       ",
]

# Coloured version using ANSI
def _mascot_ansi() -> list[str]:
    pink   = "\x1b[38;2;255;182;193m"
    purple = "\x1b[38;2;180;130;255m"
    white  = "\x1b[38;2;220;220;255m"
    dim    = "\x1b[38;2;130;130;160m"
    reset  = "\x1b[0m"
    out = []
    for i, line in enumerate(MASCOT_LINES):
        if i in (1, 2, 3):
            out.append(pink + line + reset)
        elif i in (4, 5, 6, 7):
            out.append(purple + line + reset)
        elif i == 9:
            out.append(white + line + reset)
        else:
            out.append(dim + line + reset)
    return out

# ─── Terminal detection ───────────────────────────────────────────────────────

def _supports_kitty() -> bool:
    """Check if the terminal supports the Kitty graphics protocol."""
    term = os.environ.get("TERM", "")
    term_prog = os.environ.get("TERM_PROGRAM", "")
    # Kitty, Ghostty, and WezTerm all support the Kitty graphics protocol
    return (
        "kitty" in term or
        "ghostty" in term_prog.lower() or
        "wezterm" in term_prog.lower() or
        os.environ.get("KITTY_WINDOW_ID") is not None
    )


def _supports_iterm2() -> bool:
    term_prog = os.environ.get("TERM_PROGRAM", "")
    return "iTerm" in term_prog


def _supports_sixel() -> bool:
    term = os.environ.get("TERM", "")
    return "sixel" in term or os.environ.get("COLORTERM", "") == "sixel"


# ─── Image fetching ───────────────────────────────────────────────────────────

def _video_id(url: str) -> str | None:
    m = _ID_RE.search(url)
    return m.group(1) if m else None


def _fetch_thumb(url: str) -> bytes | None:
    """Download the YouTube thumbnail as JPEG bytes."""
    vid = _video_id(url)
    if not vid:
        return None
    thumb_url = f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
    try:
        req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.read()
    except Exception:
        return None


# ─── Kitty graphics protocol ──────────────────────────────────────────────────

def _render_kitty(img_bytes: bytes, cols: int = 42, rows: int = 22) -> str:
    """Render image using Kitty terminal graphics protocol.

    Returns the escape sequence string to print directly.
    This renders actual pixels — no block art, real image quality.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        # Scale to fit the requested cell area (each cell ≈ 2:1 aspect ratio)
        target_w = cols * 8   # approx 8px per col
        target_h = rows * 16  # approx 16px per row
        img.thumbnail((target_w, target_h), Image.LANCZOS)
        # Encode as raw RGBA bytes for Kitty
        raw = img.tobytes()
        w, h = img.size
        encoded = base64.standard_b64encode(raw).decode()

        # Chunk the data — Kitty requires ≤4096 bytes per chunk
        chunks = [encoded[i:i+4096] for i in range(0, len(encoded), 4096)]
        parts = []
        for idx, chunk in enumerate(chunks):
            more = 1 if idx < len(chunks) - 1 else 0
            if idx == 0:
                # First chunk: full header
                header = f"a=T,f=32,s={w},v={h},q=2,m={more}"
            else:
                header = f"q=2,m={more}"
            parts.append(f"\x1b_G{header};{chunk}\x1b\\")

        # Move cursor down rows lines after the image
        return "".join(parts) + "\n" * rows
    except Exception:
        return ""


# ─── iTerm2 inline image protocol ────────────────────────────────────────────

def _render_iterm2(img_bytes: bytes, cols: int = 42) -> str:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        # Convert to PNG for iTerm2
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode()
        size = len(buf.getvalue())
        return f"\x1b]1337;File=inline=1;width={cols};height=auto;size={size}:{encoded}\a\n"
    except Exception:
        return ""


# ─── ANSI half-block art (universal) ─────────────────────────────────────────

def _render_ansi(img_bytes: bytes, cols: int = 42, rows: int = 22) -> list[str]:
    """Render image as ANSI truecolor half-block characters (▀).

    Two pixels per cell vertically using foreground + background colors.
    Works in every terminal that supports truecolor.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img = img.resize((cols, rows * 2), Image.LANCZOS)
        pixels = list(img.getdata())
        lines = []
        for row in range(rows):
            cells = []
            for col in range(cols):
                top = pixels[row * 2 * cols + col]
                bot_idx = (row * 2 + 1) * cols + col
                bot = pixels[bot_idx] if bot_idx < len(pixels) else (0, 0, 0)
                tr, tg, tb = top
                br, bg, bb = bot
                cells.append(
                    f"\x1b[38;2;{tr};{tg};{tb}m"
                    f"\x1b[48;2;{br};{bg};{bb}m▀"
                )
            lines.append("".join(cells) + "\x1b[0m")
        return lines
    except Exception:
        return []


# ─── BMP fallback (no PIL, uses sips on macOS) ───────────────────────────────

def _render_sips_ansi(url: str, cols: int = 42, rows: int = 22) -> list[str]:
    """Fallback: use macOS sips + raw BMP parsing (no PIL needed)."""
    vid = _video_id(url)
    if not vid:
        return []
    with tempfile.TemporaryDirectory() as d:
        jpg = os.path.join(d, "a.jpg")
        bmp = os.path.join(d, "a.bmp")
        try:
            urllib.request.urlretrieve(
                f"https://img.youtube.com/vi/{vid}/hqdefault.jpg", jpg
            )
        except Exception:
            return []
        try:
            subprocess.run(
                ["sips", "-s", "format", "bmp", "-z", str(rows * 2), str(cols),
                 jpg, "--out", bmp],
                capture_output=True, timeout=30
            )
        except Exception:
            return []
        try:
            with open(bmp, "rb") as f:
                data = f.read()
        except OSError:
            return []
        if len(data) < 54:
            return []
        w = int.from_bytes(data[18:22], "little")
        h = int.from_bytes(data[22:26], "little", signed=True)
        bpp = int.from_bytes(data[28:30], "little")
        if bpp != 24 or w <= 0 or h == 0:
            return []
        row_size = ((w * 3 + 3) // 4) * 4
        ah = abs(h)
        bmp_rows = []
        for y in range(ah):
            off = 54 + y * row_size
            bmp_rows.append([
                (data[off + x*3+2], data[off + x*3+1], data[off + x*3])
                for x in range(w)
            ])
        if h > 0:
            bmp_rows.reverse()
        lines = []
        for y in range(0, len(bmp_rows), 2):
            top = bmp_rows[y]
            bot = bmp_rows[y+1] if y+1 < len(bmp_rows) else [(0,0,0)] * w
            cells = [
                f"\x1b[38;2;{rt};{gt};{bt}m\x1b[48;2;{rb};{gb};{bb}m▀"
                for (rt,gt,bt),(rb,gb,bb) in zip(top, bot)
            ]
            lines.append("".join(cells) + "\x1b[0m")
        return lines


def _bmp_rows(bmp_path: str) -> list[list[tuple[int, int, int]]] | None:
    try:
        with open(bmp_path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) < 54:
        return None
    w = int.from_bytes(data[18:22], "little")
    h = int.from_bytes(data[22:26], "little", signed=True)
    bpp = int.from_bytes(data[28:30], "little")
    if bpp != 24 or w <= 0 or h == 0:
        return None
    row_size = ((w * 3 + 3) // 4) * 4
    ah = abs(h)
    if len(data) < 54 + ah * row_size:
        return None
    bmp_rows = []
    for y in range(ah):
        off = 54 + y * row_size
        bmp_rows.append([
            (data[off + x * 3 + 2], data[off + x * 3 + 1], data[off + x * 3])
            for x in range(w)
        ])
    if h > 0:
        bmp_rows.reverse()
    return bmp_rows


# ─── Public API ───────────────────────────────────────────────────────────────

def render(url: str, cols: int = 42, rows: int = 22) -> list[str]:
    """Render album art for `url`. Returns list of printable lines.

    Automatically picks the best renderer:
    - Kitty/Ghostty/WezTerm → native pixel image (stunning)
    - iTerm2                 → inline PNG image
    - Everything else        → ANSI half-block truecolor art

    If no art is available, returns the skyemusic cat mascot.
    """
    img_bytes = _fetch_thumb(url)

    if img_bytes:
        if _supports_kitty():
            seq = _render_kitty(img_bytes, cols, rows)
            if seq:
                return [seq]  # single string — print it directly

        if _supports_iterm2():
            seq = _render_iterm2(img_bytes, cols)
            if seq:
                return [seq]

        # PIL ANSI half-block
        try:
            lines = _render_ansi(img_bytes, cols, rows)
            if lines:
                return lines
        except Exception:
            pass

    # Fallback: sips BMP (macOS, no PIL)
    if url:
        lines = _render_sips_ansi(url, cols, rows)
        if lines:
            return lines

    # Last resort: mascot
    return _mascot_ansi()


def render_mascot() -> list[str]:
    """Always return the skyemusic cat mascot (used when player is idle)."""
    return _mascot_ansi()


def print_art(url: str, cols: int = 42, rows: int = 22) -> None:
    """Convenience: render and print art to stdout immediately."""
    lines = render(url, cols, rows)
    for line in lines:
        print(line)
