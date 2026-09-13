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
#art{width:78px;height:78px;object-fit:cover;border-radius:18px;flex:none;background:#f0e9f2;display:none}
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
#searchbox{display:flex;gap:8px;margin-bottom:12px}
#qq{border:0;background:var(--card);box-shadow:var(--shadow);border-radius:18px;padding:14px 18px;
  font-size:15px;flex:1;color:var(--ink);outline:none}
#sbtn{border:0;background:linear-gradient(135deg,var(--lav),var(--pink));color:#fff;border-radius:18px;
  padding:0 20px;font-weight:700;font-size:14px;cursor:pointer;box-shadow:var(--shadow)}
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
  <div class=ic style="margin-top:10px">
    <button id=spkbtn onclick="toggleSpeaker()">📻 Phone Speaker OFF</button>
  </div>
  <audio id=spkaudio style="display:none" playsinline></audio>
</div>
<h3>Queue</h3><div id=q></div>
<h3>Search</h3>
<div id=searchbox>
  <input id=qq placeholder='search song or artist…' onkeydown="if(event.key==='Enter')search()">
  <button id=sbtn onclick="search()">Search</button>
</div>
<div id=r></div>
</div>
<div id=pinoverlay><div id=pinbox><b>🔐 PIN required</b><br><br>
<input id=p pinmode autocomplete=off placeholder='enter PIN' onkeydown="if(event.key==='Enter')savePin()"><br><br>
<button onclick="savePin()">Unlock</button></div></div>
<script>
let pin=localStorage.getItem('tune_pin')||'';
let spkActive=false;
let spkTrackUrl='';
let refreshing=false;

function toggleSpeaker(){
  spkActive=!spkActive;
  const btn=document.getElementById('spkbtn');
  const audio=document.getElementById('spkaudio');
  if(spkActive){
    if(btn){
      btn.textContent='🔊 Phone Speaker ON';
      btn.style.background='linear-gradient(135deg,var(--lav),var(--pink))';
      btn.style.color='#fff';
    }
    refresh(true);
  }else{
    if(btn){
      btn.textContent='📻 Phone Speaker OFF';
      btn.style.background='var(--card)';
      btn.style.color='var(--ink)';
    }
    if(audio){
      audio.pause();
      audio.src='';
    }
    spkTrackUrl='';
  }
}

function qs(v,a){
  let u='/api/'+v+'?pin='+encodeURIComponent(pin);
  if(a!==undefined&&a!==null&&a!=='') u+='&arg='+encodeURIComponent(a);
  return u;
}

async function c(v,a){
  try{
    const r=await fetch(qs(v,a));
    if(r.status===401){askPin();return;}
    await refresh(true);
  }catch(e){}
}

