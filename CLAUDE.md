# tune — terminal music player (YouTube, no login)

Streams YouTube audio through **mpv** + **yt-dlp** with a full-screen TUI, a CLI,
and a phone remote. **Pure Python stdlib at runtime** — no pip dependencies.
Requires the external binaries `mpv` and `yt-dlp` (Homebrew).

## Architecture

- **daemon** (`tune/daemon.py`) — the only long-lived process. Owns one mpv
  instance (driven over its JSON IPC socket), the queue, history, favorites,
  and all network lookups (yt-dlp). Exposes a Unix control socket
  (`$TMPDIR/tune-ctrl.sock`) where thin clients send one JSON line and read one
  JSON line back. Auto-started on demand by the client; killed with
  `tune quit`.
- **client** (`tune/client.py`) — `send_cmd(verb, arg)` talks to the daemon,
  auto-starting it if needed. `arg` is a JSON value (string for most verbs, a
  list for `play`/`add`/`playlist`/`m3u`).
- **cli** (`tune/cli.py`) — argparse front-end over the socket. `python3 -m
  tune <cmd>` or the `tune` console script (after pip/pipx install).
- **tui** (`tune/tui.py`) — curses UI. A background thread polls `status` every
  ~0.2s; the render loop never blocks on the daemon. Modes: now / search /
  theme / filter / cmd. Themes are 6-role palettes (see `_THEMES`).
- **queue** (`tune/queue.py`) — `Track`, `QueueState`, runtime paths, and the
  artist-aware `shuffle_no_adjacent`. State persists to
  `~/.config/tune/queue.json` (atomic writes).
- **resolver / lyrics / art / scrobble / mediakeys** — yt-dlp metadata lookups,
  karaoke subs, ANSI thumbnail art, ListenBrainz scrobbling, and optional
  macOS media keys.

## Running

```sh
brew install mpv yt-dlp          # runtime deps
./bin/tune                        # TUI
./bin/tune play "coldplay yellow" # CLI (auto-starts the daemon)
./bin/tune remote                 # phone remote URL
```

## Tests / lint

```sh
python3 -m unittest discover -s tests   # no pytest needed
python3 -m pip install ruff mypy        # dev tools
ruff check .
mypy tune
```

## Conventions

- **No runtime pip deps.** Stdlib only. Optional extras (media keys) degrade
  gracefully — see `mediakeys.py` (needs `pyobjc-framework-Quartz`).
- **Lock ordering:** always acquire `self._op_lock` BEFORE `self._lock` (never
  the reverse) to avoid deadlocks. The slow yt-dlp work happens without holding
  either lock. `_play_gen` lets a newer `play` supersede a slower one.
- **Low latency:** direct URLs/video-ids get a placeholder Track and are handed
  to mpv immediately (no yt-dlp wait); the real title comes from mpv's own
  `media-title`, and channel/duration are backfilled in the background
  (`_pending_enrich`). The next track's *direct stream URL* is prefetched during
  the current one (`_direct_cache`) so `next`/advance start in ~0.4s. All
  daemon-side yt-dlp is capped to 2 concurrent calls (`_YTDLP_SEM` in
  `resolver.py`) — YouTube rate-limits parallel lookups and each call can
  balloon to ~40s when throttled.
- **Config** (`tune/config.py`) is a flat dict of DEFAULTS; `_h_config` coerces
  values based on the existing type (bool/int/float/string).
- **The daemon is stateful:** after editing it, `tune quit` then any command to
  restart, or the old process keeps running stale code.
- **Socket protocol:** one JSON request `{"verb": ..., "arg": ...}` + newline;
  one JSON response + newline. `arg` may be a list for multi-item verbs.

## Adding a command

1. Add a `_h_<verb>` method in `daemon.py` and register it in `dispatch`.
2. Add a subparser + `arg` mapping in `cli.py` (and a print branch).
3. If it should appear in the TUI, add a key/mode in `tui.py`.
4. Add a test in `tests/test_daemon.py` (no mpv needed — `player=None`).
