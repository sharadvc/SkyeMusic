"""Track model, queue state, and shared runtime paths for tune."""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- Runtime paths ---------------------------------------------------------
# Shared by every module; kept here because the queue owns the persistence.
_HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", _HOME / ".config")) / "tune"
QUEUE_FILE = CONFIG_DIR / "queue.json"
LOCK_FILE = CONFIG_DIR / "daemon.lock"
LOG_FILE = CONFIG_DIR / "tune.log"
PLAYLIST_DIR = CONFIG_DIR / "playlists"
FAVORITES_FILE = CONFIG_DIR / "favorites.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
BOOKMARKS_FILE = CONFIG_DIR / "bookmarks.json"
DOWNLOADS_DIR = CONFIG_DIR / "downloads"
DOWNLOADS_INDEX = DOWNLOADS_DIR / "index.json"

# Sockets live in $TMPDIR, which is per-user on macOS.
_SOCK_DIR = Path(os.environ.get("TMPDIR", "/tmp"))
CTRL_SOCK = _SOCK_DIR / "tune-ctrl.sock"
MPV_SOCK = _SOCK_DIR / "tune-mpv.sock"

VOLUME_MAX = 130


def _channel_key(t) -> str:
    return getattr(t, "channel", "")


def shuffle_no_adjacent(tracks: list, key=None, prev: str = ""):
    """Shuffle `tracks`, avoiding back-to-back same `key` when possible.

    Greedy: after a random start, each pick prefers a track whose key differs
    from the previous one (and from `prev`, the key of the track played before
    the list, if any). Falls back to the next track when no separation exists.
    """
    if key is None:
        key = _channel_key
    if len(tracks) < 2:
        return list(tracks)
    pool = list(tracks)
    random.shuffle(pool)
    # if a previous track is given, start with a track that differs from it
    if prev:
        for i, t in enumerate(pool):
            if key(t) != prev:
                pool[0], pool[i] = pool[i], pool[0]
                break
    out = [pool.pop(0)]
    last = key(out[0])
    while pool:
        pick = None
        for i, t in enumerate(pool):
            if key(t) != last:
                pick = pool.pop(i)
                break
        if pick is None:
            pick = pool.pop(0)
        out.append(pick)
        last = key(pick)
    return out


@dataclass
class Track:
    """One playable item: a resolved YouTube video."""

    query: str
    title: str
    url: str
    duration: float | None = None
    channel: str = ""

    def as_json(self) -> dict:
        return asdict(self)


@dataclass
class QueueState:
    """The player's full state: playlist + playback settings.

    `index` is -1 when nothing is loaded. `repeat` is one of off|all|one.
    `positions` maps track URL -> last play position (per-track resume).
    """

    tracks: list[Track] = field(default_factory=list)
    index: int = -1
    repeat: str = "off"
    shuffle: bool = False
    volume: int = 80
    speed: float = 1.0
    position: float = 0.0  # last known position, for crash recovery
    positions: dict[str, float] = field(default_factory=dict)  # url -> seconds

    def current(self) -> Track | None:
        if 0 <= self.index < len(self.tracks):
            return self.tracks[self.index]
        return None

    def reshuffle_after_current(self) -> None:
        """Shuffle only the tracks after the current one, keeping it stable
        and (when possible) avoiding back-to-back same-channel tracks."""
        if self.index < 0:
            self.tracks = shuffle_no_adjacent(self.tracks, key=lambda t: t.channel)
            return
        head = self.tracks[: self.index + 1]
        tail = self.tracks[self.index + 1 :]
        cur = head[-1]
        self.tracks = head + shuffle_no_adjacent(tail, key=lambda t: t.channel,
                                                 prev=cur.channel)

    # --- persistence --------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "tracks": [t.as_json() for t in self.tracks],
            "index": self.index,
            "repeat": self.repeat,
            "shuffle": self.shuffle,
            "volume": self.volume,
            "speed": self.speed,
            "position": round(self.position, 1),
            "positions": {k: round(v, 1) for k, v in self.positions.items()},
        }

    @classmethod
    def from_json(cls, d: dict) -> QueueState:
        q = cls()
        q.tracks = [Track(**t) for t in d.get("tracks", [])]
        q.index = int(d.get("index", -1))
        q.repeat = d.get("repeat", "off")
        q.shuffle = bool(d.get("shuffle", False))
        q.volume = int(d.get("volume", 80))
        q.speed = float(d.get("speed", 1.0))
        q.position = float(d.get("position", 0.0))
        q.positions = {k: float(v) for k, v in (d.get("positions") or {}).items()}
        return q

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = QUEUE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=2))
        os.replace(tmp, QUEUE_FILE)  # atomic on POSIX

    def load(self) -> None:
        try:
            d = json.loads(QUEUE_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return
        try:
            loaded = QueueState.from_json(d)
        except (ValueError, TypeError, KeyError):
            return
        self.tracks = loaded.tracks
        self.index = loaded.index
        self.repeat = loaded.repeat
        self.shuffle = loaded.shuffle
        self.volume = loaded.volume
        self.speed = loaded.speed
        self.position = loaded.position
        self.positions = loaded.positions
