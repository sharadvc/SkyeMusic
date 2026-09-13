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
<title>SKYEMUSIC WEB</title>
<style>
:root {
  --bg: #f4f5f8;
  --card: #ffffff;
  --border: #e2e4e9;
  --ink: #0f1419;
  --muted: #657786;
  --accent: #00a4bd;
  --accent-subtle: rgba(0, 164, 189, 0.12);
  --shadow: 0 4px 20px rgba(0, 0, 0, 0.05);
}
html.dark {
  --bg: #0d1117;
  --card: #161b22;
  --border: #21262d;
  --ink: #f0f6fc;
  --muted: #8b949e;
  --accent: #00e5ff;
  --accent-subtle: rgba(0, 229, 255, 0.15);
  --shadow: 0 8px 30px rgba(0, 0, 0, 0.4);
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: var(--ink);
  background: var(--bg);
  min-height: 100vh;
  padding: 24px 16px 48px;
  transition: background 0.25s ease, color 0.25s ease;
}
.wrap { max-width: 580px; margin: 0 auto; }
.header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 20px;
}
.logo-box { display: flex; align-items: center; gap: 10px; }
.logo {
  font-size: 20px; font-weight: 900; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--ink);
}
.badge {
  font-size: 10px; font-weight: 800; letter-spacing: 0.08em;
  background: var(--accent-subtle); color: var(--accent);
  padding: 3px 8px; border-radius: 6px; border: 1px solid var(--accent);
  text-transform: uppercase;
}
.theme-btn {
  background: var(--card); border: 1px solid var(--border);
  color: var(--ink); font-size: 13px; font-weight: 600;
  padding: 8px 14px; border-radius: 20px; cursor: pointer;
  box-shadow: var(--shadow); display: flex; align-items: center; gap: 6px;
  transition: all 0.2s ease;
}
.theme-btn:active { transform: scale(0.95); }

.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 20px; box-shadow: var(--shadow);
  padding: 20px; margin-bottom: 20px; transition: background 0.25s ease;
}

#now { display: flex; gap: 16px; align-items: center; }
#art {
  width: 88px; height: 88px; object-fit: cover; border-radius: 14px;
  flex: none; background: var(--border); display: none;
  box-shadow: 0 4px 14px rgba(0,0,0,0.12);
}
.track-info { flex: 1; overflow: hidden; }
#ti {
  font-size: 17px; font-weight: 700; line-height: 1.3;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
#st { font-size: 13px; color: var(--muted); margin-top: 4px; font-weight: 500; }

#seekbar { display: flex; gap: 12px; align-items: center; margin-top: 20px; }
#seekbar span { font-size: 12px; color: var(--muted); min-width: 38px; font-variant-numeric: tabular-nums; font-weight: 600; }
#seekbar span:last-child { text-align: right; }
#seek {
  flex: 1; height: 6px; border-radius: 99px; appearance: none; -webkit-appearance: none;
  background: var(--border); outline: none; cursor: pointer; margin: 0;
}
#seek::-webkit-slider-thumb {
  -webkit-appearance: none; width: 18px; height: 18px; border-radius: 50%;
  background: var(--accent); border: 2px solid var(--card); box-shadow: 0 2px 6px rgba(0,0,0,0.2);
}

.transport { display: flex; justify-content: center; align-items: center; gap: 20px; margin-top: 22px; }
.transport button {
  width: 52px; height: 52px; border-radius: 50%; border: 1px solid var(--border);
  cursor: pointer; background: var(--card); color: var(--ink);
  box-shadow: var(--shadow); transition: transform 0.12s ease, background 0.2s ease;
  display: flex; align-items: center; justify-content: center;
}
.transport button:active { transform: scale(0.92); }
#pp {
  width: 62px; height: 62px;
  background: var(--accent); color: #ffffff; border: 0;
  box-shadow: 0 6px 20px var(--accent-subtle);
}
html.dark #pp { color: #000000; }

