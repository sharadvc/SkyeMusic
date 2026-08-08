"""MpvHandle: spawn and control one mpv instance over its JSON IPC socket.

The daemon is the only owner of this connection, so command replies are
matched to requests by id and events are dispatched to a callback. All writes
go through one thread; replies are read by a dedicated reader thread.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from concurrent.futures import Future

from .queue import MPV_SOCK


class MpvError(Exception):
    pass


class MpvHandle:
    """Owns an mpv subprocess and a single JSON-IPC connection to it.

    `on_event(msg)` is called from the reader thread for every mpv event and
    must never block. `on_disconnect()` fires when the connection dies.
    """

    def __init__(self, args, sock_path=MPV_SOCK, on_event=None, on_disconnect=None):
        self.args = args
        self.sock_path = sock_path
        self.on_event = on_event or (lambda msg: None)
        self.on_disconnect = on_disconnect or (lambda: None)
        self.proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._next_id = 0
        self._pending: dict[int, Future] = {}
        self._pending_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._reader: threading.Thread | None = None

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> "MpvHandle":
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        self.proc = subprocess.Popen(
            self.args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_for_socket()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(str(self.sock_path))
        self._sock.setblocking(False)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        return self

    def _wait_for_socket(self, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(self.sock_path):
                return
            if self.proc.poll() is not None:
                raise MpvError(f"mpv exited early (rc={self.proc.returncode})")
            time.sleep(0.05)
        raise MpvError("mpv IPC socket never appeared")

    def wait(self) -> int:
        """Block until mpv exits; returns its exit code."""
        if self.proc is None:
            return -1
        return self.proc.wait()

    def close(self) -> None:
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
        try:
            if self.proc:
                self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    # --- reader thread ------------------------------------------------------

    def _read_loop(self) -> None:
        buf = b""
        try:
            while True:
                try:
                    chunk = self._sock.recv(4096)
                except (BlockingIOError, InterruptedError):
                    time.sleep(0.02)
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._dispatch_line(line.decode("utf-8", "replace"))
        finally:
            self.on_disconnect()

    def _dispatch_line(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict):
            return
        if "event" in msg:
            self.on_event(msg)
        elif "request_id" in msg:
            rid = msg.get("request_id")
            with self._pending_lock:
                fut = self._pending.pop(rid, None)
            if fut is not None:
                fut.set_result(msg)

    # --- commands -----------------------------------------------------------

    def _request(self, req: dict) -> Future:
        with self._pending_lock:
            self._next_id += 1
            req["request_id"] = self._next_id
            fut = Future()
            self._pending[self._next_id] = fut
        payload = (json.dumps(req) + "\n").encode("utf-8")
        with self._send_lock:
            if self._sock is None:
                raise MpvError("mpv is not connected")
            try:
                self._sock.sendall(payload)
            except OSError as e:
                raise MpvError(f"mpv connection lost: {e}")
        return fut

    def command(self, *args, timeout: float = 15.0):
        fut = self._request({"command": list(args)})
        try:
            reply = fut.result(timeout=timeout)
        except TimeoutError:
            raise MpvError(f"mpv timed out on {args!r}")
        err = reply.get("error")
        if err not in (None, "success"):
            raise MpvError(f"mpv {args!r}: {err}")
        return reply.get("data")

    def get_property(self, name: str, timeout: float = 5.0):
        try:
            return self.command("get_property", name, timeout=timeout)
        except MpvError:
            return None  # unavailable while idle, etc.

    def set_property(self, name: str, value, timeout: float = 5.0) -> None:
        self.command("set_property", name, value, timeout=timeout)
