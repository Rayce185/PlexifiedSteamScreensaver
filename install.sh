#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGFILE="$SCRIPT_DIR/install.log"

log() { echo "  $1"; echo "  $1" >> "$LOGFILE"; }

echo "PSS INSTALL LOG" > "$LOGFILE"
echo "Started: $(date)" >> "$LOGFILE"
echo "Machine: $(hostname) User: $(whoami)" >> "$LOGFILE"
echo "WorkDir: $SCRIPT_DIR" >> "$LOGFILE"
echo "" >> "$LOGFILE"

echo ""
echo "  ===================================="
echo "   PSS - Plexified Steam Screensaver"
echo "   First-Time Setup"
echo "  ===================================="
echo ""

# --- Python ---
if ! command -v python3 &>/dev/null; then
    log "[ERROR] Python 3 not found. Install via your package manager."
    exit 1
fi
PYVER=$(python3 --version 2>&1)
log "[OK] $PYVER"

# --- pip ---
if ! python3 -m pip --version &>/dev/null; then
    log "[ERROR] pip not found. Install python3-pip."
    exit 1
fi
PIPVER=$(python3 -m pip --version 2>&1)
log "[OK] $PIPVER"

# --- Dependencies ---
echo ""
log "Installing dependencies..."
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" >> "$LOGFILE" 2>&1
log "[OK] Dependencies installed."

# --- .env check ---
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo ""
    log "[OK] .env already exists - skipping setup."
else
    echo ""
    echo "  -----------------------------------------------"
    echo "  You need a Steam Web API Key to use PSS."
    echo "  Get one here: https://steamcommunity.com/dev/apikey"
    echo "  -----------------------------------------------"
    echo ""
    read -rp "  Enter your Steam API Key: " STEAM_KEY
    if [ -z "$STEAM_KEY" ]; then
        log "[ERROR] No key entered."
        exit 1
    fi

    # --- Auto-detect Steam ---
    STEAM_DIR=""
    CANDIDATES=(
        "$HOME/.local/share/Steam"
        "$HOME/.steam/steam"
        "$HOME/.steam/debian-installation"
        "$HOME/Library/Application Support/Steam"
    )
    for p in "${CANDIDATES[@]}"; do
        if [ -d "$p/config" ]; then
            STEAM_DIR="$p"
            break
        fi
    done

    if [ -n "$STEAM_DIR" ]; then
        log "[OK] Steam found at: $STEAM_DIR"
    else
        echo "  [WARN] Steam not found at standard paths."
        read -rp "  Enter Steam install path: " STEAM_DIR
    fi

    cat > "$SCRIPT_DIR/.env" <<ENVEOF
STEAM_API_KEY=$STEAM_KEY
STEAM_PATH=$STEAM_DIR
ENVEOF
    log "[OK] Configuration saved to .env"
fi

# --- Create dirs ---
mkdir -p "$SCRIPT_DIR/data" "$SCRIPT_DIR/logs"

echo ""
echo "  ===================================="
echo "   Setup complete!"
echo ""
echo "   To start PSS, run: ./start.sh"
echo "  ===================================="
echo ""
