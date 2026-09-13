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
<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>
<title>tune</title>
<style>
:root{
  --bg:#faf6f0; --card:#ffffff; --ink:#4a4450; --muted:#a79eb3;
  --lav:#b9a6f2; --pink:#f6b9cd; --peach:#ffd9b8; --mint:#b7e8d4;
  --sky:#aecbfa; --shadow:0 8px 24px rgba(140,120,170,.12);
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  color:var(--ink);min-height:100vh;padding:28px 18px 44px;
  background:radial-gradient(120% 90% at 85% -10%, #fdf0f2 0%, transparent 55%),
             radial-gradient(120% 90% at -15% 0%, #eef2fd 0%, transparent 55%),
             var(--bg);}
.wrap{max-width:560px;margin:0 auto}
h1{margin:0;font-size:30px;font-weight:800;letter-spacing:-.03em;
  background:linear-gradient(92deg,var(--lav),var(--pink) 55%,var(--peach));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--muted);font-size:13px;margin:2px 0 18px;font-weight:500}
.card{background:var(--card);border-radius:24px;box-shadow:var(--shadow);padding:18px;margin-bottom:16px}
#now{display:flex;gap:14px;align-items:center}
#art{width:78px;height:78px;object-fit:cover;border-radius:18px;flex:none;background:#f0e9e2}
#ti{font-size:16px;font-weight:700;line-height:1.25}
#st{font-size:13px;color:var(--muted);margin-top:3px}
#seekbar{display:flex;gap:10px;align-items:center;margin-top:16px}
#seekbar span{font-size:12px;color:var(--muted);min-width:34px;font-variant-numeric:tabular-nums}
#seekbar span:last-child{text-align:right}
#seek{flex:1;height:8px;border-radius:99px;appearance:none;-webkit-appearance:none;
  background:linear-gradient(90deg,var(--lav),var(--pink));outline:none;cursor:pointer;margin:0}
#seek::-webkit-slider-thumb{-webkit-appearance:none;width:22px;height:22px;border-radius:50%;
  background:#fff;border:3px solid var(--lav);box-shadow:0 2px 8px rgba(140,120,170,.3)}
#seek::-moz-range-thumb{width:16px;height:16px;border-radius:50%;background:#fff;
  border:3px solid var(--lav)}
.transport{display:flex;justify-content:center;gap:16px;margin-top:18px}
.transport button{width:58px;height:58px;border-radius:50%;border:0;font-size:22px;cursor:pointer;
  background:var(--card);box-shadow:var(--shadow);color:var(--ink);
  transition:transform .12s ease}
.transport button:active{transform:scale(.9)}
#pp{background:linear-gradient(135deg,var(--lav),var(--sky));color:#fff;box-shadow:0 8px 20px rgba(150,130,240,.35)}
.ic{display:flex;justify-content:center;gap:12px;margin-top:14px}
.ic button{border:0;border-radius:16px;padding:10px 16px;font-size:15px;cursor:pointer;
  background:var(--card);box-shadow:var(--shadow);color:var(--ink);transition:transform .12s ease}
.ic button:active{transform:scale(.94)}
#favbtn.on{color:#ff8fb2}
h3{margin:22px 0 10px;font-size:12px;font-weight:700;letter-spacing:.08em;color:var(--muted);
  text-transform:uppercase}
.qrow{display:flex;gap:8px;align-items:center;background:var(--card);border-radius:16px;
  box-shadow:var(--shadow);padding:11px 13px;margin:8px 0}
.qrow .t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px}
.qrow .cur{color:var(--lav);font-weight:700}
.qrow button{font-size:13px;padding:6px 9px;margin:0 2px;border:0;border-radius:10px;cursor:pointer;
  background:#f3edf9;color:var(--ink)}
#qq{border:0;background:var(--card);box-shadow:var(--shadow);border-radius:18px;padding:15px 18px;
  font-size:15px;width:100%;color:var(--ink);outline:none}
#r div{margin:8px 0;padding:13px 15px;background:var(--card);border-radius:16px;box-shadow:var(--shadow)}
#r a{color:#8b7bd6;text-decoration:none;font-weight:600}
#pinoverlay{position:fixed;inset:0;background:rgba(74,68,80,.35);backdrop-filter:blur(5px);
  display:none;align-items:center;justify-content:center;padding:24px}
#pinbox{background:#fff;border-radius:24px;box-shadow:0 14px 50px rgba(74,68,80,.3);
  padding:26px;max-width:320px;width:100%}
#pinbox b{font-size:17px}
#pinbox input{width:100%;margin-top:14px;border:2px solid #efe7f7;border-radius:14px;padding:13px 15px;
  font-size:15px;color:var(--ink);outline:none}
#pinbox input:focus{border-color:var(--lav)}
#pinbox button{border:0;width:100%;margin-top:14px;border-radius:14px;padding:13px;font-size:15px;
  font-weight:700;cursor:pointer;color:#fff;
  background:linear-gradient(135deg,var(--lav),var(--pink))}
