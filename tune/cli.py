"""tune CLI front-end: subcommands that talk to the daemon."""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import __version__
from .client import send_cmd

_STATE_MARK = {"playing": "▶", "paused": "⏸", "loading": "…", "idle": "·"}


def _fmt_time(sec: float | None) -> str:
    if sec is None:
        return "LIVE"
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _print_status(d: dict) -> None:
    state = d.get("state", "idle")
    mark = _STATE_MARK.get(state, "·")
    title = d.get("title")
    if title:
        pos = _fmt_time(d.get("position"))
        dur = _fmt_time(d.get("duration"))
        line = f"{mark} {state:7} {title}   {pos} / {dur}"
    else:
        line = f"{mark} {state:7} (nothing playing)"
    if d.get("error"):
        line += f"   [!] {d['error']}"
    print(line)
    extra = f" · speed {d.get('speed')}×" if d.get("speed") and d.get("speed") != 1 else ""
    mood = d.get("mood")
    if mood:
        refine = " ".join(x for x in (d.get("mood_lang"), d.get("mood_artist")) if x)
        extra += f" · mood {mood}" + (f" · {refine}" if refine else "")
    print(f"    volume {d.get('volume')} · repeat {d.get('repeat')} · "
          f"shuffle {'on' if d.get('shuffle') else 'off'} · queue {d.get('queue_len')}{extra}")


def _print_list(d: dict) -> None:
    idx = d.get("index", -1)
    tracks = d.get("tracks", [])
    if not tracks:
        print("queue is empty")
        return
    for i, t in enumerate(tracks, 1):
        mark = "▶" if i - 1 == idx else " "
        dur = _fmt_time(t.get("duration"))
        print(f"{mark} {i:3}  {t['title']}  [{dur}]")
    print(f"\n{len(tracks)} track(s) · current #{idx + 1 if idx >= 0 else '-'}")


def _print_search(d: dict) -> None:
    results = d.get("results", [])
    query = d.get("query", "")
    if not results:
        print(f"no results for {query!r}")
        return
    print(f"results for {query!r}:\n")
    for i, r in enumerate(results, 1):
        dur = _fmt_time(r.get("duration"))
        channel = r.get("channel") or ""
        print(f"{i:2}.  {r['title']}  [{dur}]  {channel}")
    print("\nplay one with:  tune play \"<url from list>\"")


def _print_info(d: dict) -> None:
    if not d.get("playing"):
        print("nothing playing")
        return
    print(f"▶ {d['title']}")
    print(f"   channel:  {d.get('channel') or '—'}")
    print(f"   url:      {d.get('url')}")
    print(f"   duration: {_fmt_time(d.get('duration'))}")
    pos = d.get("position")
    print(f"   position: {'—' if pos is None else _fmt_time(pos)}")
    print(f"   queue:    #{d.get('queue_position')} of {d.get('queue_len')}")
    print(f"   volume:   {d.get('volume')} · repeat: {d.get('repeat')} · "
          f"shuffle: {'on' if d.get('shuffle') else 'off'}")


def _print_bookmarks(d: dict) -> None:
    marks = d.get("bookmarks") or []
    if not marks:
        print("no bookmarks yet — tune bookmark while a track plays")
        return
    print("bookmarks:")
    for i, b in enumerate(marks, 1):
        print(f"{i:2}.  [{_fmt_time(b.get('position'))}]  {b.get('title')}  ({b.get('label')})")
    print("\njump to one with:  tune bookmarks <n>")


def _print_favs(d: dict, action: str) -> None:
    if action == "play":
        print(f"▶ playing {d.get('count', 0)} favorites")
        return
    tracks = d.get("tracks", [])
    if not tracks:
        print("no favorites yet — tune fav while a song plays")
        return
    mark = "  ♥ now playing" if d.get("current_is_fav") else ""
    print(f"favorites ({len(tracks)}){mark}")
    for i, t in enumerate(tracks, 1):
        print(f"{i:2}.  {t['title']}  [{_fmt_time(t.get('duration'))}]")
    print("\nplay them with:  tune favs play")


