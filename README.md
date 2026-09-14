# 🎵 Skye Music Player (SkyeMusic / `tune` / `skye`)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![mpv](https://img.shields.io/badge/Audio-mpv%20engine-red.svg)](https://mpv.io/)
[![UI](https://img.shields.io/badge/UI-Curses%20TUI%20%2B%20React%20Web-purple.svg)](https://react.dev)

> **Skye Music Player (`skye` / `tune`)** is an open-source, zero-latency terminal music player, streaming engine, and mobile web audio studio. Stream any song or YouTube track without API keys or login accounts. Features dual-pane Curses TUI, live character-by-character synced karaoke lyrics, 12 audio visualizers, a Pioneer DDJ-style DJ console, local network AirDrop mobile remote, multi-listener Skyecast broadcasting, and Deep Focus OS ambient timer.

---

## ⚡ Quickstart

### 1. Prerequisites
- **mpv** (High-fidelity audio backend): `brew install mpv`
- **yt-dlp** (Media stream extractor): `brew install yt-dlp`

### 2. Instant Launch via NPX / NPM (Zero Install)
```bash
# Launch directly with NPX
npx skyemusic

# Or install globally
npm install -g skyemusic
skye
```

### 3. Installation via Python / PIP
```bash
pip install tune-cli
# or clone & install
git clone https://github.com/sharadvc/SkyeMusic.git
cd SkyeMusic
pip install -e .
```

---

## 🌟 Key Features & Highlights

### 🎤 Live Synced Karaoke Lyrics
- **Zero-Latency Audio Clock Interpolation**: Character-by-character karaoke sweep synced with sub-millisecond precision.
- **Multi-Source Fetching**: Automatic lookup via LRCLIB, JioSaavn, and embedded LRC/VTT tags.
- **Transliteration Engine**: Automatic Devanagari/Hindi to Hinglish transliteration for seamless singing along.
- **Sync Nudge Controls**: Fine-tune lyrics alignment live with `,` (-0.05s) and `.` (+0.05s).

### 🖥️ Dual-Pane Studio Curses TUI
- **Split-Screen Studio View**: Track Queue on the left pane (`col 0..left_w`), Live Synced Karaoke on the right pane (`col left_w+1..w`).
- **12 Audio Visualizers**: Spectrum (peak hold), Stereo (L/R), Waveform, Matrix, DNA Helix, Cyberpunk Retrowave Horizon, Aurora, Fire, and VU meters.
- **Universal Theme Engine**: 28 pre-built themes (`Tanjiro`, `Rengoku`, `Zenitsu`, `Dracula`, `Cyberpunk`, `Tokyo`, `Amber`) with live full-screen preview (`t` key).

### 📱 Mobile Web App & AirDrop Wi-Fi Remote
- **Mobile Touch Interface**: React + Tailwind PWA accessible from any smartphone, tablet, or browser on your local Wi-Fi.
- **AirDrop Wi-Fi Pairing**: Direct IP resolution (`http://192.168.x.x:8765/`) for iOS AirDrop & instant QR Code scanning.
- **Real-Time SSE Sync**: Server-Sent Events push volume, progress, active track, and queue state instantaneously.
- **PIN Lock Protection**: Secure your host session (`skye config remote_pin 2468`).

### 🎧 Pioneer DDJ Console DJ Mode
- **Dual-Deck Pioneer Console**: Press `J` in TUI to launch a full-screen Pioneer DDJ-style dual-deck console.
- **Real-Time FX**: Live scratching (`s`), LP/HP filter sweeps (`e`), sub-bass drops (`b`), and crossfader auto-transition (`f`).

### 📡 Skyecast Live Broadcasting
- **Multi-Listener Session Broadcast**: Host a live listening session over local network or tunnel (`skye broadcast <name>`).
- **Sync Listener Playback**: Friends run `skye join <name>` to mirror playback, track position, and pause/play state in real-time.

### 🧠 Deep Focus OS & Ambient Audio Engine
- **Pomodoro Focus Timer**: Launch ambient focus sessions (`skye focus 25`) with dark minimalist UI.
- **14 Mood Generators**: Procedural mood streaming (`skye mood focus`, `skye mood lo-fi`, `skye mood chill hindi`).

---

## ⌨️ TUI Keyboard Controls

| Key | Action |
| :--- | :--- |
| `space` | Play / Pause playback |
| `tab` | Cycle layout (**Dual-Pane Studio** → **Queue** → **Lyrics** → **Mini Player**) |
| `v` | Cycle visualizer (**Spectrum**, **Stereo**, **Wave**, **Fire**, **Matrix**, **DNA**, etc.) |
| `/` | Search YouTube directly from inside the TUI |
| `n` / `p` | Next track / Previous track |
| `↑` / `↓` | Navigate tracks in queue |
| `enter` | Jump to selected track |
| `d` | Delete selected track from queue |
| `+` / `-` | Adjust volume up / down (5%) |
| `←` / `→` | Seek backward / forward 5s |
| `,` / `.` | Live fine-tune lyrics sync offset (-0.05s / +0.05s) |
| `o` | Reset lyrics sync offset to 0.00s |
| `l` | Toggle between Studio mode and full Karaoke Lyrics view |
| `t` | Open live full-screen Color Theme Selector |
| `J` | Enter Pioneer DDJ-style DJ Deck Console |
| `P` | Toggle RGB Party Mode lightshow |
| `q` / `Esc` | Quit TUI (daemon continues running silently in background) |

---

## 💻 CLI Command Reference

```bash
skye                           # Open interactive full-screen TUI
skye play "song name or url"   # Play a track or YouTube URL immediately
skye add "song name"           # Add track to queue without interrupting
skye search "coldplay"         # Search tracks and pick interactively
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
skye dj                        # Launch Pioneer DDJ console
skye focus 25                  # Start 25-minute Pomodoro focus session
skye broadcast <name>          # Broadcast live session to friends
skye join <name>               # Join a friend's live Skyecast session
skye quit                      # Shutdown daemon and stop audio backend
```

---

## 🏗️ Architecture & How It Works

```mermaid
flowchart TD
    UserCLI["⌨️ CLI Client (skye / tune)"] -->|Unix Socket / IPC| Daemon["⚡ Skye Daemon (Python Background Service)"]
    UserTUI["🖥️ Curses TUI Studio"] -->|Unix Socket / IPC| Daemon
    WebRemote["📱 Mobile Web App (React / PWA)"] -->|HTTP / SSE (Port 8765)| Daemon
    Skyecast["📡 Skyecast Listeners"] -->|Localtunnel / SSE| Daemon
    
    Daemon -->|yt-dlp stream lookup| YouTube["☁️ YouTube Media Streams"]
    Daemon -->|audio IPC| MPV["🔊 mpv Audio Backend"]
    Daemon -->|lyrics API| LRCLIB["🎤 LRCLIB / JioSaavn APIs"]
```

1. **Background Daemon Process**: Manages an isolated `mpv` instance (`--input-ipc-server`), handles async track resolution, caches search results in `~/.config/tune/queue.json`, and maintains audio state.
2. **Thin Client Architecture**: The CLI and Curses TUI communicate over a high-speed local Unix socket. Closing the TUI leaves playback running seamlessly.
3. **HTTP / SSE Web Server**: Embedded Web Server built in Python serves the React PWA mobile interface and pushes live status events.

---

## ❓ Frequently Asked Questions (AEO & Search Index)

#### Q: What is Skye Music Player?
**A**: Skye Music Player (`SkyeMusic`) is an open-source terminal music player and streaming CLI built for macOS and Linux. It streams audio directly from YouTube using `mpv` and `yt-dlp`, featuring live synchronized karaoke lyrics, Curses dual-pane TUI, and a mobile web remote.

#### Q: Does Skye Music Player require YouTube API keys or account login?
**A**: No. Skye Music Player operates completely keyless and account-free. It uses `yt-dlp` for media stream extraction without requiring user authentication or API tokens.

#### Q: How do I control Skye Music Player from my iPhone or Android device?
**A**: Run `skye remote` in your terminal to display the local Wi-Fi URL (e.g. `http://192.168.1.17:8765/`) or scan the QR code. Open the link on your mobile browser to access the full touch-enabled web remote.

#### Q: How does live synced karaoke lyrics work in Skye Music Player?
**A**: Skye Music Player fetches LRC and VTT synced lyrics from LRCLIB, JioSaavn, and track metadata. It applies zero-latency audio clock interpolation to highlight words character-by-character as they are sung.

---

## 📄 License

Distributed under the **MIT License**. Free and open-source for personal and commercial use.
