"""tune daemon: owns mpv and the queue, serves the control socket.

A single long-lived process (auto-started by the CLI/TUI) that spawns mpv,
drives it one track at a time, and exposes a Unix control socket where thin
clients send one JSON request line and read one JSON response line back.
"""

from __future__ import annotations

import concurrent.futures
import fcntl
import json
import os
import queue
import re
import shlex
import shutil
import signal
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import cast

from . import __version__
from .art import render as render_art
from .config import Config
from .lyrics import fetch as fetch_lyrics
from .lyrics import fetch_lrclib
from .musicbrain import (
    MOODS,
    build_discovery_session,
    build_mood_session,
    build_radio_session,
    build_similar_session,
    detect_intent,
    parse_mood_arg,
)
from .player import MpvError, MpvHandle
from .queue import (
    BOOKMARKS_FILE,
    CTRL_SOCK,
    FAVORITES_FILE,
    HISTORY_FILE,
    LOCK_FILE,
    LOG_FILE,
    MPV_SOCK,
    PLAYLIST_DIR,
    QUEUE_FILE,
    VOLUME_MAX,
    QueueState,
    Track,
    shuffle_no_adjacent,
)
from .downloader import (
    download_track,
    get_downloads_index,
    get_local_path,
    is_downloaded,
    remove_download,
)
from .eq import EQ_ORDER, EQ_PRESETS, format_mpv_eq, next_eq_preset
from .radio import MOOD_PRESETS, resolve_radio_query
from .resolver import (
    ResolveError,
    _is_url_or_id,
    get_direct_url,
    is_playlist_url,
    resolve,
    resolve_playlist,
    resolve_radio,
    search,
)


