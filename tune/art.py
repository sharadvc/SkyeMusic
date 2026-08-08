"""Render a video thumbnail as ANSI truecolor half-block art.

The daemon has the video URL, so we pull the YouTube thumbnail, downscale it
with macOS `sips` (built-in), parse the resulting 24-bit BMP in pure Python,
and emit truecolor ANSI lines using half-block chars (2 vertical pixels/cell).
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import urllib.request

_ID_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")


def _video_id(url: str) -> str | None:
    m = _ID_RE.search(url)
    return m.group(1) if m else None


def _bmp_rows(path: str):
    try:
        with open(path, "rb") as f:
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
    if len(data) < 54 + (ah - 1) * row_size + w * 3:
        return None  # file truncated before the last row
    rows = []
    for y in range(ah):
        off = 54 + y * row_size
        rows.append([(data[off + x * 3 + 2], data[off + x * 3 + 1], data[off + x * 3])
                     for x in range(w)])
    if h > 0:
        rows.reverse()  # positive height BMPs are stored bottom-up
    return rows


def render(url: str, width: int = 56, height: int = 32) -> list[str]:
    """Return ANSI truecolor half-block lines for the video's thumbnail."""
    vid = _video_id(url)
    if not vid:
        return []
    with tempfile.TemporaryDirectory() as d:
        jpg = os.path.join(d, "a.jpg")
        bmp = os.path.join(d, "a.bmp")
        try:
            urllib.request.urlretrieve(f"https://img.youtube.com/vi/{vid}/hqdefault.jpg", jpg)
        except Exception:
            return []
        try:
            subprocess.run(["sips", "-s", "format", "bmp", "-z", str(height), str(width),
                            jpg, "--out", bmp],
                           capture_output=True, timeout=30)
        except Exception:
            return []
        rows = _bmp_rows(bmp)
        if not rows:
            return []
        lines = []
        for y in range(0, len(rows), 2):
            top = rows[y]
            bot = rows[y + 1] if y + 1 < len(rows) else [(0, 0, 0)] * len(top)
            cells = [f"\x1b[38;2;{rt};{gt};{bt}m\x1b[48;2;{rb};{gb};{bb}m▀"
                     for (rt, gt, bt), (rb, gb, bb) in zip(top, bot)]
            lines.append("".join(cells) + "\x1b[0m")
        return lines
