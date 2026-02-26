#!/usr/bin/env bash
set -euo pipefail

# PSS Service Manager — systemd integration
# Usage: ./pss-service.sh install|uninstall|start|stop|restart|status|logs

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="pss"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="$SCRIPT_DIR/.env"

GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; DIM='\033[0;90m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "  ${RED}[ERROR]${NC} $1"; }
info() { echo -e "  $1"; }
dim()  { echo -e "  ${DIM}$1${NC}"; }

find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            echo "$cmd"
            return
        fi
    done
    return 1
}

do_install() {
    local py
    py=$(find_python) || { err "Python not found in PATH."; exit 1; }
    local py_path
    py_path=$(command -v "$py")

    if [ ! -f "$ENV_FILE" ]; then
        err ".env not found — run ./install.sh first."
        exit 1
    fi

    # Check if we need sudo
    if [ "$(id -u)" -ne 0 ]; then
        warn "Creating systemd service requires root. Re-running with sudo..."
        exec sudo "$0" install
    fi

    local run_user="${SUDO_USER:-$USER}"
    local run_group
    run_group=$(id -gn "$run_user")

    cat > "$UNIT_FILE" <<UNITEOF
[Unit]
Description=PSS — Plexified Steam Screensaver
After=network.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=$run_user
Group=$run_group
WorkingDirectory=$SCRIPT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$py_path -m pss.server
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pss

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$SCRIPT_DIR/data $SCRIPT_DIR/logs

[Install]
WantedBy=multi-user.target
UNITEOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"

    ok "Service installed and enabled."
    dim "Unit file: $UNIT_FILE"
    dim "User:      $run_user"
    dim "Python:    $py_path"
    dim "Autostart: On boot (multi-user.target)"
    echo ""
    ok "PSS will auto-start on boot."
    info "  Run ${YELLOW}'./pss-service.sh start'${NC} to start now."
}

do_uninstall() {
    do_stop 2>/dev/null || true

    if [ "$(id -u)" -ne 0 ]; then
        exec sudo "$0" uninstall
    fi

    if [ -f "$UNIT_FILE" ]; then
        systemctl disable "$SERVICE_NAME" 2>/dev/null || true
        rm -f "$UNIT_FILE"
        systemctl daemon-reload
        ok "Service removed."
    else
        warn "Service not installed."
    fi
}

do_start() {
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        warn "PSS is already running."
        return
    fi

    if [ ! -f "$UNIT_FILE" ]; then
        warn "Not installed as service. Run './pss-service.sh install' first."
        dim "Or use './start.sh' for foreground mode."
        return
    fi

    sudo systemctl start "$SERVICE_NAME"
    sleep 1

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "PSS started."
        info "  ${CYAN}Customizer:  http://localhost:8787/customizer${NC}"
        info "  ${CYAN}Screensaver: http://localhost:8787/screensaver${NC}"
    else
        err "Start failed. Check: ./pss-service.sh logs"
    fi
}

do_stop() {
    if ! systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        info "  PSS is not running."
        return
    fi
    sudo systemctl stop "$SERVICE_NAME"
    ok "PSS stopped."
}

do_restart() {
    sudo systemctl restart "$SERVICE_NAME"
    sleep 1
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "PSS restarted."
    else
        err "Restart failed. Check: ./pss-service.sh logs"
    fi
}

do_status() {
    local installed=false running=false

    echo ""
    echo -e "  ${CYAN}PSS Service Status${NC}"
    echo -e "  ${DIM}─────────────────────────────${NC}"

    if [ -f "$UNIT_FILE" ]; then
        installed=true
        local enabled
        enabled=$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || echo "unknown")
        ok "Installed ($enabled)"
    else
        warn "Not installed"
    fi

    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        running=true
        local pid uptime mem
        pid=$(systemctl show "$SERVICE_NAME" --property=MainPID --value 2>/dev/null || echo "?")
        uptime=$(systemctl show "$SERVICE_NAME" --property=ActiveEnterTimestamp --value 2>/dev/null || echo "?")

        if [ "$pid" != "?" ] && [ "$pid" != "0" ]; then
            mem=$(ps -p "$pid" -o rss= 2>/dev/null || echo "0")
            mem=$((mem / 1024))
            ok "Running (PID $pid, ${mem} MB)"
            dim "Since: $uptime"
        else
            ok "Running"
        fi

        echo ""
        info "  ${CYAN}Customizer:  http://localhost:8787/customizer${NC}"
        info "  ${CYAN}Screensaver: http://localhost:8787/screensaver${NC}"
    else
        warn "Not running"
    fi

    # Log
    local log_dir="$SCRIPT_DIR/logs"
    if [ -d "$log_dir" ]; then
        local latest
        latest=$(ls -t "$log_dir"/pss*.log 2>/dev/null | head -1)
        if [ -n "$latest" ]; then
            local log_size
            log_size=$(du -k "$latest" | cut -f1)
            dim "Log: $(basename "$latest") (${log_size} KB)"
        fi
    fi
    echo ""
}

do_logs() {
    if command -v journalctl &>/dev/null; then
        journalctl -u "$SERVICE_NAME" -f --no-hostname -o short-iso
    else
        local log_dir="$SCRIPT_DIR/logs"
        local latest
        latest=$(ls -t "$log_dir"/pss*.log 2>/dev/null | head -1)
        if [ -n "$latest" ]; then
            tail -f "$latest"
        else
            err "No log files found."
        fi
    fi
}

# ── DISPATCH ──
case "${1:-}" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
    start)     do_start ;;
    stop)      do_stop ;;
    restart)   do_restart ;;
    status)    do_status ;;
    logs)      do_logs ;;
    *)
        echo ""
        echo "  PSS Service Manager"
        echo ""
        echo "  Usage: $0 {install|uninstall|start|stop|restart|status|logs}"
        echo ""
        echo "  install    Register PSS as a systemd service (auto-start on boot)"
        echo "  uninstall  Remove the service and stop the server"
        echo "  start      Start PSS in the background"
        echo "  stop       Stop the running server"
        echo "  restart    Stop then start"
        echo "  status     Show current state (running/stopped, PID, memory)"
        echo "  logs       Follow live log output (journalctl or file tail)"
        echo ""
        exit 1
        ;;
esac
