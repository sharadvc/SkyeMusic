"""HTTP remote control for tune (control from your phone / another device).

The daemon runs a tiny web server that proxies every `tune` command over HTTP:
  GET /api/<verb>?arg=...   -> JSON response (same as the control socket)
  GET /                     -> a simple mobile-friendly control page
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = """<!doctype html><html><head><meta charset=utf8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>tune</title>
<style>
body{font-family:sans-serif;background:#111;color:#eee;max-width:620px;margin:auto;padding:20px}
h1{margin:.2em 0}
#now{font-size:20px;padding:10px;background:#1d1d1d;border-radius:8px;margin:10px 0}
button{font-size:17px;padding:12px 14px;margin:4px;border-radius:8px;border:0;background:#333;color:#eee;cursor:pointer}
input{font-size:17px;padding:12px;width:100%;box-sizing:border-box;border-radius:8px;border:1px solid #444;background:#1d1d1d;color:#eee}
#r div{margin:4px 0;padding:8px;background:#1d1d1d;border-radius:6px}
#r a{color:#7cf;text-decoration:none}
</style></head><body>
<h1>🎵 tune</h1><div id=now>—</div>
<div>
<button onclick="c('toggle')">⏯ Pause/Play</button>
<button onclick="c('next')">⏭ Next</button>
<button onclick="c('prev')">⏮ Prev</button>
<button onclick="c('volume','+5')">🔊+</button>
<button onclick="c('volume','-5')">🔉−</button>
<button onclick="c('fav')">♥</button>
</div>
<h3>Search</h3><input id=q placeholder='song name' onkeydown="if(event.key==='Enter')search()">
<div id=r></div>
<script>
async function c(v,a){const u='/api/'+v+(a?'?arg='+encodeURIComponent(a):'');await fetch(u);refresh()}
async function refresh(){try{const j=await(await fetch('/api/status')).json();const d=j.data||{};
 document.getElementById('now').textContent=(d.title||'nothing playing')+' — '+(d.state||'');}catch(e){}}
async function search(){const q=document.getElementById('q').value;
 const j=await(await fetch('/api/search?arg='+encodeURIComponent(q))).json();
 const rs=(j.data&&j.data.results)||[];window._tune_results=rs;
 document.getElementById('r').innerHTML=rs.map((t,i)=>
 `<div><a href="javascript:c('play',window._tune_results[${i}].url)">▶ ${t.title}</a></div>`).join('')||'no results';}
refresh();setInterval(refresh,2000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    daemon = None

    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path.startswith("/api/"):
            verb = parsed.path[len("/api/"):]
            arg = parse_qs(parsed.query).get("arg", [""])[0]
            try:
                resp = self.daemon.dispatch(json.dumps({"verb": verb, "arg": arg}))
            except Exception as e:
                resp = {"ok": False, "error": str(e)}
            self._json(resp)
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def log_message(self, *args) -> None:  # keep the daemon log clean
        pass


def start(daemon, port: int):
    server = ThreadingHTTPServer(("", port), Handler)
    Handler.daemon = daemon
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
