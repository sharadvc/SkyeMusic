# 🎵 Skye Music Player (`SkyeMusic`)

<div align="center">

![Skye Music Player TUI Demo](assets/tui-demo.png?v=2)

**The Terminal & Mobile Web Music Studio — Stream Any Song Without API Keys or Account Login.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![mpv Engine](https://img.shields.io/badge/Audio-mpv%20engine-red.svg)](https://mpv.io/)
[![UI Mode](https://img.shields.io/badge/UI-Curses%20TUI%20%2B%20React%20Web-purple.svg)](https://react.dev)

[Quickstart](#-quickstart--installation) • [Key Features](#-key-features) • [TUI Keybindings](#-tui-keyboard-controls) • [CLI Commands](#-cli-command-reference) • [FAQ](#-frequently-asked-questions)

</div>

---

## 📖 Overview

**Skye Music Player** (`skyemusic` / `skye` / `tune`) is an open-source terminal streaming player, audio engine, and mobile web remote built on top of `mpv` and `yt-dlp`. 

It allows you to search and stream any song, YouTube video, or playlist directly inside your terminal or from a phone web app — with zero ads, zero API keys, and no login required.

---

## ⚡ Quickstart & Installation

### Prerequisites
Make sure you have `mpv` and `yt-dlp` installed on your system:
```bash
# macOS (via Homebrew)
brew install mpv yt-dlp

# Linux (Debian / Ubuntu / Arch)
sudo apt install mpv
pip install -U yt-dlp
```

---

### Method 1: Instant Launch via NPX / NPM (Zero Install)

No Python package setup needed! Run instantly using Node.js:

```bash
# Launch directly with NPX (Zero installation)
npx skyemusic

# Or install globally with NPM
npm install -g skyemusic

# Run anywhere
skye
```

---

### Method 2: Installation via Python / PIP

Install from PyPI or directly from source:

```bash
# Install from PyPI
pip install tune-cli

# Or clone & install locally
git clone https://github.com/sharadvc/SkyeMusic.git
cd SkyeMusic

# Install in editable mode
pip install -e .

# Note: On macOS Homebrew Python, pass --break-system-packages if prompted:
pip install -e . --break-system-packages
```

---

## 🌟 Key Features

### 🎤 1. Live Synced Karaoke Lyrics
- **Sub-Second Precision**: Synchronized character-by-character karaoke line sweeps.
- **Automatic Lyrics Lookup**: Fetches LRC and VTT synced lyrics automatically via LRCLIB and JioSaavn.
- **Hinglish Transliteration**: Automatically converts Hindi/Devanagari scripts to easy-to-read Roman Hinglish.
- **Live Sync Offset Nudge**: Adjust timing live using `,` (-0.05s) and `.` (+0.05s).

### 🖥️ 2. Dual-Pane Studio Curses TUI
- **Dual-Pane Layout**: Track Queue on the left (`col 0..left_w`), Karaoke Lyrics on the right (`col left_w+1..w`).
- **12 Audio Visualizers**: Spectrum (peak hold), Stereo (L/R), Waveform, Matrix, DNA Helix, Cyberpunk Retrowave Horizon, Aurora, Fire, and VU meters.
- **Live Color Themes**: 28 color themes (`Tanjiro`, `Rengoku`, `Zenitsu`, `Dracula`, `Cyberpunk`, `Tokyo`, `Amber`) previewable live (`t` key).

### 📱 3. Mobile Web Remote & AirDrop Wi-Fi Pairing
- **Mobile Touch PWA**: Touch-friendly React web interface served automatically at `http://<your-ip>:8765`.
- **AirDrop Wi-Fi Discovery**: Direct local IP resolution (`http://192.168.x.x:8765/`) for iOS AirDrop & instant QR Code scanning.
- **Real-Time Sync**: Server-Sent Events push progress, play/pause state, volume, and queue updates instantly.

### 🎧 4. Pioneer DDJ-Style DJ Deck Console
- **Dual Deck Console**: Press `J` in the TUI to open a full-screen Pioneer DDJ DJ deck console.
- **Real-Time FX**: Live vinyl scratch (`s`), LP/HP filter sweep (`e`), sub-bass drop (`b`), and crossfader auto-fade (`f`).

### 📡 5. Skyecast Live Multi-User Broadcast
- **Broadcast Live**: Stream your active playback session to friends (`skye broadcast <room_name>`).
- **Join Broadcast**: Friends join your room (`skye join <room_name>`) to mirror your track position and pause/play state in real-time.

### 🧠 6. Deep Focus OS & Mood Engine
- **Pomodoro Timer**: Launch ambient focus sessions (`skye focus 25`) with a dark, minimalist UI.
- **14 Mood Generators**: Procedurally streams sessions by mood (`skye mood focus`, `skye mood lo-fi`, `skye mood chill hindi`).

---

## ⌨️ TUI Keyboard Controls

| Key | Action |
| :--- | :--- |
| `space` | Play / Pause playback |
| `tab` | Cycle layout (**Dual-Pane Studio** → **Queue** → **Lyrics** → **Mini Player**) |
| `v` | Cycle visualizers (**Spectrum**, **Stereo**, **Wave**, **Fire**, **Matrix**, **DNA**, etc.) |
| `/` | Search YouTube directly from inside the TUI |
| `n` / `p` | Next track / Previous track |
| `↑` / `↓` | Move selection cursor in queue |
| `enter` | Jump to selected track |
| `d` | Delete selected track from queue |
| `+` / `-` | Adjust volume up / down (5%) |
| `←` / `→` | Seek backward / forward 5s |
| `,` / `.` | Live fine-tune lyrics sync (-0.05s / +0.05s) |
| `o` | Reset lyrics sync offset to 0.00s |
| `l` | Toggle between Studio mode and full Karaoke Lyrics view |
| `t` | Open live full-screen Color Theme Selector |
| `J` | Enter Pioneer DDJ-style DJ Deck Console |
| `P` | Toggle RGB Party Mode lightshow |
| `q` / `Esc` | Exit TUI (daemon continues playing in background) |

---

## 💻 CLI Command Reference

```bash
skye                           # Open interactive full-screen TUI studio
skye play "song name or url"   # Play a track or YouTube URL immediately
skye add "song name"           # Add track to queue without interrupting
skye search "coldplay"         # Search tracks and select interactively
skye mix "lo-fi beats"         # Generate & shuffle instant mix session
skye mood focus                # Smart session for a mood (14 moods supported)
skye radio "tame impala"       # Dynamic radio stream based on artist/genre
skye similar                   # Play tracks similar to current song
skye queue                     # Display current track queue
skye next | prev | pause | resume # Transport control commands
skye volume 80                 # Set volume (0-130%)
skye seek +30                  # Seek forward 30 seconds
skye lyrics                    # View current track's synced lyrics
skye remote                    # Show phone web remote URL & QR code
skye dj                        # Launch Pioneer DDJ DJ deck console
skye focus 25                  # Start 25-minute Pomodoro focus session
skye broadcast <name>          # Broadcast live session to friends
skye join <name>               # Join a friend's live Skyecast session
skye quit                      # Shutdown daemon and stop audio backend
```

---

## ❓ Frequently Asked Questions

#### Q: What is Skye Music Player?
**A**: Skye Music Player (`SkyeMusic` / `skye` / `tune`) is an open-source terminal music player and streaming engine. It streams audio directly from YouTube using `mpv` and `yt-dlp`, featuring live synchronized karaoke lyrics, a Curses dual-pane TUI, and a phone web remote.

#### Q: Does it require any YouTube API keys or account login?
**A**: No. Skye Music Player operates 100% keyless and account-free.

#### Q: How do I control playback from my phone?
**A**: Run `skye remote` in your terminal to get your local Wi-Fi URL (e.g., `http://192.168.1.17:8765/`) or scan the QR code to open the web remote on your mobile browser.

---

## 📄 License

Distributed under the **MIT License**. Free and open-source for personal and commercial use.
