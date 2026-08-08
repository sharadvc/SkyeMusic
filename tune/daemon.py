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
import socket
import socketserver
import subprocess
import sys
import threading
import time

from . import __version__
from .art import render as render_art
from .config import Config
from .lyrics import fetch as fetch_lyrics
from .player import MpvError, MpvHandle
from .queue import (
    CTRL_SOCK, FAVORITES_FILE, HISTORY_FILE, LOCK_FILE, LOG_FILE, MPV_SOCK,
    PLAYLIST_DIR, QUEUE_FILE, VOLUME_MAX, QueueState, Track,
)
from .resolver import (
    ResolveError, is_playlist_url, resolve, resolve_playlist, resolve_radio, search,
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
        "--volume=80",
        f"--volume-max={VOLUME_MAX}",
        "--ytdl-format=bestaudio/best",
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
        self._speed = 1.0
        self._stop = threading.Event()
        self._evq: queue.Queue | None = None
        self._restore_pos: float | None = None  # seek target to apply on file-loaded
        self._pending_arg: str | None = None  # song being resolved for 'play'
        self._props: dict = {}  # mpv properties cached via observe_property
        # resolve/search caches: key -> (timestamp, value); no lock held during yt-dlp
        self._resolve_cache: dict[str, tuple[float, Track]] = {}
        self._search_cache: dict[str, tuple[float, list]] = {}
        self._cache_ttl = 600.0
        self._cache_max = 200
        self._op_lock = threading.Lock()  # serializes queue mutations (play/add/next/...)
        self._play_gen = 0  # bumps on every queue-replacing action; stale plays don't apply
        self._sleep_deadline: float | None = None  # time.monotonic deadline for sleep timer
        self._favs: list[dict] = self._load_favorites()
        self._history: list[dict] = self._load_history()
        self._lyrics: dict[str, list] = {}  # url -> timed lyric lines
        self._art_cache: dict[str, list] = {}  # url -> ANSI art lines
        self._undo: list = []  # ("remove", idx, track_dict) | ("clear", tracks, index)

    # --- mpv management ----------------------------------------------------

    def _spawn_mpv(self) -> None:
        args = mpv_argv(str(MPV_SOCK))
        if self.cfg.get("gapless"):
            args.append("--gapless-audio=yes")
        if self.cfg.get("replaygain"):
            args.append("--replaygain=track")
        dev = self.cfg.get("device")
        if dev:
            args.append(f"--audio-device={dev}")
        handle = MpvHandle(args, on_event=self._enqueue_event)
        handle.start()
        with self._lock:
            self.player = handle
            handle.set_property("volume", self.q.volume)
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
        self._error = None
        self._error_streak = 0
        self._state = "loading"
        try:
            self.player.command("loadfile", track.url, "replace")
        except MpvError as e:
            self._error = str(e)
            self._state = "idle"
        self.q.save()

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
                self.q.index = 0
                if self.q.shuffle:
                    self.q.reshuffle_after_current()  # fresh order each cycle
                self._load_current_locked()
            else:
                last_url = self.q.tracks[-1].url if self.q.tracks else None
                self.q.index = -1
                self._state = "idle"
                self.q.save()
                if self.cfg.get("autoplay") and last_url:
                    threading.Thread(target=self._autoplay, args=(last_url,),
                                     daemon=True).start()
            return
        self.q.index += 1
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
                    if self._restore_pos is not None and self.player:
                        pos, self._restore_pos = self._restore_pos, None
                        if pos > 5:
                            try:
                                self.player.command("seek", pos, "absolute")
                            except MpvError:
                                pass
                    if self._speed != 1.0 and self.player:
                        try:
                            self.player.set_property("speed", self._speed)
                        except MpvError:
                            pass
                    self._on_track_loaded_locked()
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
                self._advance_locked()
            elif reason == "error":
                self._error_streak += 1
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
                if ticks % 5 == 0:
                    self.q.save()

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

    def _on_track_loaded_locked(self) -> None:
        """Record the play, fire the notification + hook. Caller holds _lock."""
        now = self.q.current()
        if now is None:
            return
        # history: bump count / move to front, cap at 200
        for i, h in enumerate(self._history):
            if h.get("url") == now.url:
                h["count"] = h.get("count", 1) + 1
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
        # notification + hook run in a background thread (never block playback)
        title, url = now.title, now.url
        threading.Thread(target=self._notify_track, args=(title, url), daemon=True).start()

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
            time.sleep(5)
            with self._lock:
                if self._sleep_deadline is not None and \
                        time.monotonic() >= self._sleep_deadline:
                    self._sleep_deadline = None
                    log("sleep timer fired; stopping playback")
                    if self.player:
                        try:
                            self.player.command("stop")
                        except MpvError:
                            pass
                    self._state = "idle"

    # --- playlist helpers ---------------------------------------------------

    def _playlist_name(self, name: str) -> str:
        """Validate + normalize a playlist/favorite name."""
        name = "".join(c for c in name.strip() if c.isalnum() or c in "-_").strip()
        if not name:
            raise ValueError("playlist name must not be empty")
        return name

    def _playlist_path(self, name: str):
        return PLAYLIST_DIR / f"{self._playlist_name(name)}.json"

    def _tracks_for_arg(self, arg: str) -> list[Track]:
        """One argument -> list of tracks (playlist URL resolves to many)."""
        if is_playlist_url(arg):
            return resolve_playlist(arg)
        return [self._cached_resolve(arg)]

    # --- command handlers ---------------------------------------------------

    def dispatch(self, req: str) -> dict:
        try:
            msg = json.loads(req)
            verb = msg.get("verb", "")
            arg = msg.get("arg", "")
        except json.JSONDecodeError:
            return {"ok": False, "error": "bad request"}
        handler = {
            "ping": self._h_ping,
            "play": self._h_play,
            "add": self._h_add,
            "search": self._h_search,
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
            "seek": self._h_seek,
            "remove": self._h_remove,
            "clear": self._h_clear,
            "undo": self._h_undo,
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
            "sleep": self._h_sleep,
            "config": self._h_config,
            "remote": self._h_remote,
            "quit-daemon": self._h_quit,
        }.get(verb)
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

    def _resolve_all(self, args: list[str]) -> list[Track]:
        """Resolve several queries/URLs into a flat track list (parallel when >1).

        Individual failures are logged and skipped so one bad song doesn't
        abort the rest; only a total failure raises.
        """
        if len(args) == 1:
            return self._tracks_for_arg(args[0])
        results: list[Track] = []
        errors: list[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(self._tracks_for_arg, a) for a in args]
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

    def _h_play(self, arg) -> dict:
        args = arg if isinstance(arg, list) else [arg]
        if not args or not all(isinstance(a, str) and a.strip() for a in args):
            return {"ok": False, "error": "empty play request"}
        # Stop immediately and report "loading <arg>" while we resolve, so
        # play feels instant even on a cold (uncached) search. The slow
        # yt-dlp work happens WITHOUT holding _op_lock so transport keys
        # (next/prev/remove/...) stay responsive while we look the song up.
        with self._op_lock:
            with self._lock:
                self._play_gen += 1
                gen = self._play_gen
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
            tracks = self._resolve_all(args)
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
        return {"ok": True, "data": {"title": tracks[0].title, "count": len(tracks)}}

    def _h_add(self, arg) -> dict:
        args = arg if isinstance(arg, list) else [arg]
        if not args or not all(isinstance(a, str) and a.strip() for a in args):
            return {"ok": False, "error": "empty add request"}
        self._wait_pending_play()  # don't race a play that cleared the queue
        try:
            tracks = self._resolve_all(args)  # slow part; no _op_lock held
        except ResolveError as e:
            return {"ok": False, "error": str(e)}
        if not tracks:
            return {"ok": False, "error": "no playable tracks"}
        with self._op_lock:
            with self._lock:
                start_now = self.q.index < 0  # empty queue OR finished naturally
                self.q.tracks.extend(tracks)
                if start_now and self._pending_arg is None:
                    self.q.index = 0
                    self._restore_pos = None
                    self._load_current_locked()
                else:
                    self.q.save()
        return {"ok": True, "data": {
            "title": tracks[0].title, "added": len(tracks),
            "queue_len": len(self.q.tracks)}}

    def _h_search(self, arg: str) -> dict:
        data = self._cache_get(self._search_cache, arg)
        if data is None:
            results = search(arg, limit=8)
            data = [
                {"title": t.title, "duration": t.duration,
                 "channel": t.channel, "url": t.url}
                for t in results
            ]
            self._cache_put(self._search_cache, arg, data)
        return {"ok": True, "data": {"query": arg, "results": data}}

    def _h_next(self, _arg: str = "") -> dict:
        with self._op_lock:
            with self._lock:
                n = len(self.q.tracks)
                if n == 0:
                    return {"ok": True, "data": {"title": None}}
                if self.q.index >= n - 1:
                    if self.q.repeat == "all":
                        self.q.index = 0
                    else:
                        self.q.index = -1
                        self._state = "idle"
                        self.q.save()
                        return {"ok": True, "data": {"title": None}}
                else:
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
                    return {"ok": True, "data": {"title": None}}
                pos = handle.get_property("time-pos") or 0.0
                if self.q.index >= 0 and pos > 5:
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
                self.q.index = i
                self._restore_pos = None
                self._load_current_locked()
                t = self.q.current()
                return {"ok": True, "data": {"title": t.title if t else None}}

    def _h_pause(self, _arg: str = "") -> dict:
        with self._lock:
            if self.player:
                self.player.set_property("pause", True)
                self._state = "paused"
        return {"ok": True}

    def _h_resume(self, _arg: str = "") -> dict:
        with self._lock:
            if self.player:
                self.player.set_property("pause", False)
                self._state = "playing"
        return {"ok": True}

    def _h_toggle(self, _arg: str = "") -> dict:
        with self._lock:
            if self.player:
                paused = self.player.get_property("pause")
                self.player.set_property("pause", not paused)
                self._state = "paused" if paused else "playing"
        return {"ok": True}

    def _h_stop(self, _arg: str = "") -> dict:
        with self._lock:
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
            if self.player:
                try:
                    self.player.set_property("speed", x)
                except MpvError as e:
                    return {"ok": False, "error": f"player error: {e}"}
        return {"ok": True, "data": {"speed": x}}

    def _h_device(self, arg: str = "") -> dict:
        with self._lock:
            handle = self.player
            if handle is None:
                return {"ok": False, "error": "player not running"}
            if not arg:
                devices = handle.get_property("audio-device-list") or []
                return {"ok": True, "data": {
                    "current": handle.get_property("audio-device"),
                    "devices": [{"name": d.get("name"), "description": d.get("description")}
                                for d in devices],
                }}
            self.cfg.set("device", arg)  # persists + applies on next spawn
            try:
                handle.set_property("audio-device", arg)
            except MpvError:
                pass  # takes effect on next spawn
            return {"ok": True, "data": {"device": arg}}

    def _h_download(self, arg: str) -> dict:
        try:
            tracks = self._resolve_all([arg])  # read-only; no _op_lock needed
        except ResolveError as e:
            return {"ok": False, "error": str(e)}
        track = tracks[0]
        out_dir = self.cfg.get("download_dir") or os.path.expanduser("~/Downloads/tune")
        os.makedirs(out_dir, exist_ok=True)
        base = re.sub(r"[^\w\- ]+", "_", track.title).strip()[:60] or track.url
        ytdl = shutil.which("yt-dlp") or os.path.expanduser("~/.local/bin/yt-dlp")
        threading.Thread(target=_do_download, args=(ytdl, track.url, out_dir, base),
                         daemon=True).start()
        return {"ok": True, "data": {"title": track.title, "dir": out_dir}}

    def _h_history(self, _arg: str = "") -> dict:
        with self._lock:
            return {"ok": True, "data": {"history": self._history}}

    def _h_stats(self, _arg: str = "") -> dict:
        with self._lock:
            by_count = sorted(self._history, key=lambda h: h.get("count", 0), reverse=True)[:20]
            return {"ok": True, "data": {
                "most_played": by_count,
                "total_plays": sum(h.get("count", 0) for h in self._history),
            }}

    def _h_lyrics(self, arg: str = "") -> dict:
        with self._lock:
            now = self.q.current()
        url = arg or (now.url if now else "")
        if not url:
            return {"ok": False, "error": "no track playing"}
        cached = self._lyrics.get(url)
        if cached is not None:
            return {"ok": True, "data": {"lines": cached, "cached": True}}
        try:
            lines = fetch_lyrics(url)
        except Exception as e:
            return {"ok": False, "error": f"lyrics unavailable: {e}"}
        if not lines:
            return {"ok": True, "data": {"lines": [], "note": "no subtitles available"}}
        with self._lock:
            self._lyrics[url] = lines
            if len(self._lyrics) > self._cache_max:
                self._lyrics.pop(next(iter(self._lyrics)))  # drop oldest
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
                if entry[0] == "remove":
                    _, i, track = entry
                    i = min(i, len(self.q.tracks))
                    self.q.tracks.insert(i, Track(**track))
                    if self.q.index >= i:
                        self.q.index += 1
                    self.q.save()
                    return {"ok": True, "data": {"undo": f"restored {track['title']}"}}
                _, tracks, index = entry
                self.q.tracks = [Track(**t) for t in tracks]
                self.q.index = index
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

    def _h_list(self, _arg: str = "") -> dict:
        with self._lock:
            return {
                "ok": True,
                "data": {
                    "index": self.q.index,
                    "tracks": [
                        {"query": t.query, "title": t.title, "duration": t.duration}
                        for t in self.q.tracks
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
                tracks = [_track_to_dict(t) for t in self.q.tracks]
            PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(tracks, indent=2))
            os.replace(tmp, path)
            return {"ok": True, "data": {"name": nm, "count": len(tracks)}}
        if action == "smart":
            mode = name or "recents"
            if mode == "most-played":
                ranked = sorted(self._history, key=lambda h: h.get("count", 0), reverse=True)
            elif mode in ("recents", "recent"):
                ranked = list(self._history)
            else:
                return {"ok": False, "error": "smart mode must be 'most-played' or 'recents'"}
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
                    self.q.tracks = tracks
                    self.q.index = 0
                    self.q.shuffle = False
                    self._load_current_locked()
            return {"ok": True, "data": {"name": nm, "count": len(tracks)}}
        if action == "add":
            with self._op_lock:
                with self._lock:
                    start_now = self.q.index < 0  # empty OR finished naturally
                    self.q.tracks.extend(tracks)
                    if start_now and self._pending_arg is None:
                        self.q.index = 0
                        self._restore_pos = None
                        self._load_current_locked()
                    else:
                        self.q.save()
            return {"ok": True, "data": {"name": nm, "count": len(tracks),
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
        value: object = parts[1].strip()
        old = self.cfg.get(key)
        if isinstance(old, bool):
            value = value.lower() in ("1", "true", "yes", "on")
        elif isinstance(old, int):
            try:
                value = int(value)
            except ValueError:
                return {"ok": False, "error": f"expected an integer for {key}"}
        elif isinstance(old, float):
            try:
                value = float(value)
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
                    "query": now.query if now else None,
                    "position": float(pos) if pos is not None else 0.0,
                    "duration": float(dur) if dur is not None else None,
                    "volume": int(vol) if vol is not None else self.q.volume,
                    "repeat": self.q.repeat,
                    "shuffle": self.q.shuffle,
                    "current_index": self.q.index,
                    "queue_len": len(self.q.tracks),
                    "queue": [
                        {"query": t.query, "title": t.title, "duration": t.duration}
                        for t in self.q.tracks
                    ],
                    "sleep_remaining": (max(0, self._sleep_deadline - time.monotonic())
                                        if self._sleep_deadline is not None else None),
                    "speed": self._speed,
                    "fav": self._is_fav(now.url if now else None),
                    "error": self._error,
                },
            }

    def _h_quit(self, _arg: str = "") -> dict:
        self._stop.set()
        return {"ok": True}


class CtrlHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        while True:
            line = self.rfile.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            resp = self.server.daemon_ref.dispatch(text)
            try:
                self.wfile.write((json.dumps(resp) + "\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break  # client gave up and closed; not our problem
            if self.server.daemon_ref._stop.is_set():
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

    # Auto-resume: if the restored queue has a current track, load it and
    # resume at the saved position (applied on the file-loaded event).
    with daemon._lock:
        if daemon.q.current() is not None:
            daemon._restore_pos = daemon.q.position
            daemon._load_current_locked()

    threading.Thread(target=daemon._watch_mpv, daemon=True).start()
    threading.Thread(target=daemon._event_loop, daemon=True).start()
    threading.Thread(target=daemon._position_loop, daemon=True).start()
    threading.Thread(target=daemon._sleep_loop, daemon=True).start()

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
