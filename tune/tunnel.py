"""Global Public Tunnel Engine for tune (cloudflared, localtunnel, serveo fallbacks)."""

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


def start_tunnel(port: int = 8765, timeout: float = 10.0) -> Optional[str]:
    """Start a zero-config public HTTPS tunnel (cloudflared -> localtunnel -> serveo)."""
    global _tunnel_process, _tunnel_url

    with _lock:
        if _tunnel_url:
            return _tunnel_url

    # Strategy 1: cloudflared (Cloudflare Quick Tunnels)
    cloudflared = shutil.which("cloudflared") or "/opt/homebrew/bin/cloudflared"
    if os.path.exists(cloudflared):
        try:
            proc = subprocess.Popen(
                [cloudflared, "tunnel", "--url", f"http://localhost:{port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            start_t = time.time()
            found_url = None

            while time.time() - start_t < timeout:
                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    time.sleep(0.1)
                    continue
                match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if match:
                    found_url = match.group(0)
                    break

            if found_url:
                with _lock:
                    _tunnel_process = proc
                    _tunnel_url = found_url
                return found_url
            else:
                proc.terminate()
        except Exception:
            pass

    # Strategy 2: ssh serveo.net fallback
    ssh = shutil.which("ssh")
    if ssh:
        try:
            proc = subprocess.Popen(
                [ssh, "-o", "StrictHostKeyChecking=no", "-R", f"80:localhost:{port}", "serveo.net"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            start_t = time.time()
            found_url = None

            while time.time() - start_t < timeout:
                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    time.sleep(0.1)
                    continue
                match = re.search(r"https://[a-zA-Z0-9-]+\.serveo\.net", line)
                if match:
                    found_url = match.group(0)
                    break

            if found_url:
                with _lock:
                    _tunnel_process = proc
                    _tunnel_url = found_url
                return found_url
            else:
                proc.terminate()
        except Exception:
            pass

    return None
