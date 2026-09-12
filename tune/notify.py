"""Native OS System Notifications & Album Art Banner Integration for tune."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Optional

from .queue import CONFIG_DIR, Track

ART_CACHE_DIR = CONFIG_DIR / "art_cache"


def _download_artwork(url: str) -> Optional[str]:
    """Download and cache album artwork for notifications."""
    if not url:
        return None
    try:
        ART_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
        cache_path = ART_CACHE_DIR / f"{url_hash}.jpg"
        if cache_path.exists():
            return str(cache_path)

        # Download thumbnail image
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
            if data:
                with open(cache_path, "wb") as f:
                    f.write(data)
                return str(cache_path)
    except Exception:
        pass
    return None


def notify_track(track: Track) -> None:
    """Send native OS notification with title, artist, and album artwork."""
    if not track or not track.title:
        return

    def worker():
        title = track.title
        channel = track.channel or "Skye Player"
        img_url = track.url

        # Download artwork in background for thumbnail banner
        img_path = None
        if getattr(track, "url", None):
            # Attempt thumbnail extraction
            yt_id = None
            if "watch?v=" in track.url:
                yt_id = track.url.split("watch?v=")[1].split("&")[0]
            elif "youtu.be/" in track.url:
                yt_id = track.url.split("youtu.be/")[1].split("?")[0]
            if yt_id:
                thumb_url = f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg"
                img_path = _download_artwork(thumb_url)

        # macOS system notification
        if sys.platform == "darwin":
            terminal_notifier = shutil.which("terminal-notifier")
            if terminal_notifier and img_path and os.path.exists(img_path):
                try:
                    subprocess.run(
                        [
                            terminal_notifier,
                            "-title", f"▶ {title[:40]}",
                            "-subtitle", channel[:40],
                            "-message", "Skye Player · Now Playing",
                            "-contentImage", img_path,
                            "-group", "tune_player",
                        ],
                        capture_output=True,
                        timeout=3,
                    )
                    return
                except Exception:
                    pass

            # AppleScript osascript fallback
            try:
                safe_title = title.replace('"', '\\"').replace("'", "\\'")[:50]
                safe_channel = channel.replace('"', '\\"').replace("'", "\\'")[:40]
                script = (
                    f'display notification "{safe_channel}" '
                    f'with title "▶ {safe_title}" '
                    'subtitle "Skye Player"'
                )
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3)
            except Exception:
                pass
        # Linux notify-send fallback
        elif sys.platform.startswith("linux"):
            notify_send = shutil.which("notify-send")
            if notify_send:
                try:
                    cmd = [notify_send, "-a", "Skye Player", f"▶ {title}", channel]
                    if img_path and os.path.exists(img_path):
                        cmd.extend(["-i", img_path])
                    subprocess.run(cmd, capture_output=True, timeout=3)
                except Exception:
                    pass

    threading.Thread(target=worker, daemon=True).start()