</style></head><body>
<div class=wrap>
<h1>tune</h1>
<div class=sub>now playing · queue · search</div>
<div class=card>
  <div id=now><img id=art><div><div id=ti>—</div><div id=st></div></div></div>
  <div id=seekbar>
    <span id=tcur>0:00</span>
    <input id=seek type=range min=0 max=0 value=0>
    <span id=tdur>0:00</span>
  </div>
  <div class=transport>
    <button onclick="c('prev')">⏮</button>
    <button id=pp onclick="c('toggle')">⏸</button>
    <button onclick="c('next')">⏭</button>
  </div>
  <div class=ic>
    <button onclick="c('volume','-5')">🔉−</button>
    <button onclick="c('volume','+5')">🔊+</button>
    <button id=favbtn onclick="c('fav')">♡</button>
  </div>
</div>
<h3>Queue</h3><div id=q></div>
<h3>Search</h3><input id=qq placeholder='type a song name' onkeydown="if(event.key==='Enter')search()">
<div id=r></div>
</div>
<div id=pinoverlay><div id=pinbox><b>🔐 PIN required</b><br><br>
<input id=p pinmode autocomplete=off placeholder='enter PIN' onkeydown="if(event.key==='Enter')savePin()"><br><br>
<button onclick="savePin()">Unlock</button></div></div>
<script>
let pin=localStorage.getItem('tune_pin')||'';
function qs(v,a){let u='/api/'+v+'?pin='+encodeURIComponent(pin);if(a)u+='&arg='+encodeURIComponent(a);return u}
async function c(v,a){try{const r=await fetch(qs(v,a));if(r.status===401){askPin();return}await refresh()}catch(e){}}
function vid(u){const m=(u||'').match(/[?&]v=([\\w-]{11})/);return m?m[1]:''}
const fmt=s=>{s=Math.max(0,Math.floor(s||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
async function refresh(){try{
 const r=await fetch(qs('status'));if(r.status===401){askPin();return}
 const j=await r.json();const d=j.data||{};
 document.getElementById('ti').textContent=d.title||'nothing playing';
 document.getElementById('st').textContent=(d.state||'')+' · '+(d.speed&&d.speed!=1?d.speed+'× ':'')+(d.queue_len||0)+' queued';
 const v=vid(d.url);document.getElementById('art').src=v?('https://i.ytimg.com/vi/'+v+'/hqdefault.jpg'):'';
 document.getElementById('pp').textContent=(d.state==='playing'||d.state==='loading')?'⏸':'▶';
 const fb=document.getElementById('favbtn');fb.textContent=d.fav?'♥':'♡';fb.className=d.fav?'on':'';
 const dur=d.duration||0;
 const seek=document.getElementById('seek');
 seek.max=Math.max(1,Math.round(dur));
 document.getElementById('tdur').textContent=fmt(dur);
 if(!seek.__drag){ seek.value=Math.min((d.position||0),seek.max); document.getElementById('tcur').textContent=fmt(seek.value); }
 const rows=(d.queue||[]).map((t,i)=>{
   const cur=(i===d.current_index)?'<span class=cur>▶</span> ':'';
   const label=cur+'<span class=t>'+t.title+'</span>';
   const up=(i>0)?`<button onclick="c('move','${i} ${i-1}')">▲</button>`:'';
   const dn=(i<d.queue_len-1)?`<button onclick="c('move','${i+2} ${i+1}')">▼</button>`:'';
   return `<div class=qrow>${label}<span style="flex:none">${up}${dn}<button onclick="c('remove','${i+1}')">✕</button></span></div>`;
 }).join('');
 document.getElementById('q').innerHTML=rows||'<div class=qrow>queue is empty</div>';
}catch(e){}}
(function(){const s=document.getElementById('seek');
 s.addEventListener('input',()=>{s.__drag=true;document.getElementById('tcur').textContent=fmt(s.value);});
 s.addEventListener('change',()=>{s.__drag=false;c('seek',Math.round(s.value));});
})();
async function search(){const q=document.getElementById('qq').value;
 const r=await fetch(qs('search',q));if(r.status===401){askPin();return}
 const j=await r.json();const rs=(j.data&&j.data.results)||[];window._tr=rs;
 document.getElementById('r').innerHTML=rs.map((t,i)=>
 `<div><a href="javascript:c('play',window._tr[${i}].url)">▶ ${t.title}</a></div>`).join('')||'<div>no results</div>';}
function askPin(){document.getElementById('pinoverlay').style.display='flex';document.getElementById('p').focus()}
function savePin(){pin=document.getElementById('p').value;localStorage.setItem('tune_pin',pin);
 document.getElementById('pinoverlay').style.display='none';refresh()}
refresh();setInterval(refresh,2000);
</script></body></html>"""


_EXTRA_TYPES = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".json": "application/json",
}


class RemoteServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    daemon: Daemon = None  # set by start() before serving

    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.end_headers()

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        if parsed.path.startswith("/api/"):
            if not self._authorized(parsed.query):
                self.send_response(401)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        self.send_response(404)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _authorized(self, qs: str) -> bool:
        pin = Handler.daemon.cfg.get("remote_pin")
        if not pin:
            return True
        params = parse_qs(qs)
        val = params.get("pin", [""])[0] or params.get("token", [""])[0]
        return val == str(pin)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
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
    server = RemoteServer(("0.0.0.0", port), Handler)
    Handler.daemon = daemon
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

