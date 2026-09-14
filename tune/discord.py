"""Discord Rich Presence (RPC) integration for Skye Music Player.

Communicates with Discord desktop client via native IPC socket (`/tmp/discord-ipc-0`)
without external dependencies.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
from typing import Any

# Default Client ID for Skye Music Player Discord Rich Presence
DISCORD_CLIENT_ID = "1214000000000000000"


class DiscordRPC:
    """Non-blocking Discord Rich Presence IPC client."""

    def __init__(self, client_id: str = DISCORD_CLIENT_ID) -> None:
        self.client_id = client_id
        self.socket: socket.socket | None = None
        self.connected = False
        self._lock = threading.Lock()
        self._last_update = 0.0

    def _find_ipc_path(self) -> str | None:
        """Locate active Discord IPC socket path across macOS and Linux."""
        temp_dirs = [
            os.environ.get("TMPDIR", ""),
            os.environ.get("TEMP", ""),
            os.environ.get("TMP", ""),
            "/tmp",
            f"/run/user/{os.getuid()}" if hasattr(os, "getuid") else "",
        ]
        for base in temp_dirs:
            if not base or not os.path.exists(base):
                continue
            for i in range(10):
                path = os.path.join(base, f"discord-ipc-{i}")
                if os.path.exists(path):
                    return path
        return None

    def connect(self) -> bool:
        """Establish handshake connection with Discord desktop app."""
        with self._lock:
            if self.connected:
                return True
            ipc_path = self._find_ipc_path()
            if not ipc_path:
                return False
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                sock.connect(ipc_path)
                self.socket = sock

                # Send Handshake (Opcode 0)
                handshake = json.dumps({"v": 1, "client_id": self.client_id})
                header = struct.pack("<II", 0, len(handshake))
                sock.sendall(header + handshake.encode("utf-8"))

                # Read Handshake Response
                resp_header = sock.recv(8)
                if len(resp_header) == 8:
                    _, resp_len = struct.unpack("<II", resp_header)
                    _ = sock.recv(resp_len)
                    self.connected = True
                    return True
            except Exception:
                self.close()
        return False

    def update(self, title: str, channel: str = "", state: str = "playing",
               position: float = 0.0, duration: float | None = None,
               url: str | None = None) -> None:
        """Send Rich Presence activity update to Discord."""
        if not self.connected and not self.connect():
            return

        now = time.time()
        if now - self.last_update < 1.0:
            return

        activity: dict[str, Any] = {
            "details": title[:128] if title else "Skye Music Player",
            "state": f"by {channel[:128]}" if channel else "Listening to Skye Music",
            "assets": {
                "large_image": "skye_logo",
                "large_text": "Skye Music Player (skye / tune)",
            },
        }

        if state == "playing" and duration and duration > 0:
            start_t = int(now - max(0.0, position))
            end_t = int(start_t + duration)
            activity["timestamps"] = {"start": start_t, "end": end_t}

        payload = json.dumps({
            "cmd": "SET_ACTIVITY",
            "args": {
                "pid": os.getpid(),
                "activity": activity,
            },
            "nonce": str(now),
        })

        header = struct.pack("<II", 1, len(payload))
        with self._lock:
            try:
                if self.socket:
                    self.socket.sendall(header + payload.encode("utf-8"))
                    self._last_update = now
            except Exception:
                self.close()

    def close(self) -> None:
        """Close Discord IPC socket."""
        with self._lock:
            self.connected = False
            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
                self.socket = None
