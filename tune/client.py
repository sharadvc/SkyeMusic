"""Control-socket client: talk to the tune daemon, auto-starting it on demand."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .queue import CTRL_SOCK, LOCK_FILE, LOG_FILE

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TuneError(Exception):
    pass


def _connect(verb: str, arg: str) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(5.0)
        s.connect(str(CTRL_SOCK))
        s.sendall((json.dumps({"verb": verb, "arg": arg}) + "\n").encode("utf-8"))
        s.settimeout(25.0)  # a reply can take a few seconds (e.g. resolving a song)
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.split(b"\n", 1)[0])
    finally:
        s.close()


def _daemon_running() -> bool:
    """True if a daemon holds the single-instance lock."""
    try:
        with open(LOCK_FILE, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False  # we acquired it, so no daemon holds it
    except OSError:
        return True


def start_daemon() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_out = open(LOG_FILE, "a")
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    subprocess.Popen(
        [sys.executable, "-m", "tune", "daemon"],
        stdin=subprocess.DEVNULL,
        stdout=log_out,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )


def send_cmd(verb: str, arg: str = "") -> dict:
    """Send one command and return the daemon's JSON response.

    If no daemon is running, start one (detached) and wait for it. Never
    removes a live daemon's socket: we only clean up when we can prove (via
    the single-instance lock) that no daemon is holding it.
    """
    last = None
    started = False
    for _ in range(40):  # ~8s budget for daemon startup + retries
        try:
            return _connect(verb, arg)
        except (OSError, ValueError) as e:
            last = e
            if not started and not _daemon_running():
                try:
                    if os.path.exists(CTRL_SOCK):
                        os.unlink(CTRL_SOCK)
                except OSError:
                    pass
                start_daemon()
                started = True
            time.sleep(0.2)
    raise TuneError(f"could not reach tune daemon: {last}")