.ic { display: flex; justify-content: center; gap: 10px; margin-top: 16px; }
.ic button {
  border: 1px solid var(--border); border-radius: 14px; padding: 10px 18px;
  font-size: 14px; font-weight: 600; cursor: pointer; background: var(--card);
  color: var(--ink); box-shadow: var(--shadow); transition: transform 0.12s ease;
  display: flex; align-items: center; gap: 6px;
}
.ic button:active { transform: scale(0.95); }
#favbtn.on { color: #ff3b30; border-color: #ff3b30; }

h3 {
  margin: 26px 0 12px; font-size: 13px; font-weight: 800; letter-spacing: 0.08em;
  color: var(--muted); text-transform: uppercase;
}

.qrow {
  display: flex; gap: 10px; align-items: center; background: var(--card);
  border: 1px solid var(--border); border-radius: 14px;
  box-shadow: var(--shadow); padding: 12px 14px; margin: 8px 0;
}
.qrow .t { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; font-weight: 600; }
.qrow .cur { color: var(--accent); font-weight: 800; margin-right: 4px; }
.qrow button {
  padding: 6px 8px; margin: 0 2px; border: 1px solid var(--border);
  border-radius: 8px; cursor: pointer; background: var(--bg); color: var(--ink);
  display: inline-flex; align-items: center; justify-content: center;
}

#searchbox { display: flex; gap: 10px; margin-bottom: 14px; }
#qq {
  border: 1px solid var(--border); background: var(--card); border-radius: 14px;
  padding: 14px 18px; font-size: 15px; flex: 1; color: var(--ink); outline: none;
  box-shadow: var(--shadow);
}
#sbtn {
  border: 0; background: var(--accent); color: #ffffff; border-radius: 14px;
  padding: 0 22px; font-weight: 700; font-size: 14px; cursor: pointer;
  box-shadow: var(--shadow); display: flex; align-items: center; gap: 6px;
}
html.dark #sbtn { color: #000000; }

#pinoverlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5); backdrop-filter: blur(8px);
  display: none; align-items: center; justify-content: center; padding: 24px; z-index: 999;
}
#pinbox {
  background: var(--card); border: 1px solid var(--border); border-radius: 20px;
  box-shadow: 0 20px 50px rgba(0,0,0,0.3); padding: 28px; max-width: 320px; width: 100%;
}
#pinbox b { font-size: 18px; color: var(--ink); }
#pinbox input {
  width: 100%; margin-top: 16px; border: 1px solid var(--border); border-radius: 12px;
  padding: 14px; font-size: 15px; color: var(--ink); background: var(--bg); outline: none;
}
#pinbox button {
  border: 0; width: 100%; margin-top: 16px; border-radius: 12px; padding: 14px;
  font-size: 15px; font-weight: 700; cursor: pointer; color: #ffffff; background: var(--accent);
}
html.dark #pinbox button { color: #000000; }
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="logo-box">
      <div class="logo">SKYEMUSIC WEB</div>
      <div class="badge">HI-RES LOSSLESS</div>
    </div>
    <button id="themebtn" class="theme-btn" onclick="toggleTheme()">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
      Dark Mode
    </button>
  </div>

  <div class="card">
    <div id="now">
      <img id="art">
      <div class="track-info">
        <div id="ti">—</div>
        <div id="st"></div>
      </div>
    </div>
    <div id="seekbar">
      <span id="tcur">0:00</span>
      <input id="seek" type="range" min="0" max="0" value="0">
      <span id="tdur">0:00</span>
    </div>
    <div class="transport">
      <button onclick="c('prev')">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/></svg>
      </button>
      <button id="pp" onclick="c('toggle')">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3"/></svg>
      </button>
      <button onclick="c('next')">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg>
      </button>
    </div>
    <div class="ic">
      <button onclick="c('volume','-5')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="15" y1="12" x2="21" y2="12"/></svg>
      </button>
      <button onclick="c('volume','+5')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="19" y1="9" x2="19" y2="15"/><line x1="16" y1="12" x2="22" y2="12"/></svg>
      </button>
      <button id="favbtn" onclick="c('fav')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>
      </button>
    </div>
    <div class="ic" style="margin-top:12px">
      <button id="spkbtn" onclick="toggleSpeaker()">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="16" height="20" x="4" y="2" rx="2"/><circle cx="12" cy="14" r="4"/><line x1="12" y1="6" x2="12.01" y2="6"/></svg>
        <span id="spklabel">Phone Speaker OFF</span>
      </button>
    </div>
    <audio id="spkaudio" style="display:none" playsinline></audio>
  </div>

  <h3>Queue</h3>
  <div id="q"></div>

  <h3>Search</h3>
  <div id="searchbox">
    <input id="qq" placeholder="search song or artist…" onkeydown="if(event.key==='Enter')search()">
    <button id="sbtn" onclick="search()">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
      Search
    </button>
  </div>
  <div id="r"></div>