def log(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def mpv_argv(sock: str) -> list[str]:
    return [
        "mpv",
        "--idle=yes",
        f"--input-ipc-server={sock}",
        "--no-video",
        "--audio-display=no",
        "--force-window=no",
        "--no-terminal",
        "--really-quiet",
        "--prefetch-playlist=yes",
        "--volume=80",
        f"--volume-max={VOLUME_MAX}",
        # bestaudio/best: prefer audio-only stream, fall back to best merged.
        # "bestaudio" alone fails (~exit 1) for videos without a split audio stream.
        "--ytdl-format=bestaudio/best",
        # Streaming: start audio as soon as a few seconds are buffered
        "--cache=yes",
        "--demuxer-readahead-secs=20",
        "--demuxer-max-bytes=50MiB",
        "--demuxer-max-back-bytes=20MiB",
        # Fail fast on stalled network so we can retry
        "--network-timeout=10",
    ]





def _track_to_dict(t: Track) -> dict:
    return {"query": t.query, "title": t.title, "url": t.url,
            "duration": t.duration, "channel": t.channel}


def _do_download(ytdl: str, url: str, out_dir: str, base: str) -> None:
    try:
        subprocess.run(
            [ytdl, "-f", "bestaudio/best",
             "-o", os.path.join(out_dir, base + ".%(ext)s"), url],
            capture_output=True, timeout=600)
        log(f"downloaded {base}")
    except Exception as e:
        log(f"download failed: {e}")


def _do_download_queue(ytdl: str, tracks, out_dir: str) -> None:
    """Download every track in the queue sequentially (background thread)."""
    ok = 0
    for t in tracks:
        base = re.sub(r"[^\w\- ]+", "_", t.title).strip()[:60] or t.url
        _do_download(ytdl, t.url, out_dir, base)
        ok += 1
    log(f"queue download finished: {ok}/{len(tracks)} tracks → {out_dir}")


def _mk_dispatch(daemon):
    """Build the media-key callback: NX keycode -> daemon command."""
    verbs = {8: "next", 9: "prev", 10: "toggle"}  # NX_KEYTYPE_NEXT/PREVIOUS/PLAY

    def on_key(code: int) -> None:
        verb = verbs.get(code)
        if verb:
            try:
                daemon.dispatch(json.dumps({"verb": verb, "arg": ""}))
            except Exception:
                pass

    return on_key


class Daemon:
    def __init__(self):
        self.cfg = Config()
        had_queue = QUEUE_FILE.exists()
        self.q = QueueState()
        self.q.load()
        if not had_queue:  # seed a fresh start with config defaults
            self.q.volume = int(self.cfg.get("volume", 80))
            self.q.repeat = self.cfg.get("repeat", "off")
        self.player: MpvHandle | None = None
        self._lock = threading.RLock()
        self._state = "idle"  # idle | loading | playing | paused
        self._error: str | None = None
        self._last_pos = self.q.position
        self._error_streak = 0
        self._speed = self.q.speed
        self._stop = threading.Event()
        self._evq: queue.Queue | None = None
        self._restore_pos: float | None = None  # seek target to apply on file-loaded
        self._pending_arg: str | None = None  # song being resolved for 'play'
        self._back_stack: list[str] = []  # urls actually played, newest last (for prev)
        self._props: dict = {}  # mpv properties cached via observe_property
        # resolve/search caches: key -> (timestamp, value); no lock held during yt-dlp
        self._resolve_cache: dict[str, tuple[float, Track]] = {}
        self._search_cache: dict[str, tuple[float, list]] = {}
        self._cache_ttl = 3600.0  # resolve/search results stay fresh for an hour
        self._cache_max = 400
        self._op_lock = threading.Lock()  # serializes queue mutations (play/add/next/...)
        self._play_gen = 0  # bumps on every queue-replacing action; stale plays don't apply
        self._pending_enrich: set[str] = set()  # urls whose metadata is still loading
        self._direct_cache: dict[str, tuple[float, str]] = {}  # watch url -> direct stream
        self._direct_ttl = 1800.0  # direct stream URLs are prefetched for ~30 min
        self._last_load_url: str | None = None
        self._last_load_was_direct = False
        self._sq_running = False  # one smart-queue refill at a time
        self._gapless_loaded: int | None = None  # index preloaded via append-play
        self._session_mood: str | None = None  # active mood context (for continuation)
        self._session_lang: str | None = None  # optional language qualifier
        self._session_artist: str | None = None  # optional artist alias
        self._sleep_deadline: float | None = None  # time.monotonic deadline for sleep timer
        self._sleep_original_vol: int = 0  # saved volume for the fade-out ramp
        self._favs: list[dict] = self._load_favorites()
        self._history: list[dict] = self._load_history()
        self._bookmarks: list[dict] = self._load_bookmarks()
        self._lyrics: dict[str, list] = {}  # url -> timed lyric lines
        self._lyrics_in_flight: dict[str, threading.Event] = {}  # url -> in-flight event
        self._art_cache: dict[str, list] = {}  # url -> ANSI art lines
        self._undo: list = []  # ("remove", i, track) | ("clear", tr, idx) |
                              # ("replace", tr, idx) | ("shuffle", tr)
        self._eq_preset: str = str(self.cfg.get("equalizer_preset") or "flat")
        self._multiroom_nodes: dict[str, dict] = {}
        self._user_paused: bool = False
        # DJ mode is always off on daemon start — never restored from config.


        # This ensures `skye` always opens in normal view.
        self._dj_mode: bool = False
        self.cfg.set("dj_mode", False)


    # --- mpv management ----------------------------------------------------

    def _spawn_mpv(self) -> None:
        args = mpv_argv(str(MPV_SOCK))
        if self.cfg.get("gapless"):
            args.append("--gapless-audio=yes")
        if self.cfg.get("replaygain"):
            args.append("--replaygain=track")
        eq = str(self.cfg.get("equalizer") or "").strip()
        if eq and eq != "off":
            args.append(f"--af-add=lavfi=[equalizer=f={eq}]")
        dev = self.cfg.get("device")
        if dev:
            args.append(f"--audio-device={dev}")
        handle = MpvHandle(args, on_event=self._enqueue_event)
        handle.start()
        with self._lock:
            self.player = handle
            handle.set_property("volume", self.q.volume)
            if self._eq_preset and self._eq_preset != "flat":
                try:
                    handle.set_property("af", format_mpv_eq(self._eq_preset))
                except Exception:
                    pass
            for obs_id, name in ((1, "pause"), (2, "volume"),
                                 (3, "duration"), (4, "idle-active")):
                try:
                    handle.command("observe_property", obs_id, name)
                except MpvError:
                    pass  # property not supported; status falls back gracefully
        log(f"mpv started (pid {handle.proc.pid})")

    def _enqueue_event(self, msg: dict) -> None:
        """Called from the mpv reader thread — enqueue, never block."""
        if self._evq is not None:
            self._evq.put_nowait(msg)

    def _watch_mpv(self) -> None:
        """Respawn mpv if it dies, restoring volume, track, and position."""
        while not self._stop.is_set():
            handle = self.player
            if handle is None:
                time.sleep(0.5)
                continue
            rc = handle.wait()
            if self._stop.is_set():
                return
            log(f"mpv exited (rc={rc}); respawning")
            ok = False
            for attempt in range(3):
                try:
                    self._spawn_mpv()
                    ok = True
                    break
                except MpvError as e:
                    log(f"respawn {attempt + 1}/3 failed: {e}")
                    time.sleep(1)
            if not ok:
                with self._lock:
                    self._error = "mpv respawn failed after crash"
                    self._state = "idle"
                return
            with self._lock:
                if self.q.current() is not None:
                    self._restore_pos = self._last_pos
                    self._load_current_locked()
                else:
                    self._state = "idle"

    # --- loading & advance (callers hold self._lock) ------------------------

    def _load_current_locked(self) -> None:
        track = self.q.current()
        if track is None or self.player is None:
            self._state = "idle"
            return
        self._gapless_loaded = None  # manual load cancels any pending gapless
        self._error = None
        self._error_streak = 0
        self._state = "loading"
        local_path = get_local_path(track.url)
        direct = self._cached_direct(track.url) if not local_path else None
        target_path = local_path or direct or track.url
        self._last_load_url = track.url
        self._last_load_was_direct = (local_path is not None) or (direct is not None)
        if local_path:
            log(f"loading downloaded local file: {local_path}")
        elif direct:
            log(f"loading direct stream: {track.url}")
        try:
            self.player.command("loadfile", target_path, "replace")
        except MpvError as e:
            self._error = str(e)
            self._state = "idle"
        self.q.save()
        if self.cfg.get("notify", True):
            from .notify import notify_track
            notify_track(track)
        # Background prefetch lyrics for current and upcoming tracks so track changes have 0ms latency
        threading.Thread(target=self._prefetch_lyrics_worker, args=(track,), daemon=True).start()
        self._prefetch_next_locked()

    def _prefetch_lyrics_worker(self, track) -> None:
        if track is None or not getattr(track, "url", None):
            return
        with self._lock:
            if track.url in self._lyrics or track.url in self._lyrics_in_flight:
                return
            ev = threading.Event()
            self._lyrics_in_flight[track.url] = ev
        try:
            lines = fetch_lyrics(
                url=track.url,
                title=getattr(track, "title", ""),
                artist=getattr(track, "channel", "") or "",
                duration=getattr(track, "duration", None),
                query=getattr(track, "query", "") or "",
            )
            with self._lock:
                self._lyrics[track.url] = lines or []
                if len(self._lyrics) > self._cache_max:
                    self._lyrics.pop(next(iter(self._lyrics)))
        except Exception:
            with self._lock:
                if track.url not in self._lyrics:
                    self._lyrics[track.url] = []
        finally:
            with self._lock:
                self._lyrics_in_flight.pop(track.url, None)
            ev.set()

    def _cached_direct(self, url: str) -> str | None:
        with self._lock:
            hit = self._direct_cache.get(url)
            if hit and time.time() - hit[0] < self._direct_ttl:
                return hit[1]
            self._direct_cache.pop(url, None)
            return None

    def _prefetch_next_locked(self) -> None:
        """Prefetch direct URLs and lyrics for the next couple of tracks so track
        changes and lyrics transitions happen in real time with zero latency."""
        idx = self.q.index
        for k in (idx + 1, idx + 2):
            if 0 <= k < len(self.q.tracks):
                t = self.q.tracks[k]
                url = t.url
                if self._cached_direct(url) is None:
                    threading.Thread(target=self._prefetch_direct, args=(url,),
                                     daemon=True).start()
                with self._lock:
                    in_cache = url in self._lyrics
                if not in_cache:
                    threading.Thread(target=self._prefetch_lyrics_worker, args=(t,),
                                     daemon=True).start()

    def _prefetch_direct(self, url: str) -> None:
        try:
            direct = get_direct_url(url)
        except ResolveError as e:
            log(f"prefetch direct failed for {url}: {e}")
            return
        with self._lock:
            self._direct_cache[url] = (time.time(), direct)
            log(f"prefetch direct ok: {url}")
            if len(self._direct_cache) > 50:
                self._direct_cache.pop(next(iter(self._direct_cache)), None)

    def _advance_locked(self, *, skip_error: bool = False) -> None:
        """Move to the next track on EOF/error, honoring repeat mode."""
        n = len(self.q.tracks)
        if n == 0:
            self._state = "idle"
            return
        if self.q.repeat == "one" and not skip_error and self.q.index >= 0:
            self._load_current_locked()  # replay the same track
            return
        if self.q.index >= n - 1:
            if self.q.repeat == "all":
                self._push_back()
                self.q.index = 0
                if self.q.shuffle:
                    self.q.reshuffle_after_current()
                self._gapless_loaded = None
                self._load_current_locked()
            else:
                last_url = self.q.tracks[-1].url if self.q.tracks else None
                self._push_back()
                self.q.index = -1
                self._state = "idle"
                self.q.save()
                if self.cfg.get("autoplay") and last_url:
                    threading.Thread(target=self._autoplay, args=(last_url,),
                                     daemon=True).start()
            return
        self._push_back()
        next_idx = self.q.index + 1
        # If the position loop already preloaded this via append-play, mpv will
        # start it seamlessly — don't issue a second loadfile.
        if self._gapless_loaded == next_idx:
            self._gapless_loaded = None
            self.q.index = next_idx
            self._error_streak = 0
            self._error = None
            self.q.save()
            return
        self.q.index = next_idx
        self._load_current_locked()

    def _autoplay(self, url: str) -> None:
        """Smart radio: when the queue ends, continue with related tracks."""
        try:
            tracks = resolve_radio(url)
        except Exception as e:
            log(f"autoplay failed: {e}")
            return
        if not tracks:
            return
        with self._op_lock:
            with self._lock:
                existing = {t.url for t in self.q.tracks}
                fresh = [t for t in tracks if t.url not in existing]
                if not fresh:
                    return
                self.q.tracks.extend(fresh)
                if self.q.index < 0:
                    self.q.index = 0
                    self._load_current_locked()
                else:
                    self.q.save()
        log(f"autoplay: queued {len(fresh)} related tracks")

    # --- back stack (what you actually heard, for prev) ---------------------

    def _push_back(self) -> None:
        """Record the current track as 'previously heard' (caller holds _lock)."""
        now = self.q.current()
        if now is None:
            return
        self._back_stack.append(now.url)
        if len(self._back_stack) > 50:
            self._back_stack = self._back_stack[-50:]

    def _clear_back(self) -> None:
        self._back_stack = []

    def _index_of(self, url: str) -> int | None:
        for i, t in enumerate(self.q.tracks):
            if t.url == url:
                return i
        return None


    # --- mpv event handling -------------------------------------------------

    def _event_loop(self) -> None:
        while not self._stop.is_set():
            try:
                ev = self._evq.get(timeout=0.2)
            except queue.Empty:
                continue
            kind = ev.get("event")
            if kind == "end-file":
                self._on_end_file(ev)
            elif kind == "start-file":
                with self._lock:
                    pass  # (reserved: a load is starting)
            elif kind == "file-loaded":
                with self._lock:
                    self._state = "playing"
                    if self.player and not self._user_paused:
                        try:
                            self.player.set_property("pause", False)
                        except Exception:
                            pass
                    self._apply_loaded_position_locked()

                    if self._speed != 1.0 and self.player:
                        try:
                            self.player.set_property("speed", self._speed)
                        except MpvError:
                            pass
                    self._sync_title_from_mpv_locked()
                    self._on_track_loaded_locked()
                    self._prefetch_next_locked()
                    self._maybe_smart_queue_locked()
            elif kind == "idle":
                with self._lock:
                    self._state = "idle"
            elif kind == "property-change":
                name = ev.get("name")
                data = ev.get("data")
                with self._lock:
                    if name:
                        if data is None:
                            self._props.pop(name, None)
                        else:
                            self._props[name] = data

    def _on_end_file(self, ev: dict) -> None:
        reason = ev.get("reason", "")
        with self._lock:
            if reason == "eof":
                self._error_streak = 0
                now = self.q.current()
                if now:
                    t = now
                    threading.Thread(target=self._scrobble,
                                     args=(t.title, t.channel, t.url, t.duration),
                                     daemon=True).start()
                    self._record_signal(now.url, "completed")
                self._record_position_locked()
                self._advance_locked()
            elif reason == "error":
                if self._last_load_was_direct and self._last_load_url:
                    # the prefetched direct stream expired; retry with the watch URL
                    self._direct_cache.pop(self._last_load_url, None)
                    self._last_load_was_direct = False
                    self._load_current_locked()
                    return
                self._error_streak += 1
                now = self.q.current()
                if now:
                    self._record_signal(now.url, "skip")
                if self._error_streak >= 3:
                    self._error = "3 consecutive tracks failed to play"
                    self._state = "idle"
                else:
                    self._advance_locked(skip_error=True)
            # stop/quit/redirect are expected during manual control; ignore.

    def _position_loop(self) -> None:
        ticks = 0
        while not self._stop.is_set():
            time.sleep(1)
            ticks += 1
            with self._lock:
                handle = self.player
                if handle is None:
                    continue
                pos = handle.get_property("time-pos")
                if pos is not None:
                    self._last_pos = float(pos)
                    self.q.position = float(pos)
                    if self.cfg.get("resume"):
                        now = self.q.current()
                        if now:
                            self.q.positions[now.url] = float(pos)
                            if len(self.q.positions) > 500:
                                self.q.positions.pop(next(iter(self.q.positions)), None)
                    # gapless: preload the next track ~3-8s before the current ends
                    if self._state == "playing" and self._gapless_loaded is None:
                        now = self.q.current()
                        if now and now.duration:
                            remaining = now.duration - float(pos)
                            if 2.0 < remaining < 8.0:
                                nxt = self.q.index + 1
                                if 0 <= nxt < len(self.q.tracks):
                                    url = self.q.tracks[nxt].url
                                    direct = self._cached_direct(url)
                                    try:
                                        handle.command("loadfile", direct or url,
                                                       "append-play")
                                        self._gapless_loaded = nxt
                                    except MpvError:
                                        pass
                if ticks % 5 == 0:
                    self.q.save()

    # --- loaded-track position / resume / smart queue ------------------------

    def _apply_loaded_position_locked(self) -> None:
        """Seek on file-loaded: crash-resume pos, per-track resume, or intro skip."""
        if self.player is None:
            return
        now = self.q.current()
        # 1) crash recovery / daemon-start resume (highest priority)
        if self._restore_pos is not None:
            pos, self._restore_pos = self._restore_pos, None
            if pos > 5:
                try:
                    self.player.command("seek", pos, "absolute")
                except MpvError:
                    pass
            return
        if now is None:
            return
        url = now.url
        # 2) per-track resume (podcast mode)
        if self.cfg.get("resume"):
            saved = self.q.positions.get(url)
            if saved and saved > 5:
                try:
                    self.player.command("seek", saved, "absolute")
                except MpvError:
                    pass
                return
        # 3) intro skip for a never-resumed track
        n = int(self.cfg.get("intro_skip") or 0)
        if n > 0:
            try:
                self.player.command("seek", n, "absolute")
            except MpvError:
                pass

    def _record_position_locked(self) -> None:
        now = self.q.current()
        if now and self.cfg.get("resume"):
            self.q.positions[now.url] = max(0.0, self._last_pos)
            if len(self.q.positions) > 500:
                self.q.positions.pop(next(iter(self.q.positions)), None)

    def _sync_title_from_mpv_locked(self) -> None:
        """Fill a placeholder track's title from mpv (it already resolved the
        stream), so history/status show the real name without a yt-dlp call."""
        now = self.q.current()
        if now is None or self.player is None or now.url not in self._pending_enrich:
            return
        if self._last_load_was_direct:
            return  # mpv's media-title is the stream URL; enrich fills the real one
        title = self.player.get_property("media-title")
        if isinstance(title, str) and title.strip():
            now.title = title.strip()
            self.q.save()

    def _maybe_smart_queue_locked(self) -> None:
        """If the queue is nearly empty and smart_queue is on, refill it."""
        if not self.cfg.get("smart_queue") or self._sq_running:
            return
        now = self.q.current()
        if now is None:
            return
        if len(self.q.tracks) - self.q.index - 1 < 3:
            self._sq_running = True
            threading.Thread(target=self._smart_queue_refill, args=(now.url,),
                             daemon=True).start()

    def _smart_queue_refill(self, url: str) -> None:
        mood = self._session_mood
        tracks: list[Track] = []
        try:
            tracks = list(resolve_radio(url))
            # when a mood session is active, blend in mood-appropriate picks
            if mood:
                ctx = self._session_ctx()
                extra = build_mood_session(mood, self._engine_search, limit=8,
                                           avoid=ctx["avoid"] | {url},
                                           lang=self._session_lang,
                                           artist=self._session_artist)
                tracks += extra
        except Exception as e:
            log(f"smart queue failed: {e}")
            self._sq_running = False
            return
        added = 0
        if tracks:
            with self._op_lock:
                with self._lock:
                    existing = {t.url for t in self.q.tracks}
                    recent = {h.get("url") for h in self._history[:40] if h.get("url")}
                    fresh = [t for t in tracks
                             if t.url not in existing and t.url not in recent][:20]
                    added = len(fresh)
                    if fresh:
                        self.q.tracks.extend(fresh)
                        self.q.save()
        self._sq_running = False
        if added:
            log(f"smart queue: appended {added} related tracks")

    # --- resolve cache ------------------------------------------------------

    def _cache_get(self, cache: dict, key: str):
        with self._lock:
            hit = cache.get(key)
            if hit and time.time() - hit[0] < self._cache_ttl:
                return hit[1]
            cache.pop(key, None)
            return None

    def _cache_put(self, cache: dict, key: str, value) -> None:
        with self._lock:
            cache[key] = (time.time(), value)
            if len(cache) > self._cache_max:
                oldest = min(cache, key=lambda k: cache[k][0])
                del cache[oldest]

    def _cached_resolve(self, arg: str) -> Track:
        hit = self._cache_get(self._resolve_cache, arg)
        if hit is not None:
            return hit
        track = resolve(arg)  # slow; runs without the lock held
        self._cache_put(self._resolve_cache, arg, track)
        return track

    # --- favorites ----------------------------------------------------------

    def _load_favorites(self) -> list[dict]:
        try:
            data = json.loads(FAVORITES_FILE.read_text())
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_favorites(self) -> None:
        FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FAVORITES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._favs, indent=2))
        os.replace(tmp, FAVORITES_FILE)

    # --- history / track-change side effects --------------------------------

    def _load_history(self) -> list[dict]:
        try:
            data = json.loads(HISTORY_FILE.read_text())
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_history(self) -> None:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._history, indent=2))
        os.replace(tmp, HISTORY_FILE)

    def _record_signal(self, url: str, kind: str) -> None:
        """Record a listening signal (skip/completed) on a history entry."""
        key = "skips" if kind == "skip" else kind
        for h in self._history:
            if h.get("url") == url:
                h[key] = h.get(key, 0) + 1
                if kind == "skip":
                    h["last_skip_ts"] = int(time.time())
                break
        self._save_history()

    def _load_bookmarks(self) -> list[dict]:
        try:
            data = json.loads(BOOKMARKS_FILE.read_text())
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_bookmarks(self) -> None:
        BOOKMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BOOKMARKS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._bookmarks, indent=2))
        os.replace(tmp, BOOKMARKS_FILE)

    def _on_track_loaded_locked(self) -> None:
        """Record the play, fire the notification + hook. Caller holds _lock."""
        now = self.q.current()
        if now is None:
            return
        # history: bump count / move to front, cap at 200
        for i, h in enumerate(self._history):
            if h.get("url") == now.url:
                h["count"] = h.get("count", 1) + 1
                if time.time() - h.get("ts", 0) < 300:
                    h["replays"] = h.get("replays", 0) + 1  # played again recently
                h["ts"] = int(time.time())
                self._history.pop(i)
                self._history.insert(0, h)
                break
        else:
            self._history.insert(0, {"url": now.url, "title": now.title,
                                     "query": now.query, "channel": now.channel,
                                     "duration": now.duration, "count": 1,
                                     "ts": int(time.time())})
        self._history = self._history[:200]
        self._save_history()
        # offline cache: save the stream in the background so it replays instantly
        if self.cfg.get("cache_streams"):
            threading.Thread(target=self._cache_stream, args=(now.url, now.title),
                             daemon=True).start()
        # notification + hook run in a background thread (never block playback)
        title, url = now.title, now.url
        threading.Thread(target=self._notify_track, args=(title, url), daemon=True).start()
        # scrobble "now playing" to ListenBrainz if configured
        if self.cfg.get("listenbrainz_token"):
            threading.Thread(target=self._playing_now,
                             args=(now.title, now.channel, now.url),
                             daemon=True).start()

    def _playing_now(self, title: str, channel: str, url: str) -> None:
        try:
            from .scrobble import submit_playing_now
            submit_playing_now(self.cfg.get("listenbrainz_token"), title,
                               channel or "unknown", url)
        except Exception as e:
            log(f"scrobble playing-now failed: {e}")
        lf_user = self.cfg.get("lastfm_user")
        lf_token = self.cfg.get("lastfm_token")
        if lf_user and lf_token:
            try:
                from .scrobble import submit_lastfm
                submit_lastfm(lf_user, lf_token, title, channel or "unknown",
                              self.q.current().duration if self.q.current() else 0)
            except Exception as e:
                log(f"last.fm now-playing failed: {e}")

    def _scrobble(self, title: str, channel: str, url: str, duration: float | None) -> None:
        token = self.cfg.get("listenbrainz_token")
        lf_user = self.cfg.get("lastfm_user")
        lf_token = self.cfg.get("lastfm_token")
        if token:
            try:
                from .scrobble import submit_scrobble
                submit_scrobble(token, title, channel or "unknown", url,
                                int(time.time()), duration)
            except Exception as e:
                log(f"scrobble failed: {e}")
        if lf_user and lf_token:
            try:
                from .scrobble import submit_lastfm
                submit_lastfm(lf_user, lf_token, title, channel or "unknown",
                              duration or 0)
            except Exception as e:
                log(f"last.fm scrobble failed: {e}")

    def _cache_stream(self, url: str, title: str) -> None:
        """Background-download a stream so it replays without the network."""
        out_dir = os.path.expanduser("~/.cache/tune/streams")
        os.makedirs(out_dir, exist_ok=True)
        base = re.sub(r"[^\w\- ]+", "_", title).strip()[:60] or url
        ytdl = shutil.which("yt-dlp") or os.path.expanduser("~/.local/bin/yt-dlp")
        _do_download(ytdl, url, out_dir, base)

    def _notify_track(self, title: str, url: str) -> None:
        if self.cfg.get("notifications"):
            try:
                subprocess.run(
                    ["osascript", "-e",
                     f'display notification "{title.replace(chr(34), "")}" '
                     f'with title "tune ♪"'],
                    capture_output=True, timeout=5)
            except Exception:
                pass
        hook = self.cfg.get("on_track_change")
        if hook:
            try:
                cmd = (f"TITLE={shlex.quote(title)}; export TITLE; "
                       f"URL={shlex.quote(url)}; export URL; exec {hook}")
                subprocess.run(["sh", "-lc", cmd], capture_output=True, timeout=10)
                log(f"hook ran: {hook}")
            except Exception as e:
                log(f"hook error: {e}")

    # --- sleep timer --------------------------------------------------------

    def _sleep_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(1)  # check every second so the fade is smooth
            with self._lock:
                dl = self._sleep_deadline
                if dl is None:
                    continue
                remaining = max(0, dl - time.monotonic())
                if remaining <= 0:
                    self._sleep_deadline = None
                    log("sleep timer fired; stopping playback")
                    if self.player:
                        try:
                            self.player.command("stop")
                        except MpvError:
                            pass
                    self._state = "idle"
                    self.q.volume = self._sleep_original_vol
                    self.q.save()
                elif remaining <= 5.0 and self.player:
                    # fade: ramp volume to 0 over the last 5 seconds
                    if self._sleep_original_vol == 0:
                        self._sleep_original_vol = self.q.volume
                    target = int(self._sleep_original_vol * remaining / 5.0)
                    target = max(0, min(self._sleep_original_vol, target))
                    try:
                        self.player.set_property("volume", target)
                    except MpvError:
                        pass

    # --- playlist helpers ---------------------------------------------------

    def _playlist_name(self, name: str) -> str:
        """Validate + normalize a playlist/favorite name."""
        name = "".join(c for c in name.strip() if c.isalnum() or c in "-_").strip()
        if not name:
            raise ValueError("playlist name must not be empty")
        return name

    def _playlist_path(self, name: str):
        return PLAYLIST_DIR / f"{self._playlist_name(name)}.json"

    def _tracks_for_arg(self, arg: str, fast: bool = False) -> list[Track]:
        """One argument -> list of tracks (playlist URL resolves to many).

        With `fast=True`:
        - Direct URL/video-id: returns a placeholder Track immediately so mpv
          can begin loading while metadata is enriched in the background.
        - Text query: resolves normally (yt-dlp search, ~1-2s). The resolver
          timeouts and semaphore limits are already optimized for speed.
        - Playlists: always resolved fully (need yt-dlp to enumerate videos).
        """
        if is_playlist_url(arg):
            return resolve_playlist(arg)
        if fast and _is_url_or_id(arg):
            return [self._placeholder_track(arg)]
        # Text query — resolve fully (fast path via resolver optimizations)
        return [self._cached_resolve(arg)]





    def _placeholder_track(self, arg: str) -> Track:
        self._pending_enrich.add(arg)
        return Track(query=arg, title=self._short_label(arg), url=arg,
                     duration=None, channel="")

    def _short_label(self, arg: str) -> str:
        if "://" in arg:
            m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", arg)
            if m:
                return f"youtube:{m.group(1)}"
            return (arg.split("://", 1)[1] or arg)[:46]
        return arg[:46]

    def _spawn_enrich(self, tracks) -> None:
        """Backfill placeholder tracks with real metadata (background)."""
        for t in tracks:
            if t.url in self._pending_enrich:
                threading.Thread(target=self._enrich_placeholder, args=(t.url,),
                                 daemon=True).start()

    def _enrich_placeholder(self, url: str) -> None:
        try:
            track = self._cached_resolve(url)  # cache-hit if this came from search
        except ResolveError:
            self._pending_enrich.discard(url)
            return
        with self._op_lock:
            with self._lock:
                for t in self.q.tracks:
                    if t.url == url and url in self._pending_enrich:
                        t.query, t.title = track.query, track.title
                        t.channel, t.duration = track.channel or "", track.duration
                        self.q.save()
                        break
                self._pending_enrich.discard(url)
                for h in self._history:
                    if h.get("url") == url:
                        h["title"] = track.title
                        h["channel"] = track.channel or ""
                        h["duration"] = track.duration
                self._save_history()

    # --- command handlers ---------------------------------------------------

    def dispatch(self, req: str) -> dict:
        try:
            msg = json.loads(req)
            verb = msg.get("verb", "")
            arg = msg.get("arg", "")
        except json.JSONDecodeError:
            return {"ok": False, "error": "bad request"}
        handler = cast("Callable[[object], dict] | None", {
            "ping": self._h_ping,
            "play": self._h_play,
            "add": self._h_add,
            "mix": self._h_mix,
            "mood": self._h_mood,
            "radio": self._h_radio,
            "similar": self._h_similar,
            "discover": self._h_discover,
            "dj": self._h_dj,
            "queue": self._h_queue,
            "import": self._h_import,
            "rate": self._h_rate,
            "share": self._h_share,
            "wrapped": self._h_wrapped,
            "doctor": self._h_doctor,
            "search": self._h_search,
            "suggest": self._h_suggest,
            "next": self._h_next,
            "prev": self._h_prev,
            "playindex": self._h_playindex,
            "pause": self._h_pause,
            "resume": self._h_resume,
            "toggle": self._h_toggle,
            "stop": self._h_stop,
            "volume": self._h_volume,
            "speed": self._h_speed,
            "device": self._h_device,
            "download": self._h_download,
            "downloads": self._h_downloads,
            "remove_download": self._h_remove_download,
            "eq": self._h_eq,
            "seek": self._h_seek,
            "remove": self._h_remove,
            "clear": self._h_clear,
            "undo": self._h_undo,
            "move": self._h_move,
            "m3u": self._h_m3u,
            "shuffle": self._h_shuffle,
            "repeat": self._h_repeat,
            "list": self._h_list,
            "status": self._h_status,
            "info": self._h_info,
            "history": self._h_history,
            "stats": self._h_stats,
            "lyrics": self._h_lyrics,
            "art": self._h_art,
            "playlist": self._h_playlist,
            "fav": self._h_fav,
            "favs": self._h_favs,
            "bookmark": self._h_bookmark,
            "bookmarks": self._h_bookmarks,
            "sleep": self._h_sleep,
            "config": self._h_config,
            "remote": self._h_remote,
            "multiroom": self._h_multiroom,
            "quit-daemon": self._h_quit,

        }.get(verb))
        if handler is None:
            return {"ok": False, "error": f"unknown verb {verb!r}"}
        try:
            return handler(arg)
        except ResolveError as e:
            return {"ok": False, "error": str(e)}
        except MpvError as e:
            return {"ok": False, "error": f"player error: {e}"}
        except (ValueError, IndexError, KeyError, TypeError, OSError,
                json.JSONDecodeError) as e:
            return {"ok": False, "error": str(e)}

    def _h_ping(self, _arg: str = "") -> dict:
        return {"ok": True, "data": {"version": __version__, "state": self._state}}

    def _resolve_all(self, args: list[str], fast: bool = False) -> list[Track]:
        """Resolve several queries/URLs into a flat track list (parallel when >1).

        Individual failures are logged and skipped so one bad song doesn't
        abort the rest; only a total failure raises.
        """
        if len(args) == 1:
            return self._tracks_for_arg(args[0], fast=fast)
        results: list[Track] = []
        errors: list[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(self._tracks_for_arg, a, fast=fast) for a in args]
            for fut in concurrent.futures.as_completed(futs):
                try:
                    results.extend(fut.result())
                except ResolveError as e:
                    errors.append(str(e))
        if not results and errors:
            raise ResolveError(errors[0])
        return results

    def _wait_pending_play(self) -> None:
        """Block until any in-flight `play` has applied, so an `add` doesn't
        race a play that cleared the queue (the play owns it until done)."""
        while True:
            with self._lock:
                if self._pending_arg is None:
                    return
            time.sleep(0.05)

    # --- music intelligence (engine adapters) -------------------------------

    def _engine_search(self, seed: str, limit: int = 6) -> list[Track]:
        key = f"engine:{seed}:{limit}"
        data = self._cache_get(self._search_cache, key)
        if data is None:
            results = search(seed, limit=limit)
            data = [t.as_json() for t in results]
            self._cache_put(self._search_cache, key, data)
        return [Track(**d) for d in data]

    def _engine_radio(self, url: str) -> list[Track]:
        return resolve_radio(url)

    def _engine_resolve(self, seed: str) -> Track:
        return self._cached_resolve(seed)

    def _session_ctx(self) -> dict:
        """Preferences + avoid-set derived from history/favorites/queue."""
        avoid = {t.url for t in self.q.tracks}
        avoid |= {h.get("url") for h in self._history[:40] if h.get("url")}
        fav_artists = {f.get("channel", "").lower() for f in self._favs if f.get("channel")}
        top: dict[str, float] = {}
        for h in self._history[:30]:
            ch = (h.get("channel") or "").lower()
            if ch:
                top[ch] = top.get(ch, 0.0) + min(1.5, (h.get("count") or 1) * 0.3)
        return {"avoid": avoid,
                "prefs": {"fav_artists": fav_artists, "top_artists": top}}

    def _play_tracks(self, tracks: list[Track], *, mood: str | None = None,
                     lang: str | None = None, artist: str | None = None) -> None:
        """Replace the queue with `tracks` and start playing (shared by sessions)."""
        with self._op_lock:
            with self._lock:
                if self.q.tracks:
                    self._undo.append(("replace",
                                       [t.as_json() for t in self.q.tracks],
                                       self.q.index))
                    self._undo = self._undo[-20:]
                self._clear_back()
                self._play_gen += 1
                self._pending_arg = None
                self._restore_pos = None
                self._session_mood = mood
                self._session_lang = lang
                self._session_artist = artist
                self.q.tracks = tracks
                self.q.index = 0
                self.q.shuffle = False
                self._load_current_locked()
                if self.player:
                    try:
                        self.player.set_property("pause", False)
                    except Exception:
                        pass
        self._spawn_enrich(tracks)


    def _h_play(self, arg) -> dict:
        args = arg if isinstance(arg, list) else [arg]
        if not args or not all(isinstance(a, str) and a.strip() for a in args):
            return {"ok": False, "error": "empty play request"}
        # natural language: "play something like Frank Ocean", "sad songs",
        # "music for studying" -> route to the music-intelligence sessions.
        if len(args) == 1:
            kind, payload = detect_intent(args[0])
            if kind == "mood":
                return self._h_mood(payload)
            if kind == "radio":
                return self._h_radio(payload)
            if kind == "similar":
                return self._h_similar(payload)
        # Stop immediately and report "loading <arg>" while we resolve, so
        # play feels instant even on a cold (uncached) search. The slow
        # yt-dlp work happens WITHOUT holding _op_lock so transport keys
        # (next/prev/remove/...) stay responsive while we look the song up.
        with self._op_lock:
            with self._lock:
                if self.q.tracks:
                    self._undo.append(("replace",
                                       [t.as_json() for t in self.q.tracks],
                                       self.q.index))
                    self._undo = self._undo[-20:]
                self._clear_back()  # new context; prev now means nothing before this
                self._play_gen += 1
                gen = self._play_gen
                self._session_mood = None  # a plain play leaves any mood session
                self._session_lang = None
                self._session_artist = None
                self.q.tracks = []
                self.q.index = -1
                self.q.shuffle = False
                self.q.position = 0.0
                self._last_pos = 0.0
                self._error_streak = 0
                self._pending_arg = args[0]
                self._restore_pos = None
                self._error = None
                self._state = "loading"
                if self.player:
                    try:
                        self.player.command("stop")
                    except MpvError:
                        pass
                self.q.save()
        try:
            tracks = self._resolve_all(args, fast=True)
        except ResolveError as e:
            with self._op_lock:
                with self._lock:
                    if gen == self._play_gen:
                        self._pending_arg = None
                        self._error = str(e)
                        self._state = "idle"
            return {"ok": False, "error": str(e)}
        if not tracks:
            with self._op_lock:
                with self._lock:
                    if gen == self._play_gen:
                        self._pending_arg = None
                        self._error = "no playable tracks"
                        self._state = "idle"
            return {"ok": False, "error": "no playable tracks"}
        with self._op_lock:
            with self._lock:
                if gen != self._play_gen:  # a newer play superseded this one
                    return {"ok": True, "data": {"title": tracks[0].title,
                                                 "count": len(tracks),
                                                 "superseded": True}}
                self._pending_arg = None
                self.q.tracks = tracks
                self.q.index = 0
                self._load_current_locked()
        self._spawn_enrich(tracks)
        return {"ok": True, "data": {"title": tracks[0].title, "count": len(tracks)}}

    def _h_add(self, arg) -> dict:
        args = arg if isinstance(arg, list) else [arg]
        if not args or not all(isinstance(a, str) and a.strip() for a in args):
            return {"ok": False, "error": "empty add request"}
        self._wait_pending_play()  # don't race a play that cleared the queue
        try:
            tracks = self._resolve_all(args, fast=True)  # URLs start instantly
        except ResolveError as e:
            return {"ok": False, "error": str(e)}
        if not tracks:
            return {"ok": False, "error": "no playable tracks"}
        with self._op_lock:
            with self._lock:
                existing = {t.url for t in self.q.tracks}
                fresh = [t for t in tracks if t.url not in existing]
                skipped = len(tracks) - len(fresh)
                start_now = self.q.index < 0  # empty queue OR finished naturally
                self.q.tracks.extend(fresh)
                if start_now and fresh and self._pending_arg is None:
                    # continue after what's already queued: play the FIRST new track
                    self.q.index = len(self.q.tracks) - len(fresh)
                    self._restore_pos = None
                    self._load_current_locked()
                else:
                    self.q.save()
        self._spawn_enrich(fresh)
        return {"ok": True, "data": {
            "title": fresh[0].title if fresh else tracks[0].title,
            "added": len(fresh), "skipped": skipped,
            "queue_len": len(self.q.tracks)}}

    def _h_mix(self, arg) -> dict:
        """Resolve a playlist URL or search query, then shuffle and play."""
        arg = str(arg).strip()
        if not arg:
            return {"ok": False, "error": "mix needs a query, URL, or playlist"}
        with self._op_lock:
            if is_playlist_url(arg):
                try:
                    tracks = resolve_playlist(arg)
                except ResolveError as e:
                    return {"ok": False, "error": str(e)}
            elif _is_url_or_id(arg):
                tracks = self._tracks_for_arg(arg)  # real resolve; single track, shuffle is a no-op
            else:
                n = int(self.cfg.get("mix_count", 20))
                tracks = search(arg, limit=n)
                if not tracks:
                    return {"ok": False, "error": f"no results for {arg!r}"}
            if not tracks:
                return {"ok": False, "error": "nothing to play"}
            shuffled = shuffle_no_adjacent(tracks, key=lambda t: t.channel)
        self._play_tracks(shuffled)
        return {"ok": True, "data": {"title": shuffled[0].title,
                                     "count": len(shuffled)}}

    def _h_mood(self, arg: str) -> dict:
        """Build and play an intelligent session for a mood.

        Supports `mood <mood> [language] [artist alias]`, e.g. "sad hindi",
        "focus english", "sad punjabi sidhu moose wala".
        """
        mood, lang, artist = parse_mood_arg(arg)
        if mood is None:
            return {"ok": False, "error":
                    f"unknown mood {arg!r} — try: {', '.join(sorted(MOODS))} "
                    "[language] [artist]"}
        ctx = self._session_ctx()
        if artist:
            ctx["prefs"] = {**ctx["prefs"], "artist": artist}
        limit = int(self.cfg.get("mix_count", 16))
        try:
            tracks = build_mood_session(mood, self._engine_search, limit=limit,
                                        avoid=ctx["avoid"], prefs=ctx["prefs"],
                                        lang=lang, artist=artist)
        except Exception as e:
            return {"ok": False, "error": f"could not build a session: {e}"}
        if not tracks:
            return {"ok": False, "error": "could not build a session — network down?"}
        self._play_tracks(tracks, mood=mood, lang=lang, artist=artist)
        return {"ok": True, "data": {"title": tracks[0].title, "count": len(tracks),
                                     "mood": mood, "lang": lang, "artist": artist}}

    def _h_radio(self, arg: str) -> dict:
        """Radio from a seed (artist / song / genre / URL / mood preset)."""
        seed = arg.strip()
        if not seed:
            seed = "lofi"
        resolved_query = resolve_radio_query(seed)
        ctx = self._session_ctx()
        limit = int(self.cfg.get("mix_count", 16))
        try:
            tracks = build_radio_session(resolved_query, self._engine_resolve, self._engine_radio,
                                         self._engine_search, limit=limit,
                                         avoid=ctx["avoid"], prefs=ctx["prefs"])
        except Exception:
            tracks = []
        if not tracks:
            try:
                tracks = self._engine_search(resolved_query)
            except Exception:
                tracks = []
        if not tracks:
            return {"ok": False, "error": f"no results for {seed!r}"}
        self._play_tracks(tracks, mood=seed)
        return {"ok": True, "data": {"title": tracks[0].title, "count": len(tracks),
                                     "seed": seed, "query": resolved_query}}

    def _h_similar(self, arg: str) -> dict:
        """Play tracks related to a song (or the current one if arg empty)."""
        seed = arg.strip()
        if not seed:
            with self._lock:
                now = self.q.current()
            if now is None:
                return {"ok": False, "error": "nothing playing — give a song to compare"}
            seed = now.title
        ctx = self._session_ctx()
        limit = int(self.cfg.get("mix_count", 16))
        try:
            tracks = build_similar_session(seed, self._engine_resolve, self._engine_radio,
                                           self._engine_search, limit=limit,
                                           avoid=ctx["avoid"], prefs=ctx["prefs"])
        except Exception as e:
            return {"ok": False, "error": f"could not build a session: {e}"}
        if not tracks:
            return {"ok": False, "error": f"no related tracks for {seed!r}"}
        self._play_tracks(tracks, mood=None)
        return {"ok": True, "data": {"title": tracks[0].title, "count": len(tracks),
                                     "seed": seed}}

    def _h_discover(self, _arg: str = "") -> dict:
        """Fresh tracks you probably haven't heard (deduped against history)."""
        seen = {h.get("url") for h in self._history if h.get("url")}
        ctx = self._session_ctx()
        limit = int(self.cfg.get("mix_count", 16))
        try:
            tracks = build_discovery_session(self._engine_search, limit=limit,
                                             seen=seen, avoid=ctx["avoid"],
                                             prefs=ctx["prefs"])
        except Exception as e:
            return {"ok": False, "error": f"could not build a session: {e}"}
        if not tracks:
            return {"ok": False, "error": "nothing new to discover right now"}
        self._play_tracks(tracks, mood=None)
        return {"ok": True, "data": {"title": tracks[0].title, "count": len(tracks)}}

    def _h_queue(self, arg) -> dict:
        """`queue add <q> | remove <n> | move <from> <to> | shuffle | clear | smart`."""
        if not isinstance(arg, str):
            arg = " ".join(arg) if isinstance(arg, list) else str(arg)
        parts = arg.strip().split(None, 1)
        action = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if action == "add" and rest:
            return self._h_add(rest.split())
        if action == "remove" and rest:
            return self._h_remove(rest.split()[0])
        if action == "move" and rest:
            return self._h_move(rest)
        if action == "shuffle":
            return self._h_shuffle()
        if action == "clear":
            return self._h_clear()
        if action == "smart":
            self.cfg.set("smart_queue", True)
            with self._lock:
                now = self.q.current()
            if now:
                threading.Thread(target=self._smart_queue_refill,
                                 args=(now.url,), daemon=True).start()
            return {"ok": True, "data": {"smart_queue": True}}
        if action in ("", "status"):
            with self._lock:
                return {"ok": True, "data": {
                    "queue_len": len(self.q.tracks),
                    "index": self.q.index,
                    "smart_queue": bool(self.cfg.get("smart_queue")),
                    "mood": self._session_mood,
                    "tracks": [{"title": t.title, "channel": t.channel, "url": t.url}
                               for t in self.q.tracks]}}
        return {"ok": False, "error":
                "queue: add <q> | remove <n> | move <from> <to> | shuffle | clear | smart"}

    def _h_dj(self, arg: str = "") -> dict:
        """Real DJ mode: beat-matching, seamless crossfading, audio FX & smart queue."""
        arg = (arg or "").strip().lower()
        if arg in ("off", "stop", "disable"):
            with self._lock:
                self._dj_mode = False
                self.cfg.set("dj_mode", False)
                if self.player:
                    try:
                        self.player.set_property("af", "")
                    except Exception:
                        pass
            return {"ok": True, "data": {"dj_mode": False}}

        if arg in ("scratch", "effect", "fx"):
            if self.player:
                try:
                    self.player.set_property("af", "lavfi=[lowpass=f=1200]")
                    threading.Timer(0.8, lambda: self.player and self.player.set_property("af", "")).start()
                except Exception:
                    pass
            return {"ok": True, "data": {"fx": "scratch", "dj_mode": True}}

        if arg in ("bass", "bassdrop", "bass_drop"):
            if self.player:
                try:
                    # Massive bass boost + low-pass — classic drop effect
                    self.player.set_property("af", "lavfi=[equalizer=f=60:width_type=h:width=80:g=12]")
                    threading.Timer(1.5, lambda: self.player and self.player.set_property(
                        "af", "lavfi=[equalizer=f=60:width_type=h:width=50:g=4]")).start()
                except Exception:
                    pass
            return {"ok": True, "data": {"fx": "bass_drop", "dj_mode": True}}

        if arg in ("filter", "filterdrop", "filter_drop"):
            if self.player:
                try:
                    # High-pass filter sweep — strips lows, then opens back up
                    self.player.set_property("af", "lavfi=[highpass=f=800]")
                    threading.Timer(1.2, lambda: self.player and self.player.set_property("af", "")).start()
                except Exception:
                    pass
            return {"ok": True, "data": {"fx": "filter", "dj_mode": True}}

        if arg in ("fade", "fadenext", "fade_next"):
            if self.player:
                try:
                    # Quick volume duck + advance to next track
                    self.player.set_property("volume", 30)
                    def _fade_and_next():
                        import time as _t
                        _t.sleep(0.6)
                        with self._op_lock:
                            with self._lock:
                                self._advance_locked()
                        _t.sleep(0.3)
                        if self.player:
                            try:
                                vol = int(self.cfg.get("volume") or 80)
                                self.player.set_property("volume", vol)
                            except Exception:
                                pass
                    threading.Thread(target=_fade_and_next, daemon=True).start()
                except Exception:
                    pass
            return {"ok": True, "data": {"fx": "fade_next", "dj_mode": True}}



        with self._lock:
            self._dj_mode = True
            self.cfg.set("dj_mode", True)
            self.cfg.set("smart_queue", True)
            self.cfg.set("gapless", True)
            if self.player:
                try:
                    self.player.set_property("af", "lavfi=[equalizer=f=60:width_type=h:width=50:g=4]")
                except Exception:
                    pass

        # Non-blocking: kick off track resolution in background so caller returns instantly
        def _load_dj_tracks() -> None:
            import random as _rand
            moods = sorted(MOODS)
            mood = _rand.choice(moods)
            ctx = self._session_ctx()
            limit = int(self.cfg.get("mix_count", 20))
            try:
                tracks = build_mood_session(mood, self._engine_search, limit=limit,
                                            avoid=ctx["avoid"], prefs=ctx["prefs"])
                if tracks:
                    self._play_tracks(tracks, mood=mood, lang=None, artist=None)
            except Exception:
                pass

        needs_load = not self.q.tracks or self._state in ("idle",)
        if needs_load:
            threading.Thread(target=_load_dj_tracks, daemon=True).start()

        return {
            "ok": True,
            "data": {
                "dj_mode": True,
                "title": self.q.current().title if self.q.current() else "Loading DJ session…",
                "count": len(self.q.tracks),
            },
        }

    def _h_import(self, arg: str) -> dict:
        """Import tracks from an external source (currently: Spotify playlist URL).

        Resolves the playlist to track names, searches YouTube for each, and
        adds the results to the queue. No Spotify auth needed for public lists.
        """
        import re as _re
        import urllib.request as _ur
        parts = (arg or "").strip().split(None, 1)
        source = parts[0].lower() if parts else ""
        url = parts[1] if len(parts) > 1 else ""
        if source != "spotify" or not url:
            return {"ok": False, "error": "usage: import spotify <playlist-url>"}
        # fetch the public playlist page and extract track names
        tracks_added = 0
        try:
            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _ur.urlopen(req, timeout=15) as rp:
                html = rp.read().decode("utf-8", "replace")
            # Spotify pages embed track names in og:title or <meta> tags
            names: set[str] = set()
            for m in _re.finditer(r'<meta[^>]+name="music:song"[^>]+content="([^"]+)"', html):
                nm = m.group(1).strip()
                if nm:
                    names.add(nm)
            if not names:
                # fall back: scrape text that looks like playlist items
                for m in _re.finditer(r'<span[^>]*data-encore-id="type"[^>]*>([^<]+)</span>', html):
                    nm = m.group(1).strip()
                    if nm and len(nm) > 2 and not nm.startswith("<"):
                        names.add(nm)
            n = min(50, len(names))
            for nm in list(names)[:n]:
                try:
                    t = self._cached_resolve(nm)
                except Exception:
                    continue
                with self._lock:
                    self.q.tracks.append(t)
                    tracks_added += 1
                self.q.save()
        except Exception as e:
            return {"ok": False, "error": f"import failed: {e}"}
        return {"ok": True, "data": {"added": tracks_added,
                                     "queue_len": len(self.q.tracks)}}

    def _h_rate(self, arg: str = "") -> dict:
        """Rate the current track 0-5."""
        try:
            n = int(arg.strip())
        except ValueError:
            return {"ok": False, "error": "rate 0-5"}
        n = max(0, min(5, n))
        with self._lock:
            now = self.q.current()
            if now is None:
                return {"ok": False, "error": "nothing playing to rate"}
            for h in self._history:
                if h.get("url") == now.url:
                    h["rating"] = n
                    self._save_history()
                    return {"ok": True, "data": {"rating": n, "title": now.title}}
            self._history.insert(0, {"url": now.url, "title": now.title,
                                     "query": now.query, "channel": now.channel,
                                     "rating": n, "count": 1, "ts": int(time.time())})
            self._history = self._history[:200]
            self._save_history()
        return {"ok": True, "data": {"rating": n, "title": now.title}}

    def _h_share(self, _arg: str = "") -> dict:
        """Generate a shareable YouTube playlist URL from the current queue."""
        with self._lock:
            ids = [t.url.rsplit("=", 1)[-1] for t in self.q.tracks
                   if "youtube" in t.url and "?v=" in t.url or "&v=" in t.url]
        if not ids:
            return {"ok": False, "error": "queue has no YouTube tracks to share"}
        url = "https://www.youtube.com/watch_videos?video_ids=" + ",".join(ids)
        return {"ok": True, "data": {"url": url, "count": len(ids)}}

    def _h_wrapped(self, _arg: str = "") -> dict:
        """Your year / all-time in review from local history."""
        from collections import Counter as _C
        total = sum(h.get("count", 0) for h in self._history)
        artists = _C((h.get("channel") or "").strip() for h in self._history if h.get("channel"))
        moods_raw = _C()
        for h in self._history:
            for mh in detect_intent(h.get("title", "")), detect_intent(h.get("query", "")):
                if mh[0] == "mood":
                    moods_raw[mh[1]] += h.get("count", 0)
        ratings = [h.get("rating") for h in self._history if h.get("rating") is not None]
        avg_rating = sum(ratings) / len(ratings) if ratings else None
        top_track = sorted(self._history, key=lambda h: h.get("count", 0), reverse=True)[:1]
        return {"ok": True, "data": {
            "total_plays": total,
            "total_tracks": len(self._history),
            "top_artists": [{"artist": a, "plays": c} for a, c in artists.most_common(10)],
            "top_moods": [{"mood": m, "plays": c} for m, c in moods_raw.most_common(5)],
            "top_track": top_track[0] if top_track else None,
            "avg_rating": round(avg_rating, 1) if avg_rating else None,
            "skips": sum(h.get("skips", 0) for h in self._history),
            "completed": sum(h.get("completed", 0) for h in self._history),
        }}

    def _h_doctor(self, _arg: str = "") -> dict:
        """Health check: verify deps, config, and network."""
        issues: list[str] = []
        try:
            import shutil as _sh
            from pathlib import Path as _P
            mpv = _sh.which("mpv")
            ytdl = _sh.which("yt-dlp") or str(_P.home() / ".local/bin/yt-dlp")
            if not mpv:
                issues.append("mpv not found — install with: brew install mpv")
            if not ytdl or not _P(ytdl).exists():
                issues.append("yt-dlp not found — install with: brew install yt-dlp")
        except Exception as e:
            issues.append(f"tooling check failed: {e}")
        with self._lock:
            player = self.player is not None
            tracks = len(self.q.tracks)
        try:
            result = search("test", limit=1)
            net_ok = bool(result)
        except Exception:
            net_ok = False
        if not net_ok:
            issues.append("yt-dlp search failed — network or rate-limit issue")
        return {"ok": True, "data": {
            "mpv": bool(mpv),
            "ytdlp": bool(ytdl),
            "player_alive": player,
            "network": net_ok,
            "http_port": int(self.cfg.get("http_port") or 0),
            "queue_len": tracks,
            "cache_tracks": self._cache_max,
            "issues": issues,
        }}

    def _h_search(self, arg: str) -> dict:
        data = self._cache_get(self._search_cache, arg)
        if data is None:
            kind, payload = detect_intent(arg)
            if kind in ("mood", "radio", "similar"):
                # intent search: return a curated set from a quick session
                ctx = self._session_ctx()
                try:
                    if kind == "mood":
                        tracks = build_mood_session(payload, self._engine_search,
                                                    limit=8, avoid=ctx["avoid"])
                    elif kind == "radio":
                        tracks = build_radio_session(payload, self._engine_resolve,
                                                     self._engine_radio, self._engine_search,
                                                     limit=8, avoid=ctx["avoid"])
                    else:
                        tracks = build_similar_session(payload, self._engine_resolve,
                                                       self._engine_radio, self._engine_search,
                                                       limit=8, avoid=ctx["avoid"])
                except Exception:
                    tracks = []
                data = [{"title": t.title, "duration": t.duration,
                         "channel": t.channel, "url": t.url} for t in tracks]
            if data is None:
                results = search(arg, limit=8)
                data = [
                    {"title": t.title, "duration": t.duration,
                     "channel": t.channel, "url": t.url}
                    for t in results
                ]
            self._cache_put(self._search_cache, arg, data)
            # seed the resolve cache so playing a result is instant (no yt-dlp)
            for r in data:
                if r.get("url"):
                    self._cache_put(
                        self._resolve_cache, r["url"],
                        Track(query=r["url"], title=r["title"], url=r["url"],
                              duration=r.get("duration"), channel=r.get("channel") or ""))
        return {"ok": True, "data": {"query": arg, "results": data}}

    def _h_suggest(self, arg: str) -> dict:
        """Return up to 5 history titles/queries matching a prefix (for TUI auto-complete)."""
        needle = str(arg).strip().lower()
        if not needle:
            return {"ok": True, "data": {"suggestions": []}}
        seen: set[str] = set()
        out: list[str] = []
        for h in self._history:
            for key in (h.get("title"), h.get("query")):
                key = str(key)
                if needle in key.lower() and key not in seen:
                    seen.add(key)
                    out.append(key)
                    break
            if len(out) >= 5:
                break
        return {"ok": True, "data": {"suggestions": out}}

    def _h_next(self, _arg: str = "") -> dict:
        with self._op_lock:
            with self._lock:
                n = len(self.q.tracks)
                if n == 0:
                    return {"ok": True, "data": {"title": None}}
                if self.q.index >= n - 1:
                    if self.q.repeat == "all":
                        self._push_back()
                        self.q.index = 0
                    else:
                        self._push_back()
                        self.q.index = -1
                        self._state = "idle"
                        self.q.save()
                        return {"ok": True, "data": {"title": None}}
                else:
                    self._push_back()
                    self.q.index += 1
                self._restore_pos = None  # user is changing tracks; drop crash-resume pos
                self._load_current_locked()
                t = self.q.current()
                return {"ok": True, "data": {"title": t.title if t else None}}

    def _h_prev(self, _arg: str = "") -> dict:
        with self._op_lock:
            with self._lock:
                handle = self.player
                if handle is None or self.q.index < 0:
                    # after the queue ends, prev can still go back into it
                    url = self._back_stack.pop() if self._back_stack else None
                    if url:
                        i = self._index_of(url)
                        if i is not None:
                            self.q.index = i
                            self._restore_pos = None
                            self._load_current_locked()
                            t = self.q.current()
                            return {"ok": True, "data": {"title": t.title if t else None,
                                                         "back": True}}
                    return {"ok": True, "data": {"title": None}}
                pos = handle.get_property("time-pos") or 0.0
                # prefer walking back through what was actually heard
                if self._back_stack:
                    url = self._back_stack.pop()
                    i = self._index_of(url)
                    if i is not None:
                        self.q.index = i
                        self._restore_pos = None
                        self._load_current_locked()
                        t = self.q.current()
                        return {"ok": True, "data": {"title": t.title if t else None,
                                                     "back": True}}
                if pos > 5:
                    handle.command("seek", 0, "absolute")  # restart current track
                    t = self.q.current()
                    return {"ok": True, "data": {"title": t.title if t else None, "restart": True}}
                if self.q.index > 0:
                    self.q.index -= 1
                    self._restore_pos = None
                    self._load_current_locked()
                else:
                    handle.command("seek", 0, "absolute")
                t = self.q.current()
                return {"ok": True, "data": {"title": t.title if t else None}}

    def _h_playindex(self, arg: str) -> dict:
        try:
            i = int(arg) - 1
        except ValueError:
            return {"ok": False, "error": f"bad number {arg!r}"}
        with self._op_lock:
            with self._lock:
                if not (0 <= i < len(self.q.tracks)):
                    return {"ok": False, "error": f"no track #{arg}"}
                self._push_back()
                self.q.index = i
                self._restore_pos = None
                self._load_current_locked()
                t = self.q.current()
                return {"ok": True, "data": {"title": t.title if t else None}}

    def _h_pause(self, _arg: str = "") -> dict:
        with self._lock:
            self._user_paused = True
            if self.player:
                self.player.set_property("pause", True)
                self._state = "paused"
        return {"ok": True}

    def _h_resume(self, _arg: str = "") -> dict:
        with self._lock:
            self._user_paused = False
            if self.player:
                self.player.set_property("pause", False)
                self._state = "playing"
        return {"ok": True}

    def _h_toggle(self, _arg: str = "") -> dict:
        with self._lock:
            if self.player:
                paused = self.player.get_property("pause")
                self._user_paused = not paused
                self.player.set_property("pause", not paused)
                self._state = "paused" if not paused else "playing"
        return {"ok": True}


    def _h_stop(self, _arg: str = "") -> dict:
        with self._lock:
            self._gapless_loaded = None
            self._state = "idle"
            if self.player:
                try:
                    self.player.command("stop")
                except MpvError:
                    pass
        return {"ok": True}

    def _h_volume(self, arg: str) -> dict:
        try:
            delta = int(arg)
        except ValueError:
            return {"ok": False, "error": f"bad volume {arg!r}"}
        with self._lock:
            if arg.startswith(("+", "-")):
                self.q.volume = max(0, min(VOLUME_MAX, self.q.volume + delta))
            else:
                self.q.volume = max(0, min(VOLUME_MAX, delta))
            if self.player:
                self.player.set_property("volume", self.q.volume)
            self.q.save()
        return {"ok": True, "data": {"volume": self.q.volume}}

    def _h_speed(self, arg: str) -> dict:
        try:
            x = float(arg)
        except ValueError:
            return {"ok": False, "error": f"bad speed {arg!r} (e.g. 1.5, 0.8)"}
        if not (0.1 <= x <= 4.0):
            return {"ok": False, "error": "speed must be between 0.1 and 4.0"}
        with self._lock:
            self._speed = x
            self.q.speed = x
            if self.player:
                try:
                    self.player.set_property("speed", x)
                except MpvError as e:
                    return {"ok": False, "error": f"player error: {e}"}
            self.q.save()
        return {"ok": True, "data": {"speed": x}}

    def _h_device(self, arg: str = "") -> dict:
        with self._lock:
            handle = self.player
            if handle is None:
                return {"ok": False, "error": "player not running"}
            devices = handle.get_property("audio-device-list") or []
            dev_list = [{"name": d.get("name"), "description": d.get("description")} for d in devices]
            if not arg:
                return {"ok": True, "data": {
                    "current": handle.get_property("audio-device"),
                    "devices": dev_list,
                }}

            arg_low = arg.strip().lower()
            target_name = arg
            matched_desc = arg
            for d in devices:
                name = str(d.get("name") or "").lower()
                desc = str(d.get("description") or "").lower()
                if arg_low in name or arg_low in desc:
                    target_name = str(d.get("name") or "")
                    matched_desc = str(d.get("description") or "")
                    break

            self.cfg.set("device", target_name)
            try:
                handle.set_property("audio-device", target_name)
            except MpvError as e:
                return {"ok": False, "error": f"player error: {e}"}
            return {"ok": True, "data": {"device": target_name, "description": matched_desc}}

    def _h_multiroom(self, arg) -> dict:
        """Manage multi-room audio speaker nodes (registration, heartbeat, matrix control)."""
        if isinstance(arg, str):
            try:
                msg = json.loads(arg)
            except Exception:
                msg = {"action": "list"}
        elif isinstance(arg, dict):
            msg = arg
        else:
            msg = {"action": "list"}

        action = msg.get("action", "list")
        now_t = time.time()
        with self._lock:
            # clean stale nodes (> 12s without heartbeat)
            stale = [nid for nid, n in self._multiroom_nodes.items() if now_t - n.get("ts", 0) > 12.0]
            for nid in stale:
                self._multiroom_nodes.pop(nid, None)

            if action in ("register", "heartbeat", "update"):
                node_id = str(msg.get("node_id") or f"node_{len(self._multiroom_nodes)+1}")
                entry = self._multiroom_nodes.get(node_id, {})
                entry.update({
                    "node_id": node_id,
                    "name": str(msg.get("name") or entry.get("name") or "Phone Speaker"),
                    "volume": int(msg.get("volume", entry.get("volume", 100))),
                    "channel": str(msg.get("channel") or entry.get("channel") or "stereo"),
                    "muted": bool(msg.get("muted", entry.get("muted", False))),
                    "ts": now_t,
                })
                self._multiroom_nodes[node_id] = entry
                return {"ok": True, "data": {"node": entry, "active_count": len(self._multiroom_nodes)}}

            nodes = list(self._multiroom_nodes.values())
            return {"ok": True, "data": {"nodes": nodes, "active_count": len(nodes)}}


    def _h_download(self, arg: str = "") -> dict:
        target_track = None
        sarg = arg.strip()
        if sarg == "queue":
            with self._lock:
                tracks = list(self.q.tracks)
            if not tracks:
                return {"ok": False, "error": "queue is empty"}
            for t in tracks:
                download_track(t)
            return {"ok": True, "data": {"title": f"{len(tracks)} tracks", "count": len(tracks), "dir": "downloads"}}
        elif not sarg:
            with self._lock:
                target_track = self.q.current()
            if not target_track:
                return {"ok": False, "error": "nothing playing to download"}
        else:
            try:
                tracks = self._resolve_all([sarg])
                if tracks:
                    target_track = tracks[0]
            except Exception as e:
                return {"ok": False, "error": f"download resolve failed: {e}"}

        if not target_track:
            return {"ok": False, "error": "track not found"}

        download_track(target_track)
        return {"ok": True, "data": {"title": target_track.title, "url": target_track.url, "dir": "downloads"}}

    def _h_downloads(self, _arg: str = "") -> dict:
        return {"ok": True, "data": {"downloads": get_downloads_index()}}

    def _h_remove_download(self, arg: str = "") -> dict:
        target_url = arg.strip()
        if not target_url:
            with self._lock:
                cur = self.q.current()
                if cur:
                    target_url = cur.url
        if not target_url:
            return {"ok": False, "error": "no track url specified"}
        removed = remove_download(target_url)
        return {"ok": True, "data": {"url": target_url, "removed": removed}}

    def _h_eq(self, arg: str = "") -> dict:
        sarg = arg.strip().lower()
        with self._lock:
            if not sarg or sarg == "next":
                preset = next_eq_preset(self._eq_preset)
            elif sarg == "off":
                preset = "flat"
            elif sarg in EQ_PRESETS:
                preset = sarg
            else:
                return {
                    "ok": False,
                    "error": f"unknown eq preset '{arg}'. Available: {', '.join(EQ_ORDER)}",
                }
            self._eq_preset = preset
            self.cfg.set("equalizer_preset", preset)
            if self.player:
                try:
                    self.player.set_property("af", format_mpv_eq(preset))
                except Exception as e:
                    log(f"failed setting eq af filter: {e}")
            return {"ok": True, "data": {"preset": preset, "af": format_mpv_eq(preset)}}

    def _h_history(self, _arg: str = "") -> dict:
        with self._lock:
            return {"ok": True, "data": {"history": self._history}}

    def _h_stats(self, _arg: str = "") -> dict:
        with self._lock:
            by_count = sorted(self._history, key=lambda h: h.get("count", 0), reverse=True)[:20]
            artists: dict[str, int] = {}
            skips = 0
            completions = 0
            for h in self._history:
                ch = (h.get("channel") or "").strip()
                if ch:
                    artists[ch] = artists.get(ch, 0) + h.get("count", 0)
                skips += h.get("skips", 0)
                completions += h.get("completed", 0)
            top_artists = sorted(artists.items(), key=lambda kv: kv[1], reverse=True)[:10]
            return {"ok": True, "data": {
                "most_played": by_count,
                "top_artists": [{"artist": a, "plays": c} for a, c in top_artists],
                "total_plays": sum(h.get("count", 0) for h in self._history),
                "skips": skips,
                "completed": completions,
            }}

    def _h_lyrics(self, arg: str = "") -> dict:
        with self._lock:
            now = self.q.current()
            target_track = None
            if arg:
                for t in self.q.tracks:
                    if t.url == arg:
                        target_track = t
                        break
            if not target_track:
                target_track = now
        url = arg or (target_track.url if target_track else "")
        if not url:
            return {"ok": False, "error": "no track playing"}

        # 1. Memory cache hit
        with self._lock:
            cached = self._lyrics.get(url)
            in_flight = self._lyrics_in_flight.get(url)

        if cached is not None:
            if not cached:
                return {"ok": True, "data": {"lines": [], "note": "no lyrics found for this track", "cached": True}}
            return {"ok": True, "data": {"lines": cached, "cached": True}}

        # 2. If an in-flight fetch is ALREADY running (e.g. background prefetch), wait for it
        if in_flight is not None:
            in_flight.wait(timeout=6.0)
            with self._lock:
                cached = self._lyrics.get(url)
            if cached is not None:
                if not cached:
                    return {"ok": True, "data": {"lines": [], "note": "no lyrics found for this track", "cached": True}}
                return {"ok": True, "data": {"lines": cached, "cached": True}}
            return {"ok": True, "data": {"lines": [], "note": "fetching lyrics timed out", "cached": False}}

        # 3. Claim single-flight slot atomically
        ev = threading.Event()
        with self._lock:
            cached = self._lyrics.get(url)
            if cached is not None:
                if not cached:
                    return {"ok": True, "data": {"lines": [], "note": "no lyrics found for this track", "cached": True}}
                return {"ok": True, "data": {"lines": cached, "cached": True}}
            in_flight = self._lyrics_in_flight.get(url)
            if in_flight is not None:
                wait_ev = in_flight
            else:
                self._lyrics_in_flight[url] = ev
                wait_ev = None

        if wait_ev is not None:
            wait_ev.wait(timeout=6.0)
            with self._lock:
                cached = self._lyrics.get(url)
            if cached is not None:
                if not cached:
                    return {"ok": True, "data": {"lines": [], "note": "no lyrics found for this track", "cached": True}}
                return {"ok": True, "data": {"lines": cached, "cached": True}}
            return {"ok": True, "data": {"lines": [], "note": "fetching lyrics timed out", "cached": False}}

        try:
            lines = fetch_lyrics(
                url=url,
                title=getattr(target_track, "title", "") if target_track else "",
                artist=getattr(target_track, "channel", "") or "" if target_track else "",
                duration=getattr(target_track, "duration", None) if target_track else None,
                query=getattr(target_track, "query", "") or "" if target_track else "",
            )
            with self._lock:
                self._lyrics[url] = lines or []
                if len(self._lyrics) > self._cache_max:
                    self._lyrics.pop(next(iter(self._lyrics)))
        except Exception as e:
            return {"ok": False, "error": f"lyrics unavailable: {e}"}
        finally:
            with self._lock:
                self._lyrics_in_flight.pop(url, None)
            ev.set()

        if not lines:
            return {"ok": True, "data": {"lines": [], "note": "no lyrics found for this track", "cached": False}}
        return {"ok": True, "data": {"lines": lines, "cached": False}}

    def _h_art(self, _arg: str = "") -> dict:
        with self._lock:
            now = self.q.current()
            url = now.url if now else None
        if not url:
            return {"ok": False, "error": "nothing playing"}
        cached = self._art_cache.get(url)
        if cached is not None:
            return {"ok": True, "data": {"lines": cached, "cached": True}}
        lines = render_art(url)
        if not lines:
            return {"ok": False, "error": "could not fetch album art"}
        with self._lock:
            self._art_cache[url] = lines
            if len(self._art_cache) > self._cache_max:
                self._art_cache.pop(next(iter(self._art_cache)))  # drop oldest
        return {"ok": True, "data": {"lines": lines, "cached": False}}

    def _h_seek(self, arg: str) -> dict:
        command: tuple
        if arg.startswith(("+", "-")):
            command = ("seek", arg)  # relative
        else:
            try:
                float(arg)
            except ValueError:
                return {"ok": False, "error": f"bad seek {arg!r}"}
            command = ("seek", arg, "absolute")

        # mpv rejects seek while it is still loading the file, so wait for the
        # load to finish. Poll WITHOUT holding the lock, otherwise the event
        # loop can't clear the "loading" state.
        deadline = time.time() + 8
        while True:
            with self._lock:
                player = self.player
                loading = self._state == "loading"
                nothing = self.q.current() is None
            if player is None or nothing:
                return {"ok": False, "error": "nothing playing to seek"}
            if not loading:
                break
            if time.time() >= deadline:
                return {"ok": False, "error": "player is still loading"}
            time.sleep(0.2)

        with self._lock:
            try:
                self.player.command(*command, timeout=5)
                return {"ok": True}
            except MpvError as e:
                return {"ok": False, "error": f"seek failed: {e}"}

    def _h_remove(self, arg: str) -> dict:
        try:
            i = int(arg) - 1  # 1-based in the UI
        except ValueError:
            return {"ok": False, "error": f"bad number {arg!r}"}
        with self._op_lock:
            with self._lock:
                if not (0 <= i < len(self.q.tracks)):
                    return {"ok": False, "error": f"no track #{arg}"}
                self._undo.append(("remove", i, _track_to_dict(self.q.tracks[i])))
                self._undo = self._undo[-20:]
                was_current = i == self.q.index
                self.q.tracks.pop(i)
                if not self.q.tracks:
                    self.q.index = -1
                    self._state = "idle"
                    if self.player:
                        try:
                            self.player.command("stop")
                        except MpvError:
                            pass
                elif was_current:
                    self.q.index = min(i, len(self.q.tracks) - 1)
                    self._load_current_locked()
                elif i < self.q.index:
                    self.q.index -= 1
                self.q.save()
        return {"ok": True}

    def _h_move(self, arg: str) -> dict:
        """Move a queue item from one position to another (1-based)."""
        try:
            frm, to = (int(x) for x in arg.split())
            frm -= 1
            to -= 1
        except (ValueError, AttributeError):
            return {"ok": False, "error": f"bad move {arg!r} (expected 'from to')"}
        with self._op_lock:
            with self._lock:
                n = len(self.q.tracks)
                if not (0 <= frm < n) or not (0 <= to < n):
                    return {"ok": False, "error": f"no track at that position (queue {n})"}
                track = self.q.tracks.pop(frm)
                self.q.tracks.insert(to, track)
                if self.q.index == frm:
                    self.q.index = to
                elif frm < self.q.index <= to:
                    self.q.index -= 1
                elif to <= self.q.index < frm:
                    self.q.index += 1
                self.q.save()
        return {"ok": True, "data": {"title": track.title}}

    def _h_clear(self, _arg: str = "") -> dict:
        with self._op_lock:
            with self._lock:
                if self.q.tracks:
                    self._undo.append(("clear", [t.as_json() for t in self.q.tracks],
                                       self.q.index))
                    self._undo = self._undo[-20:]
                self.q.tracks = []
                self.q.index = -1
                self._state = "idle"
                if self.player:
                    try:
                        self.player.command("stop")
                    except MpvError:
                        pass
                self.q.save()
        return {"ok": True}

    def _h_undo(self, _arg: str = "") -> dict:
        with self._op_lock:
            with self._lock:
                if not self._undo:
                    return {"ok": False, "error": "nothing to undo"}
                entry = self._undo.pop()
                kind = entry[0]
                if kind == "remove":
                    _, i, track = entry
                    i = min(i, len(self.q.tracks))
                    self.q.tracks.insert(i, Track(**track))
                    if self.q.index >= i:
                        self.q.index += 1
                    self.q.save()
                    return {"ok": True, "data": {"undo": f"restored {track['title']}"}}
                if kind == "shuffle":
                    _, tracks = entry
                    self.q.tracks = [Track(**t) for t in tracks]
                    self.q.index = min(self.q.index, max(0, len(self.q.tracks) - 1))
                    self.q.save()
                    return {"ok": True, "data": {"undo": "un-shuffled the queue"}}
                # "clear" and "replace": (kind, tracks, index)
                _, tracks, index = entry
                self.q.tracks = [Track(**t) for t in tracks]
                self.q.index = index
                if index >= 0:
                    self._restore_pos = None
                    self._load_current_locked()
                else:
                    self._state = "idle"
                self.q.save()
                return {"ok": True, "data": {"undo": f"restored {len(tracks)} tracks"}}

    def _h_m3u(self, arg) -> dict:
        if not isinstance(arg, list) or len(arg) < 2:
            return {"ok": False, "error": "m3u needs an action"}
        action = arg[0]
        if action == "export":
            name = arg[1]
            out = arg[2] if len(arg) > 2 else ""
            path = PLAYLIST_DIR / f"{self._playlist_name(name)}.json"
            if not path.exists():
                return {"ok": False, "error": f"playlist {name!r} not found"}
            data = json.loads(path.read_text())
            out_path = out or os.path.expanduser(f"~/Downloads/{name}.m3u")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for t in data:
                    f.write(f"#EXTINF:-1,{t.get('title', '')}\n{t.get('url', '')}\n")
            return {"ok": True, "data": {"file": out_path, "count": len(data)}}
        if action == "import":
            in_path = arg[1]
            nm = self._playlist_name(arg[2] if len(arg) > 2 else
                                     os.path.splitext(os.path.basename(in_path))[0])
            entries, title = [], None
            for line in open(in_path, encoding="utf-8", errors="replace"):
                line = line.strip()
                if line.startswith("#EXTINF"):
                    title = line.split(",", 1)[1] if "," in line else None
                elif line and not line.startswith("#") and "://" in line:
                    entries.append({"query": title or line, "title": title or line,
                                   "url": line, "duration": None, "channel": ""})
            if not entries:
                return {"ok": False, "error": "no URLs found in that .m3u"}
            PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
            p = PLAYLIST_DIR / f"{nm}.json"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(entries, indent=2))
            os.replace(tmp, p)
            return {"ok": True, "data": {"name": nm, "count": len(entries)}}
        return {"ok": False, "error": f"unknown m3u action {action!r}"}

    def _h_shuffle(self, _arg: str = "") -> dict:
        with self._lock:
            self.q.shuffle = not self.q.shuffle
            if self.q.shuffle:
                self._undo.append(("shuffle", [t.as_json() for t in self.q.tracks]))
                self._undo = self._undo[-20:]
                self.q.reshuffle_after_current()
            self.q.save()
        return {"ok": True, "data": {"shuffle": self.q.shuffle}}

    def _h_repeat(self, arg: str) -> dict:
        if arg not in ("all", "one", "off"):
            return {"ok": False, "error": "repeat must be all|one|off"}
        with self._lock:
            self.q.repeat = arg
            self.q.save()
        return {"ok": True, "data": {"repeat": arg}}

    def _h_list(self, arg: str = "") -> dict:
        with self._lock:
            needle = arg.strip().lower()
            tracks = self.q.tracks
            if needle:
                tracks = [t for t in tracks
                          if needle in t.title.lower() or needle in t.query.lower()
                          or needle in t.channel.lower()]
            return {
                "ok": True,
                "data": {
                    "index": self.q.index,
                    "tracks": [
                        {"query": t.query, "title": t.title, "duration": t.duration}
                        for t in tracks
                    ],
                },
            }

    def _h_info(self, _arg: str = "") -> dict:
        with self._lock:
            now = self.q.current()
            if now is None:
                return {"ok": True, "data": {"playing": False}}
            handle = self.player
            pos = handle.get_property("time-pos") if handle else None
            return {
                "ok": True,
                "data": {
                    "playing": True,
                    "title": now.title,
                    "query": now.query,
                    "url": now.url,
                    "channel": now.channel,
                    "duration": now.duration,
                    "position": float(pos) if pos is not None else None,
                    "queue_position": self.q.index + 1,
                    "queue_len": len(self.q.tracks),
                    "volume": self.q.volume,
                    "repeat": self.q.repeat,
                    "shuffle": self.q.shuffle,
                },
            }

    def _is_fav(self, url: str | None) -> bool:
        if not url:
            return False
        return any(f.get("url") == url for f in self._favs)

    def _h_fav(self, _arg: str = "") -> dict:
        with self._lock:
            now = self.q.current()
            if now is None:
                return {"ok": False, "error": "nothing playing to favorite"}
            entry = _track_to_dict(now)
            for i, f in enumerate(self._favs):
                if f.get("url") == entry["url"]:
                    self._favs.pop(i)
                    self._save_favorites()
                    return {"ok": True, "data": {"fav": False, "title": now.title}}
            self._favs.insert(0, entry)
            self._save_favorites()
            return {"ok": True, "data": {"fav": True, "title": now.title}}

    def _h_favs(self, arg: str = "") -> dict:
        if arg == "play":
            with self._op_lock:
                if not self._favs:
                    return {"ok": False, "error": "no favorites yet"}
                with self._lock:
                    self._play_gen += 1
                    self._pending_arg = None
                    self._clear_back()
                    self._restore_pos = None
                    self.q.tracks = [Track(**f) for f in self._favs]
                    self.q.index = 0
                    self.q.shuffle = False
                    self._load_current_locked()
                return {"ok": True, "data": {"count": len(self._favs)}}
        with self._lock:
            now = self.q.current()
            return {"ok": True, "data": {
                "tracks": self._favs,
                "current_is_fav": self._is_fav(now.url if now else None),
            }}

    def _h_bookmark(self, arg: str = "") -> dict:
        """Save the current track + position as a bookmark (arg = optional label)."""
        label = arg.strip()
        with self._lock:
            now = self.q.current()
            if now is None:
                return {"ok": False, "error": "nothing playing to bookmark"}
            pos = self._last_pos
            if not label:
                m, s = divmod(int(pos), 60)
                label = f"{m}:{s:02d}"
            entry = {"url": now.url, "title": now.title, "channel": now.channel,
                     "duration": now.duration, "position": round(pos, 1),
                     "label": label, "ts": int(time.time())}
            # same url+label replaces the older entry
            self._bookmarks = [b for b in self._bookmarks
                               if not (b.get("url") == now.url and b.get("label") == label)]
            self._bookmarks.insert(0, entry)
            self._bookmarks = self._bookmarks[:50]
            self._save_bookmarks()
        return {"ok": True, "data": {"label": label, "title": now.title,
                                     "position": round(pos, 1)}}

    def _h_bookmarks(self, arg: str = "") -> dict:
        """List bookmarks, or `bookmarks play <n>` to jump to one."""
        arg = arg.strip()
        if arg.startswith("play"):
            try:
                n = int(arg.split()[1]) - 1
            except (IndexError, ValueError):
                return {"ok": False, "error": "usage: bookmarks play <n>"}
            with self._op_lock:
                with self._lock:
                    if not (0 <= n < len(self._bookmarks)):
                        return {"ok": False, "error": f"no bookmark #{n + 1}"}
                    b = self._bookmarks[n]
                    track = Track(query=b["url"], title=b["title"], url=b["url"],
                                  duration=b.get("duration"), channel=b.get("channel") or "")
                    if self.q.tracks:
                        self._undo.append(("replace",
                                           [t.as_json() for t in self.q.tracks],
                                           self.q.index))
                        self._undo = self._undo[-20:]
                    self._clear_back()
                    self._play_gen += 1
                    self._pending_arg = None
                    self._restore_pos = float(b.get("position", 0))
                    self.q.tracks = [track]
                    self.q.index = 0
                    self.q.shuffle = False
                    self._load_current_locked()
                return {"ok": True, "data": {"title": b["title"],
                                             "position": b.get("position")}}
        with self._lock:
            return {"ok": True, "data": {"bookmarks": self._bookmarks}}

    def _h_playlist(self, arg) -> dict:
        if not isinstance(arg, list) or len(arg) < 1:
            return {"ok": False, "error": "playlist needs an action"}
        action, name = arg[0], arg[1] if len(arg) > 1 else ""
        if action == "list":
            PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
            names = sorted(p.stem for p in PLAYLIST_DIR.glob("*.json"))
            return {"ok": True, "data": {"playlists": names}}
        try:
            nm = self._playlist_name(name)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        path = PLAYLIST_DIR / f"{nm}.json"
        if action == "save":
            with self._lock:
                if not self.q.tracks:
                    return {"ok": False, "error": "queue is empty — nothing to save"}
                saved = [_track_to_dict(t) for t in self.q.tracks]
            PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(saved, indent=2))
            os.replace(tmp, path)
            return {"ok": True, "data": {"name": nm, "count": len(saved)}}
        if action == "smart":
            mode = name or "recents"
            if mode == "most-played":
                ranked = sorted(self._history, key=lambda h: h.get("count", 0), reverse=True)
            elif mode in ("recents", "recent"):
                ranked = list(self._history)
            elif mode == "recently-added":
                ranked = sorted(self._history, key=lambda h: h.get("ts", 0), reverse=True)
            elif mode.startswith("artist:"):
                want = mode[len("artist:"):].strip().lower()
                ranked = [h for h in self._history
                          if want in (h.get("channel") or "").lower()]
                if not ranked:
                    return {"ok": False, "error": f"no history for artist {want!r}"}
            else:
                return {"ok": False, "error":
                        "smart mode: most-played | recents | recently-added | artist:<name>"}
            if not ranked:
                return {"ok": False, "error": "no history yet"}
            tracks = [Track(query=h.get("query") or h["title"], title=h["title"],
                            url=h["url"], duration=h.get("duration"),
                            channel=h.get("channel") or "")
                      for h in ranked[:50]]
            with self._op_lock:
                with self._lock:
                    self._play_gen += 1
                    self._pending_arg = None
                    self._restore_pos = None
                    self._clear_back()
                    self.q.tracks = tracks
                    self.q.index = 0
                    self.q.shuffle = False
                    self._load_current_locked()
            return {"ok": True, "data": {"name": mode, "count": len(tracks)}}
        if not path.exists():
            return {"ok": False, "error": f"playlist {nm!r} not found"}
        try:
            tracks = [Track(**t) for t in json.loads(path.read_text())]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return {"ok": False, "error": f"playlist {nm!r} is corrupt"}
        if action == "load":
            with self._op_lock:
                with self._lock:
                    self._play_gen += 1
                    self._pending_arg = None
                    self._restore_pos = None
                    self._clear_back()
                    self.q.tracks = tracks
                    self.q.index = 0
                    self.q.shuffle = False
                    self._load_current_locked()
            return {"ok": True, "data": {"name": nm, "count": len(tracks)}}
        if action == "add":
            with self._op_lock:
                with self._lock:
                    existing = {t.url for t in self.q.tracks}
                    fresh = [t for t in tracks if t.url not in existing]
                    skipped = len(tracks) - len(fresh)
                    start_now = self.q.index < 0  # empty OR finished naturally
                    self.q.tracks.extend(fresh)
                    if start_now and fresh and self._pending_arg is None:
                        self.q.index = len(self.q.tracks) - len(fresh)
                        self._restore_pos = None
                        self._load_current_locked()
                    else:
                        self.q.save()
            return {"ok": True, "data": {"name": nm, "count": len(fresh),
                                         "skipped": skipped,
                                         "queue_len": len(self.q.tracks)}}
        if action == "show":
            return {"ok": True, "data": {
                "name": nm,
                "tracks": [{"query": t.query, "title": t.title, "duration": t.duration}
                           for t in tracks],
            }}
        if action == "delete":
            path.unlink()
            return {"ok": True, "data": {"name": nm}}
        return {"ok": False, "error": f"unknown playlist action {action!r}"}

    def _h_sleep(self, arg: str = "") -> dict:
        arg = arg.strip()
        with self._lock:
            if arg in ("", "query"):
                remaining = (max(0, self._sleep_deadline - time.monotonic())
                             if self._sleep_deadline is not None else None)
                return {"ok": True, "data": {"sleep_remaining": remaining}}
            if arg in ("off", "0", "cancel"):
                self._sleep_deadline = None
                return {"ok": True, "data": {"sleep_remaining": None}}
            try:
                minutes = float(arg)
            except ValueError:
                return {"ok": False, "error": f"bad sleep time {arg!r} (minutes)"}
            if minutes <= 0:
                self._sleep_deadline = None
                return {"ok": True, "data": {"sleep_remaining": None}}
            self._sleep_deadline = time.monotonic() + minutes * 60
            return {"ok": True, "data": {"sleep_remaining": minutes * 60}}

    def _h_config(self, arg: str = "") -> dict:
        parts = arg.split(" ", 1)
        key = parts[0].strip()
        if not key:
            return {"ok": False, "error": "config needs a key (e.g. autoplay on)"}
        if len(parts) == 1 or not parts[1].strip():
            return {"ok": True, "data": {"key": key, "value": self.cfg.get(key)}}
        raw = parts[1].strip()
        value: object = raw
        old = self.cfg.get(key)
        if isinstance(old, bool):
            value = raw.lower() in ("1", "true", "yes", "on")
        elif isinstance(old, int):
            try:
                value = int(raw)
            except ValueError:
                return {"ok": False, "error": f"expected an integer for {key}"}
        elif isinstance(old, float):
            try:
                value = float(raw)
            except ValueError:
                return {"ok": False, "error": f"expected a number for {key}"}
        self.cfg.set(key, value)
        return {"ok": True, "data": {"key": key, "value": value}}

    def _h_remote(self, _arg: str = "") -> dict:
        return {"ok": True, "data": {"port": int(self.cfg.get("http_port") or 0)}}

    def _h_status(self, _arg: str = "") -> dict:
        with self._lock:
            handle = self.player
            now = self.q.current()
            pos = handle.get_property("time-pos") if handle else None
            dur = self._props.get("duration")
            if dur is None:
                dur = now.duration if now else None
            paused = self._props.get("pause")
            idle = self._props.get("idle-active")
            vol = self._props.get("volume", self.q.volume)
            state = self._state
            if self._pending_arg is not None:
                state = "loading"
                arg = self._pending_arg
                if len(arg) > 46:
                    arg = arg[:43] + "…"
                title = f"resolving {arg!r}…"
            else:
                title = now.title if now else None
                if state != "loading":
                    if idle is None:
                        idle = now is None
                    if now is None or idle:
                        state = "idle"
                    elif paused is not None:
                        state = "paused" if paused else "playing"
            return {
                "ok": True,
                "data": {
                    "state": state,
                    "title": title,
                    "url": now.url if now else None,
                    "channel": now.channel if now else "",
                    "query": now.query if now else None,
                    "position": float(pos) if pos is not None else 0.0,
                    "duration": float(dur) if dur is not None else None,
                    "volume": int(vol) if vol is not None else self.q.volume,
                    "repeat": self.q.repeat,
                    "shuffle": self.q.shuffle,
                    "current_index": self.q.index,
                    "queue_len": len(self.q.tracks),
                    "queue": [
                        {"query": t.query, "title": t.title, "duration": t.duration,
                         "channel": t.channel, "url": t.url}
                        for t in self.q.tracks
                    ],
                    "sleep_remaining": (max(0, self._sleep_deadline - time.monotonic())
                                        if self._sleep_deadline is not None else None),
                    "speed": self._speed,
                    "mood": self._session_mood,
                    "mood_lang": self._session_lang,
                    "mood_artist": self._session_artist,
                    "smart_queue": bool(self.cfg.get("smart_queue")),
                    "dj_mode": getattr(self, "_dj_mode", False),
                    "fav": self._is_fav(now.url if now else None),
                    "direct_url": self._cached_direct(now.url) if now else None,
                    "eq": getattr(self, "_eq_preset", "flat"),

                    "downloaded": is_downloaded(now.url) if now else False,
                    "error": self._error,
                    "ts": time.time(),
                },
            }

    def _h_quit(self, _arg: str = "") -> dict:
        with self._lock:
            if self.player:
                try:
                    self.player.set_property("pause", True)
                    self.player.command("stop")
                except Exception:
                    pass
            self._state = "idle"
        self._stop.set()
        return {"ok": True}


