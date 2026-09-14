"""HTTP remote control for tune (control from your phone / another device).

The daemon runs a tiny web server that proxies every `tune` command over HTTP:
  GET /api/<verb>?arg=...&pin=...   -> JSON response (same as the control socket)
  GET /                             -> a mobile-friendly control page

If `remote_pin` is set in the config, every /api call must carry that PIN.
"""

from __future__ import annotations

import json
import queue
import time
import threading
import weakref
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from .daemon import Daemon

PAGE = r"""
<!doctype html><html><head><meta charset=utf8>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>skyemusic web</title>
<script src="https://unpkg.com/feather-icons"></script>
<style>
:root {
  --bg: #000000;
  --text: #ffffff;
  --subtext: #a0a0a0;
  --accent: #00e0ff;
  --highlight: #1a1a1a;
  --icon-size: 24px;
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body, html { margin: 0; padding: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; min-height: 100vh; overflow-x: hidden; }

/* Main Player View */
.player-view { display: flex; flex-direction: column; height: 100vh; padding: 20px 24px; max-width: 500px; margin: 0 auto; position: relative; z-index: 2; }

/* Top Bar */
.top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }
.top-bar .title { font-size: 12px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: var(--subtext); text-align: center; flex: 1; }
.top-bar button { background: none; border: none; color: var(--text); padding: 0; cursor: pointer; display: flex; align-items: center; justify-content: center; }

/* Album Art */
.art-wrapper { width: 100%; aspect-ratio: 1; border-radius: 8px; overflow: hidden; margin-bottom: 40px; box-shadow: 0 10px 40px rgba(0,0,0,0.5); background: #111; }
.art-wrapper img { width: 100%; height: 100%; object-fit: cover; }

/* Track Info & Actions */
.track-info-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }
.track-info { display: flex; flex-direction: column; overflow: hidden; padding-right: 15px; }
#ti { font-size: 24px; font-weight: 700; margin: 0 0 6px 0; white-space: nowrap; text-overflow: ellipsis; overflow: hidden; letter-spacing: -0.5px; }
#st { font-size: 16px; font-weight: 400; color: var(--subtext); margin: 0; white-space: nowrap; text-overflow: ellipsis; overflow: hidden; }
.track-actions { display: flex; gap: 20px; align-items: center; }
.track-actions button { background: none; border: none; color: var(--text); padding: 0; cursor: pointer; display: flex; }
#favbtn { color: var(--subtext); transition: color 0.2s; }
#favbtn.active { color: var(--accent); }

/* Seek Bar */
.seek-container { margin-bottom: 30px; }
.seek-bar-wrapper { position: relative; height: 32px; display: flex; align-items: center; cursor: pointer; }
.seek-bg { position: absolute; left: 0; right: 0; height: 4px; background: #333; border-radius: 2px; }
.seek-fill { position: absolute; left: 0; height: 4px; background: var(--text); border-radius: 2px; width: 0%; pointer-events: none; }
#seek { position: absolute; left: 0; right: 0; width: 100%; height: 100%; margin: 0; opacity: 0; cursor: pointer; }
.time-row { display: flex; justify-content: space-between; font-size: 12px; color: var(--subtext); font-variant-numeric: tabular-nums; margin-top: -5px; }

/* Transport Controls */
.transport { display: flex; justify-content: space-between; align-items: center; margin-bottom: 35px; }
.transport button { background: none; border: none; color: var(--text); cursor: pointer; padding: 10px; display: flex; align-items: center; justify-content: center; }
.transport .secondary { color: var(--subtext); }
.transport .play-pause { width: 64px; height: 64px; background: var(--text); color: var(--bg); border-radius: 50%; display: flex; align-items: center; justify-content: center; padding: 0; }
.transport .play-pause svg { width: 28px; height: 28px; fill: var(--bg); }
.transport .main-skip svg { width: 32px; height: 32px; fill: var(--text); }

/* Bottom Action Bar */
.bottom-actions { display: flex; justify-content: space-between; align-items: center; margin-top: auto; padding-bottom: 10px; }
.bottom-actions button { background: none; border: none; color: var(--subtext); padding: 10px; cursor: pointer; display: flex; align-items: center; justify-content: center; }
#spkbtn.active { color: var(--accent); }

/* Background Blur */
.bg-blur { position: fixed; inset: 0; z-index: 1; opacity: 0.3; background-size: cover; background-position: center; filter: blur(60px) saturate(2); transform: scale(1.1); pointer-events: none; }
.bg-overlay { position: fixed; inset: 0; z-index: 1; background: linear-gradient(to bottom, rgba(0,0,0,0.2) 0%, rgba(0,0,0,0.8) 50%, rgba(0,0,0,1) 100%); pointer-events: none; }

/* Overlays (Queue & Search) */
.panel { position: fixed; inset: 0; background: var(--bg); z-index: 10; transform: translateY(100%); transition: transform 0.3s cubic-bezier(0.2, 0.8, 0.2, 1); display: flex; flex-direction: column; }
.panel.open { transform: translateY(0); }
.panel-header { display: flex; justify-content: space-between; align-items: center; padding: 20px 24px; border-bottom: 1px solid #222; }
.panel-header h2 { margin: 0; font-size: 16px; font-weight: 700; }
.panel-header button { background: none; border: none; color: var(--text); cursor: pointer; padding: 5px; }
.panel-content { flex: 1; overflow-y: auto; padding: 0 24px 40px 24px; }

/* Queue Items */
.qrow { display: flex; align-items: center; padding: 16px 0; border-bottom: 1px solid #1a1a1a; gap: 15px; }
.qrow:active { background: #111; }
.qrow .t { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 16px; font-weight: 500; }
.qrow .cur { color: var(--accent); }
.qrow .actions { display: flex; gap: 10px; }
.qrow .actions button { background: none; border: none; color: var(--subtext); cursor: pointer; padding: 5px; }

/* Search Box */
.search-box { display: flex; gap: 10px; margin: 20px 0; }
#qq { flex: 1; background: #1a1a1a; border: none; border-radius: 8px; padding: 14px 16px; color: var(--text); font-size: 16px; outline: none; }
#qq:focus { box-shadow: 0 0 0 1px var(--subtext); }

#r div { display: flex; align-items: center; gap: 15px; padding: 16px 0; border-bottom: 1px solid #1a1a1a; cursor: pointer; }
#r div:active { background: #111; }
#r .t { flex: 1; font-size: 16px; font-weight: 500; }

/* PIN Overlay */
#pinoverlay{position:fixed;inset:0;background:rgba(0,0,0,.8);backdrop-filter:blur(10px);
  display:none;align-items:center;justify-content:center;padding:24px; z-index: 100;}
#pinbox{background:#111;border-radius:16px;padding:30px;max-width:320px;width:100%; border: 1px solid #333; text-align: center;}
#pinbox b{font-size:18px;}
#pinbox input{width:100%;margin-top:20px;background:#000;border:1px solid #333;border-radius:8px;padding:14px;
  font-size:16px;color:var(--text);outline:none; text-align: center;}
#pinbox input:focus{border-color:var(--accent);}
#pinbox button{border:0;width:100%;margin-top:20px;border-radius:24px;padding:14px;font-size:16px;
  font-weight:700;cursor:pointer;color:#000;background:var(--text);}
</style>
</head><body>

<div class="bg-blur" id="bgblur"></div>
<div class="bg-overlay"></div>

<div class="player-view">
  <div class="top-bar">
    <button onclick="togglePanel('search')"><i data-feather="search"></i></button>
    <div class="title">skyemusic web</div>
    <button><i data-feather="more-horizontal"></i></button>
  </div>

  <div class="art-wrapper">
    <img id="art" src="data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=">
  </div>
  
  <div class="track-info-row">
    <div class="track-info">
      <h2 id="ti">—</h2>
      <p id="st">Loading...</p>
    </div>
    <div class="track-actions">
      <button id="favbtn" onclick="c('fav')">
        <i data-feather="heart"></i>
      </button>
    </div>
  </div>
  
  <div class="seek-container">
    <div class="seek-bar-wrapper">
      <div class="seek-bg"></div>
      <div class="seek-fill" id="seekfill"></div>
      <input id="seek" type="range" min="0" max="0" value="0">
    </div>
    <div class="time-row">
      <span id="tcur">0:00</span>
      <span id="tdur">0:00</span>
    </div>
  </div>
  
  <div class="transport">
    <button id="shufbtn" class="secondary" onclick="cmd('shuffle')"><i data-feather="shuffle"></i></button>
    <button class="main-skip" onclick="cmd('prev')"><i data-feather="skip-back"></i></button>
    <button id="pp" class="play-pause" onclick="cmd('toggle')"><i data-feather="play"></i></button>
    <button class="main-skip" onclick="cmd('next')"><i data-feather="skip-forward"></i></button>
    <button id="rptbtn" class="secondary" onclick="cmd('repeat')"><i data-feather="repeat"></i></button>
  </div>
  
  <div class="bottom-actions">
    <button id="spkbtn" onclick="toggleSpeaker()"><i data-feather="speaker"></i></button>
    <button onclick="togglePanel('queue')"><i data-feather="list"></i></button>
  </div>
</div>

<!-- Queue Panel -->
<div id="queue-panel" class="panel">
  <div class="panel-header">
    <button style="visibility:hidden"><i data-feather="chevron-down"></i></button>
    <h2>Queue</h2>
    <button onclick="togglePanel('queue')"><i data-feather="chevron-down"></i></button>
  </div>
  <div class="panel-content" id="q"></div>
</div>

<!-- Search Panel -->
<div id="search-panel" class="panel">
  <div class="panel-header">
    <button style="visibility:hidden"><i data-feather="chevron-down"></i></button>
    <h2>Search</h2>
    <button onclick="togglePanel('search')"><i data-feather="chevron-down"></i></button>
  </div>
  <div class="panel-content">
    <div class="search-box">
      <input id="qq" placeholder="Search for tracks..." onkeydown="if(event.key==='Enter')search()">
      <button onclick="search()" style="background:none;border:none;color:var(--text);padding:10px;"><i data-feather="search"></i></button>
    </div>
    <div id="r"></div>
  </div>
</div>

<audio id="spkaudio" style="display:none" playsinline></audio>

<div id="pinoverlay"><div id="pinbox"><b>Enter PIN</b>
<input id="p" type="password" autocomplete="off" onkeydown="if(event.key==='Enter')savePin()">
<button onclick="savePin()">Unlock</button></div></div>

<script>
feather.replace();

// ─── State (all declared at top to avoid hoisting bugs) ───────────────────────
let pin         = localStorage.getItem('tune_pin') || '';
let _lastStatus  = null;   // last status object received from server
let _lastStatusAt = 0;     // Date.now() when _lastStatus was stored
let _isPlaying   = false;
let spkActive    = false;
let spkTrackId   = '';     // url of track currently loaded in <audio>
let spkSeeking   = false;  // true while an async seek is in-flight
let _sseOk       = false;
const spkAudio   = document.getElementById('spkaudio');

// ─── Utilities ────────────────────────────────────────────────────────────────
function togglePanel(id){ document.getElementById(id+'-panel').classList.toggle('open'); }
function qs(v,a){let u='/api/'+v+'?pin='+encodeURIComponent(pin);if(a)u+='&arg='+encodeURIComponent(a);return u;}
function vid(u){const m=(u||'').match(/[?&]v=([\w-]{11})/);return m?m[1]:'';}
const fmt=s=>{s=Math.max(0,Math.floor(s||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');};
async function cmd(v,a){try{const r=await fetch(qs(v,a));if(r.status===401){askPin();return;}await refresh();}catch(e){}}
const c = cmd;

// ─── Host position extrapolation ──────────────────────────────────────────────
// Returns what the host playback position is RIGHT NOW, no server call needed.
function hostPos(){
  if(!_lastStatus) return 0;
  return (_lastStatus.position||0)+(Date.now()-_lastStatusAt)/1000;
}

// ─── Speaker: correct load → canplay → seek → seeked → play chain ─────────────
spkAudio.addEventListener('canplay', ()=>{
  if(!spkActive||spkSeeking) return;
  spkSeeking=true;
  spkAudio.currentTime=hostPos();   // seek AFTER audio is ready — always lands
});
spkAudio.addEventListener('seeked', ()=>{
  spkSeeking=false;
  if(spkActive&&_isPlaying&&spkAudio.paused) spkAudio.play().catch(()=>{});
});

// ─── PLL: continuous drift correction every 100ms ────────────────────────────
function spkPLL(){
  if(spkAudio.paused||!spkAudio.src||spkSeeking) return;
  const hp=hostPos();
  if(hp<=0) return;
  const drift=spkAudio.currentTime-hp;  // +ve = phone is ahead of host
  if(Math.abs(drift)>2.5){
    spkSeeking=true;
    spkAudio.currentTime=hp;
    spkAudio.playbackRate=1.0;
  } else if(Math.abs(drift)>0.06){
    spkAudio.playbackRate=drift>0?0.94:1.06;  // max ±6%, imperceptible
  } else {
    spkAudio.playbackRate=1.0;  // locked in
  }
}

function loadSpeakerTrack(d){
  const url=d.direct_url||(d.url?'/api/stream_proxy?pin='+encodeURIComponent(pin)+'&url='+encodeURIComponent(d.url):'');
  if(!url) return;
  spkTrackId=d.url;
  spkSeeking=false;
  spkAudio.pause();
  spkAudio.src=url;
  spkAudio.load();  // triggers canplay → seeked → play
}

function toggleSpeaker(){
  spkActive=!spkActive;
  const btn=document.getElementById('spkbtn');
  if(spkActive){
    btn.classList.add('active');
    // play() MUST be called here (inside user gesture) for iOS autoplay policy
    if(_lastStatus&&_lastStatus.url){ loadSpeakerTrack(_lastStatus); spkAudio.play().catch(()=>{}); }
    else refresh();
  } else {
    btn.classList.remove('active');
    spkAudio.pause(); spkAudio.src=''; spkTrackId=''; spkAudio.playbackRate=1.0;
  }
}

function syncSpeaker(d){
  if(!spkActive) return;
  if(d.url&&d.url!==spkTrackId){
    loadSpeakerTrack(d);
    if(d.state==='playing') spkAudio.play().catch(()=>{});
    return;
  }
  if(d.state==='playing'&&spkAudio.paused&&spkAudio.src&&!spkSeeking) spkAudio.play().catch(()=>{});
  else if(d.state!=='playing'&&!spkAudio.paused) spkAudio.pause();
  if(!spkAudio.paused) spkPLL();
}

// ─── 100ms tick: silky seekbar + continuous PLL ───────────────────────────────
function tickSeek(){
  if(!_isPlaying) return;
  const s=document.getElementById('seek');
  if(s.__drag) return;
  const max=parseFloat(s.max)||1;
  const pos=Math.min(hostPos(),max);
  s.value=pos;
  document.getElementById('tcur').textContent=fmt(pos);
  document.getElementById('seekfill').style.width=((pos/max)*100)+'%';
  if(spkActive) spkPLL();
}
setInterval(tickSeek,100);

// ─── Network: fetch + SSE ─────────────────────────────────────────────────────
async function refresh(){ try{
  const t0=Date.now();
  const r=await fetch(qs('status')); if(r.status===401){askPin();return;}
  const t1=Date.now();
  const j=await r.json();
  applyStatus(j.data||{},(t1-t0)/2000);  // lat = one-way trip estimate
}catch(e){}}

function applyStatus(d,lat){
  _lastStatus  =Object.assign({},d,{position:(d.position||0)+lat});
  _lastStatusAt=Date.now();
  _isPlaying   =(d.state==='playing');

  document.getElementById('ti').textContent=d.title||'nothing playing';
  document.getElementById('st').textContent=d.channel||'—';

  const v=vid(d.url);
  const artUrl=v?'https://i.ytimg.com/vi/'+v+'/hqdefault.jpg':'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
  const artEl=document.getElementById('art');
  if(artEl.dataset.src!==artUrl){
    artEl.dataset.src=artUrl; artEl.src=artUrl;
    document.getElementById('bgblur').style.backgroundImage="url('"+artUrl+"')";
  }

  const pp=document.getElementById('pp');
  if(pp) pp.innerHTML=(_isPlaying||d.state==='loading')?'<i data-feather="pause"></i>':'<i data-feather="play" style="margin-left:4px"></i>';

  const sb=document.getElementById('shufbtn');
  if(sb){
    const isShuf=!!d.shuffle;
    sb.style.color=isShuf?'var(--accent)':'var(--subtext)';
    sb.innerHTML='<i data-feather="shuffle"'+(isShuf?' color="var(--accent)"':'')+'></i>';
  }

  const rb=document.getElementById('rptbtn');
  if(rb){
    const rpt=d.repeat||'off';
    const isRpt=(rpt==='all'||rpt==='one');
    rb.style.color=isRpt?'var(--accent)':'var(--subtext)';
    rb.innerHTML='<i data-feather="repeat"'+(isRpt?' color="var(--accent)"':'')+'></i>'+(rpt==='one'?'<span style="font-size:10px;font-weight:bold;position:absolute;margin-top:-10px;margin-left:14px;color:var(--accent)">1</span>':'');
  }

  const fb=document.getElementById('favbtn');
  if(fb){
    fb.className=d.fav?'active':'';
    fb.innerHTML='<i data-feather="heart"'+(d.fav?' fill="var(--accent)" color="var(--accent)"':'')+"></i>";
  }

  const s=document.getElementById('seek');
  if(s){
    s.max=Math.max(1,Math.round(d.duration||0));
    document.getElementById('tdur').textContent=fmt(d.duration||0);
  }

  syncSpeaker(d);

  const rows=(d.queue||[]).map((t,i)=>{
    const cur=(i===d.current_index);
    const ico=cur?'<i data-feather="bar-chart-2" color="var(--accent)"></i>':'<i data-feather="music" color="var(--subtext)"></i>';
    const lbl='<div onclick="cmd(\'playindex\',\''+(i+1)+'\')" style="display:flex;align-items:center;gap:15px;flex:1;overflow:hidden;cursor:pointer"><div style="width:24px">'+ico+'</div><div class="t"'+(cur?' style="color:var(--accent)"':'')+'>'+t.title+'</div></div>';
    const up=i>0?'<button onclick="cmd(\'move\',\''+i+' '+(i-1)+'\')"><i data-feather="chevron-up"></i></button>':'';
    const dn=i<d.queue_len-1?'<button onclick="cmd(\'move\',\''+(i+2)+' '+(i+1)+'\')"><i data-feather="chevron-down"></i></button>':'';
    const rm='<button onclick="cmd(\'remove\',\''+(i+1)+'\')"><i data-feather="x"></i></button>';
    return '<div class="qrow">'+lbl+'<div class="actions">'+up+dn+rm+'</div></div>';
  }).join('');
  document.getElementById('q').innerHTML=rows||'<div class="qrow" style="color:var(--subtext);justify-content:center">Queue is empty</div>';

  feather.replace();
}

// ─── Seekbar drag ─────────────────────────────────────────────────────────────
(function(){
  const s=document.getElementById('seek');
  s.addEventListener('input',()=>{s.__drag=true;document.getElementById('tcur').textContent=fmt(s.value);document.getElementById('seekfill').style.width=((s.value/s.max)*100)+'%';});
  s.addEventListener('change',()=>{s.__drag=false;cmd('seek',Math.round(s.value));});
})();

// ─── Search ───────────────────────────────────────────────────────────────────
async function search(){
  const q=document.getElementById('qq').value; if(!q.trim()) return;
  const r=await fetch(qs('search',q)); if(r.status===401){askPin();return;}
  const j=await r.json(); const rs=(j.data&&j.data.results)||[]; window._tr=rs;
  document.getElementById('r').innerHTML=rs.map((t,i)=>'<div onclick="cmd(\'play\',window._tr['+i+'].url);togglePanel(\'search\');"><i data-feather="play-circle"></i><span class="t">'+t.title+'</span></div>').join('')||'<div style="color:var(--subtext);text-align:center;padding:20px">No results found</div>';
  feather.replace();
}

// ─── PIN ─────────────────────────────────────────────────────────────────────
function askPin(){document.getElementById('pinoverlay').style.display='flex';document.getElementById('p').focus();}
function savePin(){pin=document.getElementById('p').value;localStorage.setItem('tune_pin',pin);document.getElementById('pinoverlay').style.display='none';refresh();}

// ─── SSE ─────────────────────────────────────────────────────────────────────
function connectSSE(){
  const es=new EventSource('/api/events?pin='+encodeURIComponent(pin));
  es.onmessage=e=>{try{_sseOk=true;applyStatus(JSON.parse(e.data),0);}catch(ex){}};
  es.onerror=()=>{_sseOk=false;es.close();setTimeout(connectSSE,3000);};
}
setInterval(()=>{if(!_sseOk)refresh();},2000);
refresh().then(()=>connectSSE());
</script></body></html>
"""


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
        dist_dir = Path(__file__).parent.parent / "web" / "dist"

        if parsed.path == "/" or parsed.path == "/index.html":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/events":
            # Allow public read-only access for Skyecast P2P broadcasts
            self._sse_stream()
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

        # Serve static assets from web/dist (js, css, icons, etc.)
        rel_path = parsed.path.lstrip("/")
        asset_file = dist_dir / rel_path
        if dist_dir.is_dir() and asset_file.is_file():
            import mimetypes
            ctype, _ = mimetypes.guess_type(str(asset_file))
            if not ctype:
                ctype = _EXTRA_TYPES.get(asset_file.suffix, "application/octet-stream")
            body = asset_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return

        self._json({"ok": False, "error": "not found"}, 404)

    def _sse_stream(self) -> None:
        """Long-lived SSE handler: subscribes to the broadcaster queue."""
        q: queue.Queue = queue.Queue(maxsize=10)
        _sse_subscribers.add(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            # Send initial state immediately
            try:
                data = self.daemon.dispatch(json.dumps({"verb": "status", "arg": ""}))
                payload = json.dumps(data.get("data", {}))
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
            except Exception:
                pass
            while True:
                try:
                    payload = q.get(timeout=15)
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                except queue.Empty:
                    # heartbeat to keep the connection alive through proxies
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            _sse_subscribers.discard(q)

    def log_message(self, *args) -> None:  # keep the daemon log clean
        pass


# Global set of SSE subscriber queues — populated on connect, removed on disconnect
_sse_subscribers: set[queue.Queue] = set()


def _broadcast_status(daemon) -> None:
    """Called by the broadcaster thread every second to push status to all SSE clients."""
    global _sse_subscribers
    if not _sse_subscribers:
        return
    try:
        data = daemon.dispatch(json.dumps({"verb": "status", "arg": ""}))
        payload = json.dumps(data.get("data", {}))
    except Exception:
        return
    dead = set()
    for q in list(_sse_subscribers):
        try:
            q.put_nowait(payload)
        except queue.Full:
            dead.add(q)
    _sse_subscribers -= dead


def start(daemon, port: int):
    server = RemoteServer(("0.0.0.0", port), Handler)
    Handler.daemon = daemon

    # SSE broadcaster: push status every second to all open /api/events connections
    def _broadcaster():
        while True:
            time.sleep(1)
            _broadcast_status(daemon)

    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=_broadcaster, daemon=True, name="sse-broadcaster").start()
    return server


