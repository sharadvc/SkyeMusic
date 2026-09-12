"""HTTP remote control for tune (control from your phone / another device).

The daemon runs a tiny web server that serves the built React UI (`web/dist`)
and proxies every `tune` command over HTTP:
  GET /api/<verb>?arg=...&pin=...   -> JSON response (same as the control socket)
  GET /                             -> the built web app

If `remote_pin` is set in the config, every /api call must carry that PIN.

The UI is built separately: `cd web && npm install && npm run build`. Until
that's done, the daemon returns a hint page.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from .daemon import Daemon

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"

_EXTRA_TYPES = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".json": "application/json",
}


class Handler(BaseHTTPRequestHandler):
    daemon: Daemon = None  # set by start() before serving

    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, qs: str) -> bool:
        pin = Handler.daemon.cfg.get("remote_pin")
        if not pin:
            return True
        return parse_qs(qs).get("pin", [""])[0] == str(pin)

    def _serve(self, target: Path) -> None:
        try:
            body = target.read_bytes()
        except OSError:
            self._json({"ok": False, "error": "not found"}, 404)
            return
        ctype = _EXTRA_TYPES.get(target.suffix) or mimetypes.guess_type(str(target))[0] \
            or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _build_hint(self) -> None:
        body = (
            b"tune's phone remote needs its web build first.\n\n"
            b"  cd web\n  npm install\n  npm run build\n\n"
            b"Then reload this page."
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self._authorized(parsed.query):
                self._json({"ok": False, "error": "pin required"}, 401)
                return
            verb = parsed.path[len("/api/"):]
            if verb == "party/sync":
                client_id = self.client_address[0]
                from .party import party_engine
                party_engine.register_listener(client_id)
                info = party_engine.get_info(self.daemon)
                self._json({"ok": True, "data": info})
                return
            if verb == "party/info":
                from .party import party_engine
                info = party_engine.get_info(self.daemon)
                self._json({"ok": True, "data": info})
                return

            arg = parse_qs(parsed.query).get("arg", [""])[0]
            try:
                resp = self.daemon.dispatch(json.dumps({"verb": verb, "arg": arg}))
            except Exception as e:
                resp = {"ok": False, "error": str(e)}
            self._json(resp)
            return
        if not (DIST / "index.html").exists():
            self._build_hint()
            return
        rel = parsed.path.lstrip("/")
        target = (DIST / rel).resolve()
        if target.is_file() and str(target).startswith(str(DIST.resolve())):
            self._serve(target)
        else:
            self._serve(DIST / "index.html")

    def log_message(self, *args) -> None:  # keep the daemon log clean
        pass


def start(daemon, port: int):
    server = ThreadingHTTPServer(("", port), Handler)
    Handler.daemon = daemon
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