class CtrlHandler(socketserver.StreamRequestHandler):
    @property
    def _daemon(self) -> Daemon:
        # CtrlServer sets daemon_ref before serving; BaseServer doesn't know it.
        return self.server.daemon_ref  # type: ignore[attr-defined]

    def handle(self) -> None:
        daemon = self._daemon
        while True:
            line = self.rfile.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            resp = daemon.dispatch(text)
            try:
                self.wfile.write((json.dumps(resp) + "\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break  # client gave up and closed; not our problem
            if daemon._stop.is_set():
                break


class CtrlServer(socketserver.ThreadingUnixStreamServer):
    daemon_ref: Daemon = None


def run() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("tune: daemon already running", file=sys.stderr)
        sys.exit(1)

    daemon = Daemon()
    daemon._evq = queue.Queue()
    if os.path.exists(CTRL_SOCK):
        os.unlink(CTRL_SOCK)

    try:
        daemon._spawn_mpv()
    except MpvError as e:
        log(f"failed to start mpv: {e}")
        print(f"tune: {e}", file=sys.stderr)
        sys.exit(1)

    # Auto-resume: if the restored queue has a current track, load it in paused state
    with daemon._lock:
        if daemon.q.current() is not None:
            daemon._restore_pos = daemon.q.position
            daemon._load_current_locked()
            if daemon.player:
                try:
                    daemon.player.set_property("pause", True)
                except Exception:
                    pass
            daemon._state = "paused"

    threading.Thread(target=daemon._watch_mpv, daemon=True).start()
    threading.Thread(target=daemon._event_loop, daemon=True).start()
    threading.Thread(target=daemon._position_loop, daemon=True).start()
    threading.Thread(target=daemon._sleep_loop, daemon=True).start()

    if daemon.cfg.get("media_keys"):
        try:
            from . import mediakeys
            mediakeys.start(_mk_dispatch(daemon))
        except Exception as e:
            log(f"media keys unavailable: {e}")

    http_port = int(daemon.cfg.get("http_port") or 0)
    if http_port:
        try:
            from .remote import start as start_remote
            start_remote(daemon, http_port)
            log(f"remote control at http://localhost:{http_port}")
        except OSError as e:
            log(f"remote control failed to bind :{http_port}: {e}")

    server_cls = type("_Server", (CtrlServer,), {"daemon_ref": daemon})
    server = None
    for _attempt in range(10):  # retry past a stale socket / brief bind race
        if os.path.exists(CTRL_SOCK):
            try:
                os.unlink(CTRL_SOCK)
            except OSError:
                pass
        try:
            server = server_cls(str(CTRL_SOCK), CtrlHandler)
            break
        except OSError:
            time.sleep(0.2)
    if server is None:
        log("could not bind control socket; giving up")
        sys.exit(1)

    def _shutdown(*_args) -> None:
        daemon._stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"daemon ready (tune {__version__}, control socket {CTRL_SOCK})")

    while not daemon._stop.is_set():
        time.sleep(0.2)

    log("daemon shutting down")
    server.shutdown()
    server.server_close()
    if daemon.player:
        try:
            daemon.player.command("quit", timeout=3)
        except MpvError:
            pass
        daemon.player.close()
    with daemon._lock:
        daemon.q.position = daemon._last_pos
        daemon.q.save()
    fcntl.flock(lock_fh, fcntl.LOCK_UN)
    lock_fh.close()
    if os.path.exists(CTRL_SOCK):
        os.unlink(CTRL_SOCK)