def _print_playlist(d: dict, action: str) -> None:
    if action == "list":
        names = d.get("playlists", [])
        if not names:
            print("no saved playlists yet — tune playlist save <name>")
            return
        print("saved playlists:")
        for n in names:
            print(f"  {n}")
        return
    name = d.get("name", "")
    if action == "save":
        print(f"✓ saved '{name}' ({d.get('count')} tracks)")
    elif action == "load":
        print(f"▶ loaded '{name}' ({d.get('count')} tracks)")
    elif action == "add":
        print(f"+ added {d.get('count')} from '{name}'  (queue: {d.get('queue_len')})")
    elif action == "delete":
        print(f"✗ deleted '{name}'")
    elif action == "smart":
        print(f"▶ smart playlist '{name}' ({d.get('count')} tracks)")
    elif action == "show":
        tracks = d.get("tracks", [])
        print(f"playlist '{name}' ({len(tracks)} tracks):")
        for i, t in enumerate(tracks, 1):
            print(f"{i:2}.  {t['title']}  [{_fmt_time(t.get('duration'))}]")


def _print_sleep(d: dict) -> None:
    rem = d.get("sleep_remaining")
    if rem is None:
        print("sleep timer: off")
    else:
        m, s = divmod(int(rem), 60)
        print(f"sleep timer: {m}:{s:02d} remaining")


def _print_devices(d: dict) -> None:
    devices = d.get("devices") or []
    if d.get("device") is not None:
        print(f"audio device: {d.get('device')}")
    if not devices:
        return
    print("audio devices:")
    for dev in devices:
        mark = "▶" if dev.get("name") == d.get("current") else " "
        print(f"{mark}  {dev.get('name')}   {dev.get('description')}")
    print("\nselect one with:  tune device <name>")


def _print_history(d: dict) -> None:
    hist = d.get("history") or []
    if not hist:
        print("no history yet — play something")
        return
    for i, h in enumerate(hist[:20], 1):
        cnt = h.get("count", 1)
        print(f"{i:2}.  {h['title']}  [{cnt}×]")


def _print_stats(d: dict) -> None:
    most = d.get("most_played") or []
    print(f"total plays: {d.get('total_plays', 0)}")
    if not most:
        print("no plays recorded yet")
        return
    print("\nmost played:")
    for i, h in enumerate(most[:10], 1):
        print(f"{i:2}.  {h['title']}  [{h.get('count', 1)}×]")


