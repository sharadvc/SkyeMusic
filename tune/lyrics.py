"""Fetch synced karaoke lyrics (the video's subtitles) via yt-dlp.

Uses the auto-generated/uploaded English subs from the video — no API key.
Returns a list of `{start, end, text}` (seconds). Empty list = unavailable.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_TS_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{1,3})")


def _parse_ts(ts: str) -> float:
    m = _TS_RE.match(ts)
    if not m:
        return 0.0
    h, mnt, s, ms = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_vtt(text: str) -> list[dict]:
    """Parse WebVTT subtitle text into [{start, end, text}]."""
    lines: list[dict] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block or block.startswith("WEBVTT"):
            continue
        first, _, rest = block.partition("\n")
        m = re.match(r"(\d{2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{1,3})", first)
        if not m:
            continue
        start, end = _parse_ts(m.group(1)), _parse_ts(m.group(2))
        body = re.sub(r"<[^>]+>", "", rest).strip()
        body = re.sub(r"align:.*", "", body).strip()  # drop cue settings
        body = (body.replace("&nbsp;", " ").replace("&amp;", "&")
                    .replace("&quot;", '"').replace("&#39;", "'")
                    .replace("&rarr;", "→").strip())
        if body:
            lines.append({"start": start, "end": end, "text": body})
    return lines


def fetch(url: str, timeout: int = 60) -> list[dict]:
    """Download the video's English subs and return timed lyric lines."""
    ytdl = shutil.which("yt-dlp") or str(Path.home() / ".local/bin/yt-dlp")
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "lyr")
        try:
            subprocess.run(
                [ytdl, "--skip-download", "--write-subs", "--sub-langs", "en",
                 "--sub-format", "vtt", "-o", out, url],
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return []
        vtts = sorted(Path(d).glob("*.vtt"))
        if not vtts:
            return []
        return parse_vtt(vtts[0].read_text(errors="replace"))
