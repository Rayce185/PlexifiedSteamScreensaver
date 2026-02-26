#!/usr/bin/env bash
# ============================================
#  PSS Universal Launcher
#  Run this file. That's it.
# ============================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'

# Check Python
if ! command -v python3 &>/dev/null; then
    echo ""
    echo -e "  ${RED}Python 3 is not installed.${NC}"
    echo ""
    echo "  Install it with your package manager:"
    echo ""
    echo "  Ubuntu/Debian:  sudo apt install python3 python3-pip"
    echo "  Fedora:         sudo dnf install python3 python3-pip"
    echo "  Arch:           sudo pacman -S python python-pip"
    echo "  macOS:          brew install python3"
    echo ""
    echo "  Then run this script again."
    exit 1
fi

echo ""
echo -e "  ${CYAN}PSS — Plexified Steam Screensaver${NC}"
echo ""

# Check dependencies
if ! python3 -c "import fastapi" &>/dev/null; then
    echo "  Installing dependencies..."
    python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" -q
    echo -e "  ${GREEN}[OK]${NC} Dependencies installed."
fi

# Check .env
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo ""
    echo "  ============================================"
    echo "   First-Time Setup"
    echo "  ============================================"
    echo ""
    echo "  You need a Steam Web API Key to use PSS."
    echo ""
    echo "  1. Open: https://steamcommunity.com/dev/apikey"
    echo "  2. Log in with your Steam account"
    echo "  3. Enter any domain name (e.g. 'localhost')"
    echo "  4. Copy the key shown"
    echo ""

    # Try to open browser
    if command -v xdg-open &>/dev/null; then
        xdg-open "https://steamcommunity.com/dev/apikey" 2>/dev/null &
    elif command -v open &>/dev/null; then
        open "https://steamcommunity.com/dev/apikey" 2>/dev/null &
    fi

    read -rp "  Paste your Steam API Key here: " STEAM_KEY
    if [ -z "$STEAM_KEY" ]; then
        echo -e "  ${RED}[ERROR]${NC} No key entered."
        exit 1
    fi

    echo "STEAM_API_KEY=$STEAM_KEY" > "$SCRIPT_DIR/.env"
    echo -e "  ${GREEN}[OK]${NC} API key saved."

    mkdir -p "$SCRIPT_DIR/data" "$SCRIPT_DIR/logs"
fi

# Load .env
set -a; source "$SCRIPT_DIR/.env"; set +a

# Check if desktop environment available (for tray mode)
HAS_DISPLAY=false
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    HAS_DISPLAY=true
fi

if $HAS_DISPLAY && python3 -c "import pystray" &>/dev/null; then
    echo "  Starting PSS with system tray..."
    echo "  Look for the PSS icon in your system tray."
    echo ""
    python3 "$SCRIPT_DIR/pss_tray.pyw" &
    disown
    sleep 2
    echo -e "  ${GREEN}[OK]${NC} PSS is running in your system tray."
    echo ""
    echo -e "  ${CYAN}Customizer:  http://localhost:8787/customizer${NC}"
    echo -e "  ${CYAN}Screensaver: http://localhost:8787/screensaver${NC}"
else
    echo "  Starting PSS server..."
    echo ""
    echo -e "  ${CYAN}Customizer:  http://localhost:8787/customizer${NC}"
    echo -e "  ${CYAN}Screensaver: http://localhost:8787/screensaver${NC}"
    echo ""
    echo "  Press Ctrl+C to stop."
    echo ""

    # Open browser after delay
    (sleep 2 && {
        if command -v xdg-open &>/dev/null; then xdg-open "http://localhost:8787/customizer" 2>/dev/null
        elif command -v open &>/dev/null; then open "http://localhost:8787/customizer" 2>/dev/null
        fi
    } &) 2>/dev/null || true

    python3 -m pss.server
fi