def _print_lyrics(d: dict) -> None:
    lines = d.get("lines") or []
    if not lines:
        print(d.get("note") or "no lyrics available")
        return
    for ln in lines:
        print(ln["text"])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="skye", description="Skye Player — terminal music player (YouTube, no login)")
    p.add_argument("--version", action="version", version=f"tune {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("daemon", help="run the background player daemon (internal)")
    sub.add_parser("play").add_argument("query", nargs="+", help="song(s) to search & play now")
    sub.add_parser("add").add_argument("query", nargs="+", help="song(s) to queue (keep playing)")
    sub.add_parser("mix", help="shuffle a playlist or search + play immediately").add_argument(
        "query", nargs="+", help="YouTube playlist URL, search query, or video URL")
    sub.add_parser("mood", help="play an intelligent session for a mood").add_argument(
        "mood", nargs="+",
        help="<mood> [language] [artist] — e.g. 'sad hindi', 'focus english', 'sad punjabi sidhu'")
    sub.add_parser("radio", help="radio from an artist, song, genre, or URL").add_argument(
        "seed", nargs="+")
    sub.add_parser("similar", help="play tracks like a song (default: current)").add_argument(
        "song", nargs="*", default="")
    sub.add_parser("discover", help="play fresh tracks you haven't heard")
    q = sub.add_parser("queue", help="smart queue control")
    q.add_argument("action", nargs="?", default="status",
                   help="add | remove | move | shuffle | clear | smart")
    q.add_argument("args", nargs="*", help="action arguments")
    sr = sub.add_parser("search", help="search YouTube (or --source soundcloud) and list results")
    sr.add_argument("--source", default="", help="source: youtube | soundcloud")
    sr.add_argument("query")
    sub.add_parser("pause", help="pause playback")
    sub.add_parser("resume", help="resume playback")
    sub.add_parser("toggle", help="play/pause toggle")
    sub.add_parser("next", help="play next track")
    sub.add_parser("prev", help="play previous track")
    sub.add_parser("stop", help="stop playback (queue kept)")
    sub.add_parser("volume").add_argument("amount", help="0-130, or +5 / -5")
    sub.add_parser("speed").add_argument("x", help="playback speed, e.g. 1.5 / 0.8 / 1")
    sub.add_parser("device").add_argument("name", nargs="?", default="",
                                          help="audio device name (omit to list)")
    sub.add_parser("download", help="download a song or current track as audio").add_argument("song", nargs="?", default="")
    sub.add_parser("downloads", help="list offline downloaded audio tracks")
    sub.add_parser("eq", help="cycle or set 10-band equalizer preset (flat, bass, bass_extreme, vocal, acoustic, cyberpunk, rock, pop)").add_argument("preset", nargs="?", default="next")
    sub.add_parser("seek").add_argument("amount", help="e.g. +30 / -15 / 60")
    sub.add_parser("remove").add_argument("n", type=int, help="queue position (1-based)")
    sub.add_parser("playindex").add_argument("n", type=int, help="jump to queue position (1-based)")
    mv = sub.add_parser("move", help="move a queue item between positions (1-based)")
    mv.add_argument("from")
    mv.add_argument("to")
    sub.add_parser("clear", help="empty the queue")
    sub.add_parser("undo", help="undo the last remove/clear")
    m3u = sub.add_parser("m3u", help="M3U playlist import/export")
    m3u_sub = m3u.add_subparsers(dest="m3u_action", required=True)
    m3u_export = m3u_sub.add_parser("export")
    m3u_export.add_argument("name")
    m3u_export.add_argument("file", nargs="?", default="")
    m3u_import = m3u_sub.add_parser("import")
    m3u_import.add_argument("file")
    m3u_import.add_argument("name", nargs="?", default="")
    sub.add_parser("shuffle", help="toggle shuffle")
    sub.add_parser("repeat").add_argument("mode", choices=["all", "one", "off"])
    sub.add_parser("list", help="show the queue").add_argument(
        "filter", nargs="?", default="", help="only show matching tracks")
    sub.add_parser("status", help="show what's playing")
    sub.add_parser("info", help="show details for the current track")
    sub.add_parser("history", help="recently played tracks").add_argument(
        "n", nargs="?", type=int, default=None, help="play the nth entry")
    sub.add_parser("recents", help="recently played (alias for history)").add_argument(
        "n", nargs="?", type=int, default=None, help="play the nth entry")
    sub.add_parser("stats", help="most-played stats")
    sub.add_parser("lyrics", help="show synced lyrics for the current track")
    sub.add_parser("art", help="show terminal album art for the current track")
    sub.add_parser("share", help="copy the current track's URL, or --queue for the whole queue").add_argument(
        "--queue", action="store_true", help="share the full queue as a playlist URL")
    sub.add_parser("dj", help="start a DJ session (random mood + crossfade + smart queue)")
    sub.add_parser("import").add_argument("arg", nargs="+", help="import spotify <playlist-url>")
    sub.add_parser("rate").add_argument("n", type=int, choices=range(6), help="rate the current track 0-5")
    sub.add_parser("party").add_argument("action", nargs="?", default="start", help="start | stop")
    sub.add_parser("wrapped", help="your all-time listening in review")
    sub.add_parser("doctor", help="health check (mpv, yt-dlp, network)")
    sub.add_parser("remote", help="show the phone/HTTP remote URL")
    pl = sub.add_parser("playlist", help="manage named playlists")
    pl.add_argument("action",
                    choices=["save", "load", "add", "show", "delete", "list", "smart"])
    pl.add_argument("name", nargs="?", default="", help="playlist name, or smart mode")
    sub.add_parser("fav", help="favorite / unfavorite the current track")
    favs = sub.add_parser("favs", help="show favorites")
    favs.add_argument("action", nargs="?", choices=["play"], default="", help="play favorites")
    sub.add_parser("bookmark", help="bookmark the current position in the track").add_argument(
        "label", nargs="?", default="", help="optional label")
    sub.add_parser("bookmarks", help="list bookmarks").add_argument(
        "n", nargs="?", type=int, default=None, help="jump to bookmark <n>")
    sub.add_parser("sleep").add_argument("minutes", nargs="?", default="",
                                         help="minutes, or 'off' to cancel")
    sub.add_parser("config").add_argument("key_value", nargs="+",
                                          help="config key, or 'key value' (e.g. autoplay on)")
    sub.add_parser("quit", help="stop the daemon and player")
    return p


def run(argv: list[str]) -> int:
    p = build_parser()
    args = p.parse_args(argv)

    if args.cmd is None:
        from .tui import run as tui_run
        tui_run()
        return 0
    if args.cmd == "daemon":
        from .daemon import run as daemon_run
        daemon_run()
        return 0
    if args.cmd == "share":
        try:
            if args.queue:
                resp = send_cmd("share")
                data = resp.get("data") or {}
                url = data.get("url")
                if not url:
                    print("tune: queue has no YouTube tracks", file=sys.stderr)
                    return 1
                subprocess.run(["pbcopy"], input=url.encode())
                print(f"✓ copied {data.get('count')} tracks → {url}")
            else:
                resp = send_cmd("info")
                url = (resp.get("data") or {}).get("url") if resp.get("ok") else None
                if not url:
                    print("tune: nothing playing to share", file=sys.stderr)
                    return 1
                subprocess.run(["pbcopy"], input=url.encode())
                print(f"✓ copied {url}")
        except Exception as e:
            print(f"tune: {e}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "remote":
        try:
            resp = send_cmd("remote")
            port = (resp.get("data") or {}).get("port")
            if not port:
                print("tune: remote control is disabled (config http_port 0)", file=sys.stderr)
                return 1
            print(f"remote control:\n  http://localhost:{port}")
            ip = subprocess.run(["ipconfig", "getifaddr", "en0"],
                                capture_output=True, text=True).stdout.strip()
            if ip:
                print(f"  http://{ip}:{port}   (phone on the same Wi-Fi)")
        except Exception as e:
            print(f"tune: {e}", file=sys.stderr)
            return 1
        return 0

    verb = args.cmd
    arg: object = ""
    if verb == "play":
        arg = args.query  # list of songs
    elif verb == "add":
        arg = args.query  # list of songs
    elif verb == "mix":
        arg = " ".join(args.query)  # one search query or URL
    elif verb == "mood":
        arg = " ".join(args.mood)
    elif verb == "radio":
        arg = " ".join(args.seed)
    elif verb == "similar":
        arg = " ".join(args.song)
    elif verb == "discover":
        arg = ""
    elif verb == "queue":
        arg = " ".join([args.action] + list(args.args))
    elif verb == "search":
        src = args.source
        arg = args.query
        if src == "soundcloud":
            arg = f"scsearch:{args.query}"
        elif src:
            arg = f"{src}search:{args.query}"
    elif verb == "dj":
        arg = ""
    elif verb == "import":
        arg = " ".join(args.arg)
    elif verb == "rate":
        arg = str(args.n)
    elif verb == "party":
        arg = args.action
    elif verb == "wrapped":
        arg = ""
    elif verb == "doctor":
        arg = ""
    elif verb == "playlist":
        arg = [args.action, args.name]
    elif verb == "recents":
        verb = "history"
    elif verb == "favs":
        arg = args.action  # "" or "play"
    elif verb == "bookmark":
        arg = args.label
    elif verb == "bookmarks":
        arg = f"play {args.n}" if args.n else ""
    elif verb == "sleep":
        arg = args.minutes  # "" / "30" / "off"
    elif verb == "config":
        arg = " ".join(args.key_value)
    elif verb == "volume":
        arg = args.amount
    elif verb == "speed":
        arg = args.x
    elif verb == "device":
        arg = args.name
    elif verb == "download":
        arg = args.song
    elif verb == "downloads":
        arg = ""
    elif verb == "eq":
        arg = args.preset
    elif verb == "seek":
        arg = args.amount
    elif verb in ("remove", "playindex"):
        arg = str(args.n)
    elif verb == "move":
        arg = f"{getattr(args, 'from')} {args.to}"
    elif verb == "list":
        arg = args.filter
    elif verb == "m3u":
        if args.m3u_action == "export":
            arg = ["export", args.name, args.file]
        else:
            arg = ["import", args.file, args.name]
    elif verb == "repeat":
        arg = args.mode
    elif verb == "quit":
        verb = "quit-daemon"

    try:
        if verb in ("play", "add", "mix"):
            names = ", ".join(arg) if isinstance(arg, list) else str(arg)
            print(f"… resolving {names}…", file=sys.stderr, flush=True)
        elif verb == "search":
            print(f"… searching {arg!r}…", file=sys.stderr, flush=True)
        resp = send_cmd(verb, arg)
    except Exception as e:
        print(f"tune: {e}", file=sys.stderr)
        return 1
    if not resp.get("ok"):
        print(f"tune: {resp.get('error', 'unknown error')}", file=sys.stderr)
        return 1

    data = resp.get("data") or {}
    if verb == "history" and args.n:
        hist = data.get("history") or []
        n = args.n
        if not (1 <= n <= len(hist)):
            print(f"tune: no history entry #{n}", file=sys.stderr)
            return 1
        entry = hist[n - 1]
        try:
            resp2 = send_cmd("play", entry["url"])
        except Exception as e:
            print(f"tune: {e}", file=sys.stderr)
            return 1
        if not resp2.get("ok"):
            print(f"tune: {resp2.get('error', 'unknown error')}", file=sys.stderr)
            return 1
        print(f"▶ {entry['title']}")
        return 0
    if verb == "status":
        _print_status(data)
    elif verb == "list":
        _print_list(data)
    elif verb == "search":
        _print_search(data)
    elif verb == "info":
        _print_info(data)
    elif verb == "playlist":
        _print_playlist(data, args.action)
    elif verb == "favs":
        _print_favs(data, args.action)
    elif verb == "sleep":
        _print_sleep(data)
    elif verb == "config":
        print(f"{data.get('key')} = {data.get('value')}")
    elif verb == "undo":
        print(f"↩ {data.get('undo')}")
    elif verb == "m3u":
        if args.m3u_action == "export":
            print(f"✓ exported {data.get('count')} tracks → {data.get('file')}")
        else:
            print(f"✓ imported {data.get('count')} tracks as '{data.get('name')}'")
    elif verb == "play":
        extra = f"  (+{data.get('count') - 1} more queued)" if (data.get("count") or 1) > 1 else ""
        print(f"▶ {data.get('title')}{extra}")
    elif verb == "mix":
        cnt = (data.get("count") or 1) - 1
        extra = f"  (+{cnt} more shuffled)" if cnt > 0 else ""
        print(f"🎲 {data.get('title')}{extra}")
    elif verb == "dj":
        cnt = (data.get("count") or 1) - 1
        print(f"🎧⚡ DJ: {data.get('mood')} · {data.get('title')}  (+{cnt} more)")
    elif verb == "mood":
        cnt = (data.get("count") or 1) - 1
        refine = " ".join(x for x in (data.get("lang"), data.get("artist")) if x)
        label = f"{data.get('mood')} · {refine}" if refine else data.get("mood")
        print(f"🎧 {label} · {data.get('title')}  (+{cnt} more)")
    elif verb == "download":
        print(f"✦ Downloader queued track: {data.get('title')} → {data.get('dir') or 'downloads'}")
    elif verb == "downloads":
        downloads = data.get("downloads") or {}
        if not downloads:
            print("no offline downloads yet — use 'tune download' to save songs offline")
        else:
            print(f"✦ Offline Downloads ({len(downloads)}):")
            for i, (url, meta) in enumerate(downloads.items(), 1):
                title = meta.get("title") or url
                size_mb = meta.get("size", 0) / (1024 * 1024)
                print(f"{i:2}.  {title}  [{size_mb:.1f} MB]")
    elif verb == "eq":
        preset = data.get("preset", "flat").upper()
        af = data.get("af", "")
        print(f"✦ Equalizer: {preset} ({af})")
    elif verb == "similar":
        cnt = (data.get("count") or 1) - 1
        print(f"🔀 like {data.get('seed')}: {data.get('title')}  (+{cnt} more)")
    elif verb == "discover":
        cnt = (data.get("count") or 1) - 1
        print(f"✨ discover: {data.get('title')}  (+{cnt} more)")
    elif verb == "rate":
        print(f"{'⭐' * data.get('rating', 0)}  {data.get('title')}")
    elif verb == "party":
        if data.get("party"):
            print(f"🎉 party started — token: {data.get('token')}")
        else:
            print("party stopped")
    elif verb == "import":
        print(f"⇣ imported {data.get('added')} tracks  (queue: {data.get('queue_len')})")
    elif verb == "wrapped":
        print("🎁 Skye Player wrapped:")
        print(f"    total plays: {data.get('total_plays')} · tracks: {data.get('total_tracks')}")
        print(f"    skips: {data.get('skips')} · completed: {data.get('completed')}")
        if data.get('avg_rating'):
            print(f"    avg rating: {data.get('avg_rating')} / 5")
        print(f"    top artists: {', '.join(a['artist'] for a in (data.get('top_artists') or [])[:5])}")
        if data.get('top_track'):
            print(f"    most played: {data['top_track']['title']} ({data['top_track']['count']}×)")
    elif verb == "doctor":
        issues = data.get("issues") or []
        print("🏥 Skye Player health check")
        print(f"    mpv: {'✓' if data.get('mpv') else '✗ missing'}")
        print(f"    yt-dlp: {'✓' if data.get('ytdlp') else '✗ missing'}")
        print(f"    player: {'✓ running' if data.get('player_alive') else '… not spawned'}")
        print(f"    network: {'✓ ok' if data.get('network') else '✗ may be down/rate-limited'}")
        if issues:
            for i in issues:
                print(f"    [!] {i}")
    elif verb == "queue":
        if data.get("tracks") is not None:
            mood = f" · mood {data.get('mood')}" if data.get("mood") else ""
            print(f"queue {data.get('queue_len')} · index {data.get('index')} · "
                  f"smart {'on' if data.get('smart_queue') else 'off'}{mood}")
        else:
            print("queue ok")
    elif verb == "add":
        added = data.get("added", 0)
        skipped = data.get("skipped", 0)
        extra = f"  (skipped {skipped} dup)" if skipped else ""
        print(f"+ queued {added}: {data.get('title')}  (queue: {data.get('queue_len')}){extra}")
    elif verb == "move":
        print(f"↕ moved: {data.get('title')}")
    elif verb == "fav":
        print(f"{'♥' if data.get('fav') else '♡'} {data.get('title')}")
    elif verb == "bookmark":
        print(f"✓ bookmarked '{data.get('label')}' at {_fmt_time(data.get('position'))} — {data.get('title')}")
    elif verb == "bookmarks":
        if data.get("bookmarks") is not None:
            _print_bookmarks(data)
        else:
            print(f"▶ {data.get('title')} at {_fmt_time(data.get('position'))}")
    elif verb == "volume":
        print(f"volume {data.get('volume')}")
    elif verb == "speed":
        print(f"speed {data.get('speed')}×")
    elif verb == "device":
        _print_devices(data)
    elif verb == "download":
        print(f"⤓ downloading {data.get('title')} → {data.get('dir')}")
    elif verb == "history":
        _print_history(data)
    elif verb == "stats":
        _print_stats(data)
    elif verb == "lyrics":
        _print_lyrics(data)
    elif verb == "art":
        for ln in data.get("lines") or []:
            print(ln)
    elif verb == "repeat":
        print(f"repeat {data.get('repeat')}")
    elif verb == "shuffle":
        print(f"shuffle {'on' if data.get('shuffle') else 'off'}")
    elif verb in ("next", "playindex"):
        print(f"▶ {data.get('title') or '(end of queue)'}")
    elif verb == "prev":
        print(f"▶ {data.get('title') or '(nothing)'}")
    return 0


def main() -> int:
    """Console-script entry point (`tune` after pip/pipx install)."""
    return run(sys.argv[1:])
