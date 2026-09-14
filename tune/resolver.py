"""Resolve a search query / URL / video id into a Track using yt-dlp.

Uses `--flat-playlist --dump-single-json` so no formats are fetched — a fast
metadata-only lookup (verified empirically: `--print` with `\\t` separators
emits them literally, so we parse JSON instead).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .queue import Track

# A bare 11-char YouTube video id (yt-dlp accepts these directly).
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Cap concurrent yt-dlp subprocesses. Raised to 4 so DJ mode batch-loading
# and prefetch can run in parallel without serializing. YouTube can handle this.
_YTDLP_SEM = threading.Semaphore(4)



class ResolveError(Exception):
    pass


def _ytdlp_bin() -> str:
    p = shutil.which("yt-dlp")
    if p:
        return p
    fallback = Path.home() / ".local" / "bin" / "yt-dlp"
    if fallback.exists():
        return str(fallback)
    return "yt-dlp"


def _is_url_or_id(arg: str) -> bool:
    if "://" in arg:
        return True
    return bool(_ID_RE.match(arg))


def _entry_to_track(entry: dict, query: str) -> Track | None:
    vid = entry.get("id")
    if not vid:
        return None
    return Track(
        query=query,
        title=entry.get("title") or query,
        url=f"https://www.youtube.com/watch?v={vid}",
        duration=entry.get("duration"),
        channel=entry.get("channel") or "",
    )


def _fetch(target: str, timeout: int = 20) -> list[dict] | None:
    """Run yt-dlp once; return the raw entry dicts (or None on failure)."""
    try:
        with _YTDLP_SEM:
            proc = subprocess.run(
                [_ytdlp_bin(), "--flat-playlist", "--no-warnings",
                 "--socket-timeout", "8", "--retries", "1",
                 "--dump-single-json", target],
                capture_output=True, text=True, timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        raise ResolveError(f"yt-dlp timed out resolving {target!r}") from None
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ResolveError(proc.stderr.strip() or "no result")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ResolveError(f"yt-dlp returned malformed output: {e}") from e
    if isinstance(data, dict) and data.get("entries"):
        return data["entries"]
    if isinstance(data, dict):
        return [data]
    return list(data) if isinstance(data, list) else []



def get_direct_url(url: str, timeout: int = 15) -> str:
    """Resolve a watch URL to a direct media URL (`yt-dlp -g`).

    mpv can stream this URL without running its own yt-dlp extraction, so a
    prefetched direct URL makes track changes start almost instantly.
    """
    try:
        with _YTDLP_SEM:
            proc = subprocess.run(
                [_ytdlp_bin(), "-f", "bestaudio/best",
                 "--no-warnings", "--no-playlist",
                 "--socket-timeout", "8", "--retries", "1",
                 "-g", url],
                capture_output=True, text=True, timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        raise ResolveError(
            f"yt-dlp timed out resolving direct url for {url!r}") from None
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ResolveError(proc.stderr.strip() or "no direct url")
    direct = proc.stdout.strip().splitlines()[0].strip()
    if not direct.startswith("http"):
        raise ResolveError("unexpected direct url output")
    return direct




def resolve(arg: str, timeout: int = 20) -> Track:
    """Return a Track for a free-text query, a URL, or a bare video id."""
    target = arg if _is_url_or_id(arg) else f"ytsearch1:{arg}"
    last_err = ""
    for attempt in range(2):
        try:
            entries = _fetch(target, timeout)
        except ResolveError as e:
            last_err = str(e)
        else:
            for entry in entries:
                track = _entry_to_track(entry, arg)
                if track is not None:
                    return track
            last_err = "no video id in yt-dlp output"
        if attempt == 0:
            time.sleep(0.3)  # brief pause before retry (was 1s)
    raise ResolveError(f"could not resolve {arg!r}: {last_err}")


def search(query: str, limit: int = 15, timeout: int = 20) -> list[Track]:
    """Return up to `limit` candidate Tracks for a free-text query."""
    last_err = ""
    for attempt in range(2):
        try:
            entries = _fetch(f"ytsearch{limit}:{query}", timeout)
        except ResolveError as e:
            last_err = str(e)
        else:
            tracks = []
            for entry in entries:
                track = _entry_to_track(entry, query)
                if track is not None:
                    tracks.append(track)
            return tracks
        if attempt == 0:
            time.sleep(0.3)
    raise ResolveError(f"search failed for {query!r}: {last_err}")



def is_playlist_url(arg: str) -> bool:
    """True only for a dedicated YouTube playlist page."""
    return "youtube.com/playlist?list=" in arg.replace("www.", "")


def resolve_playlist(url: str, timeout: int = 60) -> list[Track]:
    """Resolve every video in a YouTube playlist URL into Tracks (one yt-dlp call)."""
    try:
        entries = _fetch(url, timeout)
    except ResolveError as e:
        raise ResolveError(f"could not resolve playlist {url!r}: {e}") from e
    tracks = [t for e in entries if (t := _entry_to_track(e, url)) is not None]
    if not tracks:
        raise ResolveError(f"playlist {url!r} has no playable videos")
    return tracks


def resolve_radio(url: str, timeout: int = 60) -> list[Track]:
    """Resolve YouTube's related 'mix' for a video (for smart radio/autoplay).

    Returns related tracks, excluding the source video itself.
    """
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
    if not m:
        return []
    vid = m.group(1)
    mix = f"https://www.youtube.com/watch?v={vid}&list=RD{vid}"
    try:
        entries = _fetch(mix, timeout)
    except ResolveError:
        return []
    tracks = [t for e in entries if (t := _entry_to_track(e, mix)) is not None
              and t.url.rsplit("=", 1)[-1] != vid]
    return tracks[:20]
