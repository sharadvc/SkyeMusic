"""HTTP remote control for tune (control from your phone / another device).

The daemon runs a tiny web server that proxies every `tune` command over HTTP:
  GET /api/<verb>?arg=...&pin=...   -> JSON response (same as the control socket)
  GET /                             -> a mobile-friendly control page

If `remote_pin` is set in the config, every /api call must carry that PIN.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from .daemon import Daemon

PAGE = """<!doctype html><html><head><meta charset=utf8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>tune</title>
<style>
body{font-family:sans-serif;background:#111;color:#eee;max-width:620px;margin:auto;padding:20px}
h1{margin:.2em 0}
#now{display:flex;gap:12px;align-items:center;background:#1d1d1d;border-radius:8px;padding:10px;margin:10px 0}
#art{width:72px;height:54px;object-fit:cover;border-radius:4px;background:#333;flex:none}
#ti{font-size:16px;margin-bottom:2px}
button{font-size:16px;padding:12px 14px;margin:4px;border-radius:8px;border:0;background:#333;color:#eee;cursor:pointer}
input{font-size:16px;padding:12px;width:100%;box-sizing:border-box;border-radius:8px;border:1px solid #444;background:#1d1d1d;color:#eee}
.qrow{display:flex;gap:6px;align-items:center;margin:4px 0;padding:6px 8px;background:#1d1d1d;border-radius:6px}
.qrow .t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.qrow .cur{color:#7cf;font-weight:bold}
.qrow button{font-size:13px;padding:4px 8px;margin:0 2px}
#r div{margin:4px 0;padding:8px;background:#1d1d1d;border-radius:6px}
#r a{color:#7cf;text-decoration:none}
#pinoverlay{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;align-items:center;justify-content:center}
#pinbox{background:#1d1d1d;padding:24px;border-radius:10px;max-width:300px;width:90%}
</style></head><body>
<h1>🎵 tune</h1>
<div id=now><img id=art><div><div id=ti>—</div><div id=st></div></div></div>
<div>
<button onclick="c('toggle')">⏯ Pause/Play</button>
<button onclick="c('next')">⏭ Next</button>
<button onclick="c('prev')">⏮ Prev</button>
<button onclick="c('volume','+5')">🔊+</button>
<button onclick="c('volume','-5')">🔉−</button>
<button onclick="c('fav')">♥</button>
</div>
<h3>Queue</h3><div id=q></div>
<h3>Search</h3><input id=qq placeholder='song name' onkeydown="if(event.key==='Enter')search()">
<div id=r></div>
<div id=pinoverlay><div id=pinbox><b>PIN required</b><br><br>
<input id=p pinmode autocomplete=off placeholder='enter PIN' onkeydown="if(event.key==='Enter')savePin()"><br><br>
<button onclick="savePin()">Unlock</button></div></div>
<script>
let pin=localStorage.getItem('tune_pin')||'';
function qs(v,a){let u='/api/'+v+'?pin='+encodeURIComponent(pin);if(a)u+='&arg='+encodeURIComponent(a);return u}
async function c(v,a){try{const r=await fetch(qs(v,a));if(r.status===401){askPin();return}await refresh()}catch(e){}}
function vid(u){const m=(u||'').match(/[?&]v=([\\w-]{11})/);return m?m[1]:''}
async function refresh(){try{
 const r=await fetch(qs('status'));if(r.status===401){askPin();return}
 const j=await r.json();const d=j.data||{};
 document.getElementById('ti').textContent=d.title||'nothing playing';
 document.getElementById('st').textContent=(d.state||'')+' · '+(d.speed&&d.speed!=1?d.speed+'× ':'')+(d.queue_len||0)+' queued';
 const v=vid(d.url);document.getElementById('art').src=v?('https://i.ytimg.com/vi/'+v+'/hqdefault.jpg'):'';
 const rows=(d.queue||[]).map((t,i)=>{
   const cur=(i===d.current_index)?'<span class=cur>▶</span> ':'';
   const label=cur+'<span class=t>'+t.title+'</span>';
   const up=(i>0)?`<button onclick="c('move','${i} ${i-1}')">▲</button>`:'';
   const dn=(i<d.queue_len-1)?`<button onclick="c('move','${i+2} ${i+1}')">▼</button>`:'';
   return `<div class=qrow>${label}<span style="flex:none">${up}${dn}<button onclick="c('remove','${i+1}')">✕</button></span></div>`;
 }).join('');
 document.getElementById('q').innerHTML=rows||'<div class=qrow>queue is empty</div>';
}catch(e){}}
async function search(){const q=document.getElementById('qq').value;
 const r=await fetch(qs('search',q));if(r.status===401){askPin();return}
 const j=await r.json();const rs=(j.data&&j.data.results)||[];window._tr=rs;
 document.getElementById('r').innerHTML=rs.map((t,i)=>
 `<div><a href="javascript:c('play',window._tr[${i}].url)">▶ ${t.title}</a></div>`).join('')||'no results';}
function askPin(){document.getElementById('pinoverlay').style.display='flex';document.getElementById('p').focus()}
function savePin(){pin=document.getElementById('p').value;localStorage.setItem('tune_pin',pin);
 document.getElementById('pinoverlay').style.display='none';refresh()}
refresh();setInterval(refresh,2000);
</script></body></html>"""


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
            if not self._authorized(parsed.query):
                self._json({"ok": False, "error": "pin required"}, 401)
                return
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