</div>

<div id="pinoverlay">
  <div id="pinbox">
    <b>🔐 PIN Required</b><br><br>
    <input id="p" pinmode autocomplete="off" placeholder="enter PIN" onkeydown="if(event.key==='Enter')savePin()"><br>
    <button onclick="savePin()">Unlock</button>
  </div>
</div>

<script>
let pin=localStorage.getItem('tune_pin')||'';
let spkActive=false;
let spkTrackUrl='';
let refreshing=false;

const SVGS = {
  play: `<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3"/></svg>`,
  pause: `<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`,
  heart: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>`,
  heartFilled: `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>`,
  up: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m18 15-6-6-6 6"/></svg>`,
  down: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>`,
  close: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>`,
  sun: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>`,
  moon: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>`
};

let dark = localStorage.getItem('tune_theme') === 'dark';
function applyTheme() {
  const tb = document.getElementById('themebtn');
  if (dark) {
    document.documentElement.classList.add('dark');
    if(tb) tb.innerHTML = SVGS.sun + ' Light Mode';
  } else {
    document.documentElement.classList.remove('dark');
    if(tb) tb.innerHTML = SVGS.moon + ' Dark Mode';
  }
}
function toggleTheme() {
  dark = !dark;
  localStorage.setItem('tune_theme', dark ? 'dark' : 'light');
  applyTheme();
}
applyTheme();

function toggleSpeaker(){
  spkActive=!spkActive;
  const btn=document.getElementById('spkbtn');
  const lbl=document.getElementById('spklabel');
  const audio=document.getElementById('spkaudio');
  if(spkActive){
    if(lbl) lbl.textContent='Phone Speaker ON';
    if(btn){
      btn.style.background='var(--accent)';
      btn.style.color=dark?'#000':'#fff';
    }
    refresh(true);
  }else{
    if(lbl) lbl.textContent='Phone Speaker OFF';
    if(btn){
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
    if(ppEl) ppEl.innerHTML = (d.state==='playing'||d.state==='loading') ? SVGS.pause : SVGS.play;

    const fbEl = document.getElementById('favbtn');
    if(fbEl){
      fbEl.innerHTML = d.fav ? SVGS.heartFilled : SVGS.heart;
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
        const up = (i > 0) ? `<button onclick="c('move','${i} ${i-1}')">${SVGS.up}</button>` : '';
        const dn = (i < d.queue_len - 1) ? `<button onclick="c('move','${i+2} ${i+1}')">${SVGS.down}</button>` : '';
        return `<div class=qrow>${label}<span style="flex:none">${up}${dn}<button onclick="c('remove','${i+1}')">${SVGS.close}</button></span></div>`;
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
      return `<div style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;margin:8px 0;background:var(--card);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow)">
        <div style="flex:1;overflow:hidden;margin-right:12px;cursor:pointer" onclick="playUrl('${urlEsc}')">
          <div style="font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${t.title}</div>
          ${ch}
        </div>
        <button onclick="playUrl('${urlEsc}')" style="border:0;background:var(--accent);color:${dark ? '#000' : '#fff'};border-radius:10px;padding:7px 14px;font-weight:700;font-size:13px;cursor:pointer;flex:none;display:flex;align-items:center;gap:4px">▶ Play</button>
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