function vid(u){const m=(u||'').match(/[?&]v=([\\w-]{11})/);return m?m[1]:''}
const fmt=s=>{s=Math.max(0,Math.floor(s||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};

async function refresh(force){
  if(refreshing && !force) return;
  refreshing = true;
  try{
    const r=await fetch(qs('status'));
    if(r.status===401){askPin();return;}
    if(!r.ok) return;
    const j=await r.json();
    const d=j.data||{};

    const tiEl = document.getElementById('ti');
    if(tiEl) tiEl.textContent = d.title || 'nothing playing';

    const stEl = document.getElementById('st');
    if(stEl) {
      const parts = [];
      if(d.state) parts.push(d.state);
      if(d.channel) parts.push(d.channel);
      if(d.speed && d.speed !== 1) parts.push(d.speed + '×');
      parts.push((d.queue_len || 0) + ' queued');
      stEl.textContent = parts.join(' · ');
    }

    const artEl = document.getElementById('art');
    if(artEl) {
      const v = vid(d.url);
      if(v){
        artEl.src = 'https://i.ytimg.com/vi/' + v + '/hqdefault.jpg';
        artEl.style.display = 'block';
      }else{
        artEl.style.display = 'none';
      }
    }

    const ppEl = document.getElementById('pp');
    if(ppEl) ppEl.textContent = (d.state==='playing'||d.state==='loading') ? '⏸' : '▶';

    const fbEl = document.getElementById('favbtn');
    if(fbEl){
      fbEl.textContent = d.fav ? '♥' : '♡';
      fbEl.className = d.fav ? 'on' : '';
    }

    const dur = d.duration || 0;
    const seek = document.getElementById('seek');
    if(seek){
      seek.max = Math.max(1, Math.round(dur));
      if(!seek.__drag){
        seek.value = Math.min((d.position || 0), seek.max);
        const tcur = document.getElementById('tcur');
        if(tcur) tcur.textContent = fmt(seek.value);
      }
    }
    const tdur = document.getElementById('tdur');
    if(tdur) tdur.textContent = fmt(dur);

    if(spkActive){
      const audio = document.getElementById('spkaudio');
      if(audio){
        const serverTs = d.ts || (Date.now() / 1000.0);
        const elapsedSinceServer = (Date.now() / 1000.0) - serverTs;
        const targetPos = (d.position || 0) + (d.state === 'playing' ? Math.max(0, elapsedSinceServer) : 0);
        const targetUrl = d.direct_url || (d.url ? '/api/stream_proxy?pin=' + encodeURIComponent(pin) + '&url=' + encodeURIComponent(d.url) : '');

        if(targetUrl && (spkTrackUrl !== d.url || force)){
          spkTrackUrl = d.url;
          audio.src = targetUrl;
          audio.preload = 'auto';
          if(targetPos > 0) audio.currentTime = targetPos;
          if(d.state === 'playing') audio.play().catch(e => {});
        }

        if(d.state === 'playing'){
          if(audio.paused && audio.src){
            audio.play().catch(e => {});
          }
          const diff = targetPos - audio.currentTime;
          if(Math.abs(diff) > 0.08){
            audio.currentTime = targetPos;
            audio.playbackRate = 1.0;
          }else if(Math.abs(diff) > 0.015){
            audio.playbackRate = diff > 0 ? 1.03 : 0.97;
          }else{
            audio.playbackRate = 1.0;
          }
        }else{
          if(!audio.paused) audio.pause();
        }
      }
    }

    const qEl = document.getElementById('q');
    if(qEl){
      const rows = (d.queue || []).map((t, i) => {
        const cur = (i === d.current_index) ? '<span class=cur>▶</span> ' : '';
        const label = cur + '<span class=t>' + (t.title || 'Track') + '</span>';
        const up = (i > 0) ? `<button onclick="c('move','${i} ${i-1}')">▲</button>` : '';
        const dn = (i < d.queue_len - 1) ? `<button onclick="c('move','${i+2} ${i+1}')">▼</button>` : '';
        return `<div class=qrow>${label}<span style="flex:none">${up}${dn}<button onclick="c('remove','${i+1}')">✕</button></span></div>`;
      }).join('');
      qEl.innerHTML = rows || '<div class=qrow style="color:var(--muted)">queue is empty</div>';
    }
  }catch(e){
  }finally{
    refreshing = false;
  }
}

(function(){
  const s=document.getElementById('seek');
  if(s){
    s.addEventListener('input',()=>{s.__drag=true;const tc=document.getElementById('tcur');if(tc)tc.textContent=fmt(s.value);});
    s.addEventListener('change',()=>{s.__drag=false;c('seek',Math.round(s.value));});
  }
})();

async function search(){
  const qEl = document.getElementById('qq');
  const q = qEl ? qEl.value.trim() : '';
  if(!q) return;
  const resEl = document.getElementById('r');
  if(resEl) resEl.innerHTML = '<div style="padding:12px;color:var(--muted)">Searching YouTube…</div>';
  try{
    const r=await fetch(qs('search', q));
    if(r.status===401){askPin();return;}
    const j=await r.json();
    const rs=(j.data && j.data.results) || [];
    if(!rs.length){
      if(resEl) resEl.innerHTML = '<div style="padding:12px;color:var(--muted)">No results found</div>';
      return;
    }
    const html = rs.map((t, i) => {
      const urlEsc = (t.url || '').replace(/'/g, "\\'");
      const ch = t.channel ? `<div style="font-size:12px;color:var(--muted);margin-top:2px">${t.channel}</div>` : '';
      return `<div style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;margin:8px 0;background:var(--card);border-radius:16px;box-shadow:var(--shadow)">
        <div style="flex:1;overflow:hidden;margin-right:12px" onclick="playUrl('${urlEsc}')">
          <div style="font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${t.title}</div>
          ${ch}
        </div>
        <button onclick="playUrl('${urlEsc}')" style="border:0;background:linear-gradient(135deg,var(--lav),var(--pink));color:#fff;border-radius:12px;padding:7px 14px;font-weight:700;font-size:13px;cursor:pointer;flex:none">▶ Play</button>
      </div>`;
    }).join('');
    if(resEl) resEl.innerHTML = html;
  }catch(e){
    if(resEl) resEl.innerHTML = '<div style="padding:12px;color:#e55">Search error</div>';
  }
}

function playUrl(url){ if(url) c('play', url); }

function askPin(){
  const p=document.getElementById('pinoverlay');if(p)p.style.display='flex';
  const pi=document.getElementById('p');if(pi)pi.focus();
}

function savePin(){
  const pi=document.getElementById('p');
  if(pi) pin=pi.value;
  localStorage.setItem('tune_pin',pin);
  const p=document.getElementById('pinoverlay');if(p)p.style.display='none';
  refresh(true);
}

refresh();
setInterval(() => refresh(false), 250);

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
        if parsed.path == "/api/stream_proxy":
            if not self._authorized(parsed.query):
                self._json({"ok": False, "error": "pin required"}, 401)
                return
            watch_url = parse_qs(parsed.query).get("url", [""])[0]
            if not watch_url:
                self._json({"ok": False, "error": "missing url"}, 400)
                return
            try:
                from .resolver import get_direct_url
                direct_url = get_direct_url(watch_url)
                self.send_response(302)
                self.send_header("Location", direct_url)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
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


