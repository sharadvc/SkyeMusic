# Contributing to tune

tune is a small, dependency-free terminal music player. Thanks for helping out!

## Requirements

- Python 3.10+
- [mpv](https://mpv.io/) and [yt-dlp](https://github.com/yt-dlp/yt-dlp) —
  `brew install mpv yt-dlp`

## Setup

```sh
git clone <your-fork>
cd tune
# run from the tree (no install needed):
./bin/tune play "coldplay yellow"

# or install as a real command:
pip install -e ".[dev]"     # console script `tune`, plus ruff + mypy
```

Optional extras: `pip install -e ".[media]"` adds macOS global media keys
(`pyobjc-framework-Quartz`) — note this also needs Accessibility permission for
your terminal.

## Development loop

```sh
python3 -m unittest discover -s tests   # run the suite
ruff check .                            # lint (fix with: ruff check . --fix)
mypy tune                               # type check
```

## Where things live

| Area | Files |
|---|---|
| daemon (owns mpv + queue, socket server) | `tune/daemon.py` |
| CLI front-end | `tune/cli.py`, `tune/client.py` |
| TUI (curses) | `tune/tui.py` |
| queue model + persistence | `tune/queue.py` |
| yt-dlp lookups / lyrics / art | `tune/resolver.py`, `tune/lyrics.py`, `tune/art.py` |
| optional integrations | `tune/scrobble.py`, `tune/mediakeys.py`, `tune/remote.py` |
| tests | `tests/` (stdlib `unittest`) |

See `CLAUDE.md` for the full architecture and conventions (especially the
**lock ordering** and **no-runtime-deps** rules).

## Guidelines

- Keep the runtime stdlib-only. New integration features should degrade
  gracefully when their optional dependency is absent (see `mediakeys.py`).
- The daemon is long-lived: state must be safe to persist across restarts, and
  every handler must be thread-safe (observe the `_op_lock` → `_lock` order).
- Add a test for any new command/handler in `tests/test_daemon.py` — you don't
  need mpv; construct a `Daemon()` and set `d.player = None`.
- Run `ruff check .` and `mypy tune` before pushing; CI enforces them.

## Committing

Use clear, imperative commit subjects. If you reference an issue, include the
number. Keep changes focused; a feature + its tests in one commit is ideal.
