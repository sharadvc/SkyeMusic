#!/usr/bin/env bash
# ==============================================================================
# Skye Player (tune / skye / skyemusic) — One-Line Global Installer
# ==============================================================================
# Installs system dependencies (mpv, yt-dlp, cloudflared) and links tune, skye,
# skyemusic binaries permanently into your system PATH.
# ==============================================================================

set -e

BOLD="\033[1m"
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RESET="\033[0m"

echo -e "${CYAN}${BOLD}"
echo "  ⚡ Skye Player (tune / skye / skyemusic) — Global Installer"
echo -e "${RESET}"

# 1. Detect OS & Architecture
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

echo -e "${BOLD}[1/4] Checking system dependencies (${OS}/${ARCH})...${RESET}"

# 2. Package Manager & System Dependencies
install_mac_deps() {
    MISSING=""
    command -v mpv >/dev/null 2>&1 || MISSING="$MISSING mpv"
    command -v yt-dlp >/dev/null 2>&1 || MISSING="$MISSING yt-dlp"
    command -v cloudflared >/dev/null 2>&1 || MISSING="$MISSING cloudflared"

    if [ -n "$MISSING" ]; then
        if command -v brew >/dev/null 2>&1; then
            echo "  ✓ Installing missing system dependencies:$MISSING via Homebrew..."
            brew install $MISSING 2>/dev/null || true
        else
            echo -e "${YELLOW}  ! Homebrew not found. Please ensure mpv and yt-dlp are installed.${RESET}"
        fi
    else
        echo "  ✓ All system dependencies (mpv, yt-dlp, cloudflared) already installed."
    fi
}

install_linux_deps() {
    if command -v apt-get >/dev/null 2>&1; then
        echo "  ✓ Debian/Ubuntu detected. Installing mpv, python3, git..."
        sudo apt-get update -qq && sudo apt-get install -y -qq mpv python3 python3-pip git curl 2>/dev/null || true
    elif command -v dnf >/dev/null 2>&1; then
        echo "  ✓ Fedora/RHEL detected. Installing mpv..."
        sudo dnf install -y mpv python3 git curl 2>/dev/null || true
    elif command -v pacman >/dev/null 2>&1; then
        echo "  ✓ Arch Linux detected. Installing mpv, yt-dlp..."
        sudo pacman -Sy --noconfirm mpv yt-dlp python-pip git 2>/dev/null || true
    fi

    # Ensure yt-dlp is installed
    if ! command -v yt-dlp >/dev/null 2>&1; then
        echo "  ✓ Installing latest yt-dlp binary..."
        sudo curl -sSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp 2>/dev/null || true
        sudo chmod +x /usr/local/bin/yt-dlp 2>/dev/null || true
    fi
}

case "$OS" in
    darwin*) install_mac_deps ;;
    linux*)  install_linux_deps ;;
    *)       echo "  ! OS '$OS' supported via manual binary link." ;;
esac

# 3. Install Python Package & Binaries
echo -e "${BOLD}[2/4] Installing Python package & CLI binaries...${RESET}"
REPO_URL="https://github.com/sharadvc/SkyeMusic.git"
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
    INSTALL_SRC="$SCRIPT_DIR"
else
    # Piped via curl — clone or pull into ~/.local/share/skyemusic
    INSTALL_DIR="$HOME/.local/share/skyemusic"
    mkdir -p "$HOME/.local/share"
    if [ -d "$INSTALL_DIR/.git" ]; then
        echo "  ✓ Updating existing SkyeMusic repository..."
        git -C "$INSTALL_DIR" pull --quiet 2>/dev/null || true
    else
        echo "  ✓ Cloning SkyeMusic repository into $INSTALL_DIR..."
        rm -rf "$INSTALL_DIR"
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR" --quiet
    fi
    INSTALL_SRC="$INSTALL_DIR"
    SCRIPT_DIR="$INSTALL_DIR"
fi

if [ -f "$SCRIPT_DIR/bin/tune" ]; then
    chmod +x "$SCRIPT_DIR/bin/tune" 2>/dev/null || true
fi

if command -v pipx >/dev/null 2>&1; then
    pipx install --force "$INSTALL_SRC" 2>/dev/null || true
elif command -v pip3 >/dev/null 2>&1; then
    pip3 install --quiet --break-system-packages "$INSTALL_SRC" 2>/dev/null || pip3 install --user --quiet --break-system-packages "$INSTALL_SRC" 2>/dev/null || true
elif command -v pip >/dev/null 2>&1; then
    pip install --quiet --break-system-packages "$INSTALL_SRC" 2>/dev/null || pip install --user --quiet --break-system-packages "$INSTALL_SRC" 2>/dev/null || true
fi

# 4. Link Global Binaries (tune, skye, skyemusic)
echo -e "${BOLD}[3/4] Linking global commands (tune, skye, skyemusic)...${RESET}"
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"

for cmd in tune skye skyemusic; do
    # Link from script dir bin/tune if present or package entrypoint
    if [ -f "$SCRIPT_DIR/bin/tune" ]; then
        ln -sf "$SCRIPT_DIR/bin/tune" "$LOCAL_BIN/$cmd"
    fi
done

# Ensure ~/.local/bin is on PATH
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
SHELL_RC=""
if [ -n "$ZSH_VERSION" ] || [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -n "$BASH_VERSION" ] || [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

if [ -n "$SHELL_RC" ] && ! grep -q '\.local/bin' "$SHELL_RC" 2>/dev/null; then
    echo "" >> "$SHELL_RC"
    echo "# Skye Player PATH" >> "$SHELL_RC"
    echo "$PATH_LINE" >> "$SHELL_RC"
    echo -e "  ✓ Added ~/.local/bin to $SHELL_RC"
fi

export PATH="$HOME/.local/bin:$PATH"

# 5. Doctor & Verification
echo -e "${BOLD}[4/4] Verifying installation health...${RESET}"
if command -v tune >/dev/null 2>&1; then
    tune doctor 2>/dev/null || true
fi

echo -e "\n${GREEN}${BOLD}========================================================================${RESET}"
echo -e "${GREEN}${BOLD} 🎉 Skye Player (tune / skye / skyemusic) installed permanently! ${RESET}"
echo -e "${GREEN}${BOLD}========================================================================${RESET}\n"
echo -e "  Try running any of these commands from your terminal:\n"
echo -e "    ${CYAN}skye${RESET}                         # Full-screen interactive TUI player"
echo -e "    ${CYAN}skye play \"lofi beats\"${RESET}       # Instant audio streaming"
echo -e "    ${CYAN}skye eq cyberpunk${RESET}            # Apply 10-band equalizer"
echo -e "    ${CYAN}skye party${RESET}                   # Multi-room global party with QR code"
echo -e "    ${CYAN}skye download \"song\"${RESET}         # Offline audio downloader"
echo ""
