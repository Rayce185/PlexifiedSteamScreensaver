#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
BACKUP_DIR="$SCRIPT_DIR/_update_backup"
ZIP_URL="https://github.com/Rayce185/PlexifiedSteamScreensaver/archive/refs/heads/main.zip"
TMPDIR=$(mktemp -d)

trap 'rm -rf "$TMPDIR"' EXIT

echo ""
echo "  ============================================"
echo "   PlexifiedSteamScreensaver - Updater"
echo "  ============================================"
echo ""

# Check if server is running
if pgrep -f "pss.server" >/dev/null 2>&1; then
    echo "  [!] PSS server appears to be running. Please stop it first."
    exit 1
fi

# Step 1: Backup data folder
if [ -d "$DATA_DIR" ]; then
    echo "  [1/6] Backing up data folder..."
    rm -rf "$BACKUP_DIR"
    mkdir -p "$BACKUP_DIR"
    cp -a "$DATA_DIR/." "$BACKUP_DIR/"
    echo "        Backed up to _update_backup/"
else
    echo "  [1/6] No data folder found - fresh install"
fi

# Step 2: Download latest
echo "  [2/6] Downloading latest from GitHub..."
curl -sSL "$ZIP_URL" -o "$TMPDIR/pss_update.zip"
if [ ! -f "$TMPDIR/pss_update.zip" ]; then
    echo "  [!] Download failed. Check your internet connection."
    echo "      Your data is safe in _update_backup/"
    exit 1
fi

# Step 3: Extract
echo "  [3/6] Extracting..."
unzip -qo "$TMPDIR/pss_update.zip" -d "$TMPDIR"
SRC="$TMPDIR/PlexifiedSteamScreensaver-main"
if [ ! -d "$SRC" ]; then
    echo "  [!] Extraction failed - unexpected folder structure."
    exit 1
fi

# Step 4: Copy new files (skip data/)
echo "  [4/6] Updating files..."
shopt -s dotglob
for item in "$SRC"/*; do
    name=$(basename "$item")
    [ "$name" = "data" ] && continue
    cp -a "$item" "$SCRIPT_DIR/"
done

# Step 5: Restore data
if [ -d "$BACKUP_DIR" ]; then
    echo "  [5/6] Restoring data..."
    mkdir -p "$DATA_DIR"
    cp -a "$BACKUP_DIR/." "$DATA_DIR/"
    rm -rf "$BACKUP_DIR"
    echo "        Data restored successfully."
else
    echo "  [5/6] No data to restore."
fi

# Step 6: Install dependencies
echo "  [6/6] Installing dependencies..."
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" -q 2>/dev/null || {
    echo "  [!] pip install failed. Try: python3 -m pip install -r requirements.txt"
}

echo ""
echo "  ============================================"
echo "   Update complete!"
echo "   Run ./start.sh to launch the server."
echo "  ============================================"
echo ""
