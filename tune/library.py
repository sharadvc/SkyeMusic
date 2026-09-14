"""Local Music Library Indexer & Scanner for Skye Music Player.

Scans local audio files (~/Music or custom path) and builds a structured metadata
index (Artist -> Album -> Track Tree) stored at `~/.config/tune/library.json`.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any

from .config import CONFIG_DIR

LIBRARY_FILE = CONFIG_DIR / "library.json"
SUPPORTED_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".opus", ".ogg", ".aac"}


def scan_directory(target_path: str = "~/Music") -> dict[str, Any]:
    """Scan directory for audio files and extract metadata tags."""
    target = pathlib.Path(os.path.expanduser(target_path)).resolve()
    if not target.exists():
        return {"error": f"directory '{target}' does not exist", "count": 0}

    tracks: list[dict[str, Any]] = []

    for root, _, files in os.walk(target):
        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_EXTS:
                full_p = os.path.join(root, f)
                rel_p = os.path.relpath(full_p, target)
                
                # Basic filename parsing as fallback (Artist - Title)
                base_name = os.path.splitext(f)[0]
                parts = base_name.split(" - ", 1)
                if len(parts) == 2:
                    artist, title = parts[0].strip(), parts[1].strip()
                else:
                    artist, title = "Unknown Artist", base_name.strip()

                album = os.path.basename(root) if root != str(target) else "Single Tracks"

                tracks.append({
                    "title": title,
                    "artist": artist,
                    "channel": artist,
                    "album": album,
                    "url": f"file://{full_p}",
                    "path": full_p,
                    "query": title,
                    "duration": None,
                    "ext": ext,
                })

    index_data = {
        "updated_at": time.time(),
        "path": str(target),
        "count": len(tracks),
        "tracks": tracks,
    }

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp_p = LIBRARY_FILE.with_suffix(".tmp")
    tmp_p.write_text(json.dumps(index_data, indent=2))
    os.replace(tmp_p, LIBRARY_FILE)

    return {"ok": True, "count": len(tracks), "path": str(target)}


def load_library() -> dict[str, Any]:
    """Load cached local library index."""
    if not LIBRARY_FILE.exists():
        return {"count": 0, "tracks": []}
    try:
        return json.loads(LIBRARY_FILE.read_text())
    except Exception:
        return {"count": 0, "tracks": []}
