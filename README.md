# tune

A terminal music player that streams from YouTube. **No login, no API key.**
Search any song by name and control playback like a real player — from any shell.

Built on [mpv](https://github.com/mpv-player/mpv) + [yt-dlp](https://github.com/yt-dlp/yt-dlp).
Pure Python stdlib — zero pip dependencies.

## Requirements

- `mpv` (audio backend): `brew install mpv`
- `yt-dlp` (YouTube lookup): `brew install yt-dlp` (or `pipx install yt-dlp`)

## Install

```sh
chmod +x bin/tune
ln -s "$PWD/bin/tune" ~/.local/bin/tune   # ~/.local/bin should be on PATH
```

The first command auto-starts a background daemon; everything else just talks to it.

## Usage

```sh
tune                          # full-screen player — press / to search from inside it
tune search "song name"       # list YouTube results, then play one
tune play "song1" "song2"     # play one song, or a whole list at once
tune play <youtube_playlist_url>   # play a whole YouTube playlist
tune mix "lo-fi beats"        # search, shuffle, and play immediately
tune add "song"               # queue without interrupting (dedupes repeats)
tune pause | resume | toggle
tune next | prev | stop       # prev walks back through what you actually heard
tune playindex 3              # jump to queue position 3
tune move 2 1                 # move a queue item between positions (1-based)
tune volume 60                # 0-130, or relative: volume +5 / volume -5
tune seek +30                 # relative: +30 / -15, or absolute: 60
tune list [filter]            # show the queue, optionally filtered by text
tune remove 2 | clear
tune shuffle                  # toggle (avoids same-artist back-to-back)
tune repeat all | one | off
tune fav                      # favorite / unfavorite the current track
tune favs                     # list favorites
tune favs play                # play your favorites
tune bookmark [label]         # save the current position in the track
tune bookmarks [n]            # list bookmarks; add a number to jump to one
tune playlist save <name>     # save the current queue as a named playlist
tune playlist load <name> | add <name> | show <name> | delete <name> | list
tune playlist smart most-played | recents | recently-added | artist:<name>
tune sleep 30                 # stop playback after 30 minutes
tune sleep off                # cancel the sleep timer
tune lyrics                   # synced karaoke lyrics for the current track
tune art                      # terminal album art (truecolor)
tune share                    # copy the current track's URL
tune speed 1.5                # playback speed (0.1–4.0, persisted)
tune device                   # list audio devices (device <name> to select)
tune download "song"          # save a song as an audio file
tune download queue           # download the whole current queue
tune history [n]              # recently played; add a number to play that entry
tune stats                    # most-played stats
tune m3u export <name> [file] # export a playlist to .m3u
tune m3u import <file> [name] # import a .m3u as a playlist
tune undo                     # undo remove/clear/play/shuffle
tune config autoplay on       # smart radio: keep playing similar songs at queue end
tune config mix_count 30      # how many search results `tune mix` fetches
tune config smart_queue on    # keep appending related tracks when the queue runs short
tune config resume on         # per-track resume (podcast mode)
tune config intro_skip 20     # skip the first 20s of a never-resumed track
tune config remote_pin 2468   # PIN-lock the phone remote
tune config listenbrainz_token abc  # scrobble to ListenBrainz
tune config theme sunset      # TUI color theme — or press `t` in the TUI to browse all 28
tune remote                   # show the phone/HTTP remote URL
tune info                     # details for the current track
tune status                   # now playing + progress
tune quit                     # stop the daemon and player
```

A URL or bare YouTube video id works anywhere a song name does: `tune play <url>`.

## TUI keys

| Key | Action |
| --- | --- |
| `/` | search YouTube (type a query, enter, pick a result) |
| `space` | play / pause |
| `n` / `p` | next / previous track |
| `↑` / `↓` | select a row in the queue |
| `d` | remove the selected queue row |
| `enter` | jump to the selected queue row |
| `+` / `-` | volume up / down (5) |
| `[` / `]` | slow down / speed up (10%) |
| `←` / `→` | seek back / forward 10s |
| `l` | synced karaoke lyrics pane (elapsed part highlighted) |
| `a` | terminal album art |
| `f` | filter the queue by text (type live, enter keep, esc clear) |
| `:` | command bar (e.g. `:volume 50`, `:play search terms`) |
| `s` | toggle shuffle |
| `r` | cycle repeat: off → all → one |
| `t` | theme picker — ↑/↓ browse (whole UI previews live), enter apply, esc cancel |
| `q` / `Esc` | quit the TUI and stop playback (daemon stays running) |

A live animated equalizer shows below the progress bar — it bounces while
playing, freezes on pause, and lies flat when idle.

In search mode: `enter` plays the highlighted result, `tab` adds it to the queue,
`↑/↓` move, `backspace` edits, `esc` clears the query or goes back. Every other
key types into the search box. Typing a leading `/` or `search ` is optional —
`/search coldplay`, `/ coldplay`, and `coldplay` all search the same thing.

## How it works

- A background **daemon** owns one `mpv` process (audio-only, `--input-ipc-server`) and the
  song queue, persisted to `~/.config/tune/queue.json`.
- Search results and resolved songs are **cached** in the daemon for 10 minutes, so replaying
  a song or re-searching a query is instant (repeated `play`/`search` return in ~0.1 s).
- Tracks finish → the queue auto-advances. If mpv crashes, the daemon respawns it and
  resumes at the last position. If the daemon dies, the next command (or the TUI's
  background poller) restarts it with the queue intact — the TUI never freezes.
- `tune` (CLI) and the TUI are thin clients over a local Unix socket —
  control it from any shell while music keeps playing.

## Config / state

- `~/.config/tune/queue.json` — queue, volume, repeat, shuffle, last position
- `~/.config/tune/tune.log` — daemon log
- `$TMPDIR/tune-ctrl.sock`, `$TMPDIR/tune-mpv.sock` — control + mpv IPC sockets
