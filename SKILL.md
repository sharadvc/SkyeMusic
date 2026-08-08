---
name: tune
description: Terminal music player — search & stream any song from YouTube (no login), with play/pause, next/prev, volume, seek, queue, shuffle/repeat, CLI and a full-screen TUI.
---

# tune

A personal terminal music player that streams from YouTube via mpv + yt-dlp.
No login, no API key. A background daemon owns mpv and the queue; the CLI and
TUI are thin clients over a local Unix socket.

## When to use

- User wants to "play a song", "play some music", or control playback in the terminal.
- Use the CLI for scripted/remote control; use the bare `tune` TUI for interactive use.

## Commands

```sh
tune search "song name"         # list YouTube results
tune play "song1" "song2"       # play one or several songs
tune play <youtube_playlist_url>  # play a whole YouTube playlist
tune add "song name"            # queue without interrupting
tune pause | resume | toggle | next | prev | stop | playindex 3
tune volume 60 | +5 | -5        # tune speed 1.5
tune seek +30 | -15 | 60
tune list | remove 2 | clear | undo
tune shuffle | repeat all|one|off
tune fav | favs | favs play     # favorites library
tune playlist save|load|add|show|delete <name> | list | smart most-played|recents
tune m3u export <name> | m3u import <file>
tune lyrics | art | share       # karaoke lyrics, album art, clipboard URL
tune history | recents | stats
tune sleep 30 | off             # sleep timer
tune download "song"            # save audio
tune device                     # audio devices
tune config autoplay on | theme sunset | http_port 8765
tune remote                     # phone/HTTP remote URL
tune info | status              # track details / now playing
tune quit                       # stop daemon + player
tune                            # open the TUI (press / to search, l lyrics, a art, : commands)
```

## Notes

- Project root: this directory. Launcher: `bin/tune` (symlink into `~/.local/bin`).
- First run auto-starts the daemon; it auto-respawns mpv after crashes.
- State: `~/.config/tune/queue.json`, log: `~/.config/tune/tune.log`.
