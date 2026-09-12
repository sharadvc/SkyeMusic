"""Offline audio downloader module for tune."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .queue import DOWNLOADS_DIR, DOWNLOADS_INDEX, Track

_lock = threading.Lock()


def get_downloads_index() -> dict[str, dict]:
    """Return map of track URL -> metadata dict for offline downloads."""
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if not DOWNLOADS_INDEX.exists():
        return {}
    try:
        with open(DOWNLOADS_INDEX, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def is_downloaded(url: str) -> bool:
    """Return True if url is downloaded locally and the file exists."""
    if not url:
        return False
    idx = get_downloads_index()
    meta = idx.get(url)
    if not meta:
        return False
    p = meta.get("file_path")
    return bool(p and os.path.exists(p))


def get_local_path(url: str) -> str | None:
    """Return absolute file path if downloaded, else None."""
    if is_downloaded(url):
        return get_downloads_index()[url]["file_path"]
    return None


def save_download_meta(url: str, meta: dict) -> None:
    with _lock:
        idx = get_downloads_index()
        idx[url] = meta
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        with open(DOWNLOADS_INDEX, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=2)


def remove_download(url: str) -> bool:
    with _lock:
        idx = get_downloads_index()
        if url in idx:
            p = idx[url].get("file_path")
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            idx.pop(url, None)
            with open(DOWNLOADS_INDEX, "w", encoding="utf-8") as f:
                json.dump(idx, f, ensure_ascii=False, indent=2)
            return True
        return False


def download_track(track: Track, on_complete: Callable[[bool, str], None] | None = None) -> None:
    """Download a track audio file in background using yt-dlp."""
    if not track or not track.url:
        if on_complete:
            on_complete(False, "invalid track")
        return

    if is_downloaded(track.url):
        if on_complete:
            on_complete(True, get_local_path(track.url) or "")
        return

    def worker():
        ytdl = shutil.which("yt-dlp") or str(Path.home() / ".local/bin/yt-dlp")
        if not os.path.exists(ytdl) and not shutil.which("yt-dlp"):
            if on_complete:
                on_complete(False, "yt-dlp executable not found")
            return

        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", track.title or track.url)[:60]
        out_tmpl = str(DOWNLOADS_DIR / f"{safe_name}.%(ext)s")

        cmd = [
            ytdl,
            "--no-playlist",
            "-f", "bestaudio/best",
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "-o", out_tmpl,
            track.url,
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                target_file = None
                for f in DOWNLOADS_DIR.glob(f"{safe_name}.*"):
                    if f.suffix in (".mp3", ".m4a", ".opus", ".webm"):
                        target_file = str(f)
                        break
                if target_file:
                    save_download_meta(track.url, {
                        "url": track.url,
                        "title": track.title,
                        "channel": track.channel,
                        "query": track.query,
                        "duration": track.duration,
                        "file_path": target_file,
                        "size": os.path.getsize(target_file),
                        "downloaded_at": time.time(),
                    })
                    if on_complete:
                        on_complete(True, target_file)
                    return
            if on_complete:
                on_complete(False, res.stderr or "download failed")
        except Exception as e:
            if on_complete:
                on_complete(False, str(e))

    threading.Thread(target=worker, daemon=True).start()
