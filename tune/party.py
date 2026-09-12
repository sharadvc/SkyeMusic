"""Multi-Room Synchronized Audio Streaming & Party Engine for tune."""

from __future__ import annotations

import json
import time
import threading
from typing import TYPE_CHECKING, Dict, Set

if TYPE_CHECKING:
    from .daemon import Daemon


class PartySession:
    def __init__(self):
        self.active: bool = False
        self.token: str = ""
        self.global_url: str | None = None
        self.start_time: float = 0.0
        self.listeners: Set[str] = set()
        self._lock = threading.Lock()

    def start(self, token: str, global_url: str | None = None) -> None:
        with self._lock:
            self.active = True
            self.token = token
            self.global_url = global_url
            self.start_time = time.time()
            self.listeners.clear()

    def stop(self) -> None:
        from .tunnel import stop_tunnel
        with self._lock:
            self.active = False
            self.token = ""
            self.global_url = None
            self.listeners.clear()
        stop_tunnel()

    def register_listener(self, client_id: str) -> None:
        with self._lock:
            self.listeners.add(client_id)

    def unregister_listener(self, client_id: str) -> None:
        with self._lock:
            self.listeners.discard(client_id)

    def get_info(self, daemon: Daemon) -> dict:
        with self._lock:
            cur = daemon.q.current() if daemon else None
            pos = daemon.player.get_property("time-pos") if (daemon and daemon.player) else 0.0
            return {
                "active": self.active,
                "token": self.token,
                "global_url": self.global_url,
                "listeners_count": len(self.listeners),
                "server_time": time.time(),
                "track": {
                    "title": cur.title if cur else None,
                    "url": cur.url if cur else None,
                    "duration": cur.duration if cur else None,
                } if cur else None,
                "position": float(pos) if pos is not None else 0.0,
                "state": daemon._state if daemon else "idle",
            }


# Global party session instance
party_engine = PartySession()
