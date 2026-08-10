"""tune configuration (~/.config/tune/config.json).

Read at daemon start; falls back to built-in defaults. All keys optional.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .queue import CONFIG_DIR

DEFAULTS = {
    "volume": 80,             # default volume for a fresh start
    "repeat": "off",          # off | all | one
    "theme": "default",       # color theme name (see tui.py _THEMES)
    "autoplay": False,        # smart radio: keep playing similar songs when the queue ends
    "notifications": True,    # macOS now-playing notification on track change
    "gapless": True,          # --gapless-audio=yes
    "replaygain": True,       # --replaygain=track (loudness normalization)
    "device": "",             # audio device name ("" = default)
    "download_dir": "",       # where `tune download` saves files ("" = ~/Downloads/tune)
    "http_port": 8765,        # phone/HTTP remote control port (0 = disabled)
    "remote_pin": "",         # optional 4+ digit PIN for the phone remote ("" = no pin)
    "media_keys": True,       # macOS global media-key control (play/pause/next/prev)
    "smart_queue": False,     # keep appending related tracks when the queue runs short
    "resume": False,          # per-track resume (podcast mode): remember each track's position
    "intro_skip": 0,          # seconds to skip at the start of a never-resumed track
    "listenbrainz_token": "", # scrobble to ListenBrainz ("" = disabled)
    "mix_count": 20,           # how many search results `tune mix` includes
    "on_track_change": "",    # shell command run on every track change (receives title/url)
}


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or (CONFIG_DIR / "config.json")
        self.data: dict = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            d = json.loads(self.path.read_text())
            if isinstance(d, dict):
                for k, v in d.items():
                    self.data[k] = v
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        os.replace(tmp, self.path)  # atomic on POSIX
