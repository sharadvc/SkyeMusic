"""Global Public Tunnel Engine for tune (concurrent multi-provider race & validation)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from typing import Optional

_tunnel_process: Optional[subprocess.Popen] = None
_tunnel_url: Optional[str] = None
_lock = threading.Lock()


def get_active_tunnel_url() -> Optional[str]:
    with _lock:
        return _tunnel_url


def stop_tunnel() -> None:
    global _tunnel_process, _tunnel_url
    with _lock:
        if _tunnel_process:
            try:
                _tunnel_process.terminate()
                _tunnel_process.wait(timeout=2)
            except Exception:
                try:
                    _tunnel_process.kill()
                except Exception:
                    pass
            _tunnel_process = None
        _tunnel_url = None


def _verify_url(url: str, check_timeout: float = 2.5) -> bool:
    """Verify that the generated public tunnel URL resolves via DNS and returns HTTP 200/401."""
    import urllib.request
    start_t = time.time()
    while time.time() - start_t < check_timeout:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"}
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status in (200, 401, 301, 302):
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def _try_cloudflared(port: int, stop_ev: threading.Event, winner_lock: threading.Lock, winners: list) -> None:
    local_cf = os.path.expanduser("~/.config/tune/bin/cloudflared")
    cloudflared = shutil.which("cloudflared") or (
        "/opt/homebrew/bin/cloudflared" if os.path.exists("/opt/homebrew/bin/cloudflared") else local_cf
    )
    if not os.path.exists(cloudflared):
        return
    proc = None
    try:
        proc = subprocess.Popen(
            [cloudflared, "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        start_t = time.time()
        found_url = None
        while time.time() - start_t < 4.0 and not stop_ev.is_set():
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                time.sleep(0.02)
                continue
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if match:
                found_url = match.group(0)
                break

        if found_url and not stop_ev.is_set() and _verify_url(found_url, check_timeout=2.0):
            with winner_lock:
                if not stop_ev.is_set():
                    stop_ev.set()
                    winners.append(("cloudflared", proc, found_url))
                    return
        if proc:
            proc.terminate()
    except Exception:
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass


def _try_localhost_run(port: int, stop_ev: threading.Event, winner_lock: threading.Lock, winners: list) -> None:
    ssh = shutil.which("ssh")
    if not ssh:
        return
    proc = None
    try:
        proc = subprocess.Popen(
            [
                ssh,
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ServerAliveInterval=15",
                "-R",
                f"80:127.0.0.1:{port}",
                "nokey@localhost.run",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        start_t = time.time()
        found_url = None
        while time.time() - start_t < 4.0 and not stop_ev.is_set():
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                time.sleep(0.02)
                continue
            match = re.search(r"https://[a-zA-Z0-9-]+\.lhr\.life", line)
            if match:
                found_url = match.group(0)
                break

        if found_url and not stop_ev.is_set() and _verify_url(found_url, check_timeout=2.0):
            with winner_lock:
                if not stop_ev.is_set():
                    stop_ev.set()
                    winners.append(("localhost.run", proc, found_url))
                    return
        if proc:
            proc.terminate()
    except Exception:
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass


def _try_serveo(port: int, stop_ev: threading.Event, winner_lock: threading.Lock, winners: list) -> None:
    ssh = shutil.which("ssh")
    if not ssh:
        return
    proc = None
    try:
        proc = subprocess.Popen(
            [
                ssh,
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ServerAliveInterval=15",
                "-R",
                f"80:127.0.0.1:{port}",
                "serveo.net",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        start_t = time.time()
        found_url = None
        while time.time() - start_t < 4.0 and not stop_ev.is_set():
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                time.sleep(0.02)
                continue
            match = re.search(r"https://(?!console|www)[a-zA-Z0-9-]+\.serveo\.net", line)
            if match:
                found_url = match.group(0)
                break

        if found_url and not stop_ev.is_set() and _verify_url(found_url, check_timeout=2.0):
            with winner_lock:
                if not stop_ev.is_set():
                    stop_ev.set()
                    winners.append(("serveo.net", proc, found_url))
                    return
        if proc:
            proc.terminate()
    except Exception:
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass


def start_tunnel(port: int = 8765, timeout: float = 7.0) -> Optional[str]:
    """Start a zero-config public HTTPS tunnel using concurrent multi-provider race (localhost.run / cloudflared / serveo)."""
    global _tunnel_process, _tunnel_url

    with _lock:
        if _tunnel_url and _verify_url(_tunnel_url, check_timeout=1.0):
            return _tunnel_url

    stop_ev = threading.Event()
    winner_lock = threading.Lock()
    winners: list = []

    t_cf = threading.Thread(target=_try_cloudflared, args=(port, stop_ev, winner_lock, winners), daemon=True)
    t_lhr = threading.Thread(target=_try_localhost_run, args=(port, stop_ev, winner_lock, winners), daemon=True)
    t_srv = threading.Thread(target=_try_serveo, args=(port, stop_ev, winner_lock, winners), daemon=True)

    t_cf.start()
    t_lhr.start()
    t_srv.start()

    start_t = time.time()
    while time.time() - start_t < timeout:
        with winner_lock:
            if winners:
                name, proc, url = winners[0]
                with _lock:
                    _tunnel_process = proc
                    _tunnel_url = url
                return url
        time.sleep(0.05)

    stop_ev.set()
    return None
