#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGFILE="$SCRIPT_DIR/start.log"

log() { echo "  $1"; echo "  $1" >> "$LOGFILE"; }

echo "PSS START LOG" > "$LOGFILE"
echo "Started: $(date)" >> "$LOGFILE"
echo "Machine: $(hostname)" >> "$LOGFILE"
echo "WorkDir: $SCRIPT_DIR" >> "$LOGFILE"
echo "" >> "$LOGFILE"

cd "$SCRIPT_DIR"

# Check .env
if [ ! -f ".env" ]; then
    log "[ERROR] .env not found - run ./install.sh first!"
    exit 1
fi
log "[OK] .env found"

# Load .env
set -a; source .env; set +a

# Check Python
if ! command -v python3 &>/dev/null; then
    log "[ERROR] Python 3 not found - run ./install.sh first!"
    exit 1
fi
log "[OK] $(python3 --version 2>&1)"

# Check FastAPI
if ! python3 -c "import fastapi; print(f'fastapi {fastapi.__version__}')" &>/dev/null; then
    log "[ERROR] FastAPI not installed - run ./install.sh first!"
    exit 1
fi
log "[OK] $(python3 -c "import fastapi; print(f'fastapi {fastapi.__version__}')")"

# Check database
if [ -f "data/pss.db" ]; then
    log "[OK] Database exists"
else
    log "[INFO] No database yet - first run will fetch your Steam library"
fi

echo ""
log "===================================="
log " PSS is running!"
echo ""
log " Customizer: http://localhost:8787/customizer"
log " Screensaver: http://localhost:8787/screensaver"
echo ""
log " Press Ctrl+C to stop."
log "===================================="
echo ""

# Try to open browser (non-blocking, best-effort)
(sleep 2 && {
    if command -v xdg-open &>/dev/null; then
        xdg-open "http://localhost:8787/customizer" 2>/dev/null
    elif command -v open &>/dev/null; then
        open "http://localhost:8787/customizer" 2>/dev/null
    fi
} &) 2>/dev/null || true

python3 -m pss.server

echo "" >> "$LOGFILE"
echo "Server exited: $(date)" >> "$LOGFILE"
log "Server stopped."
