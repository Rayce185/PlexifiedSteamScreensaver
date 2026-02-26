# PSS — Plexified Steam Screensaver

A cinematic Ken Burns slideshow screensaver for your Steam game library, with a full web-based customizer UI.

## Features

### Screensaver
- **Ken Burns slideshow** — 8 animation variants with configurable intensity and timing
- **Multi-row display elements** — 14+ data badges across configurable rows with per-row sizing (SM/MD/LG) and color/mono modes
- **WYSIWYG layout editor** — drag game info and clock overlays anywhere on screen via the Display tab
- **Image shuffle mode** — random images per game appearance from screenshots, SteamGridDB, and headers

### Library Management
- **Multi-account support** — auto-detects all Steam accounts from `loginusers.vdf`, hot-switches without restart
- **Per-account API keys** — each Steam account stores its own API key
- **Complete library coverage** — Steam API v1 + local manifest scan for tools/software not in API
- **Presets, filters, sorting** — type, genre, installed, played, enriched, NSFW, shovelware, Deck status
- **Bulk include/exclude** — with undo snapshots
- **Shovelware detection** — configurable 6-signal scoring (low playtime, few reviews, poor ratio, low owners, unplayed, no metacritic)
- **NSFW auto-detection** — auto-excludes explicit content from screensaver pool

### Enrichment Pipeline
- **Steam Store** — genres, developer, metacritic, controller/VR support, descriptions, screenshots
- **SteamSpy** — owner counts, global avg playtime, review counts
- **Steam Deck + ProtonDB** — Valve's official Deck compatibility + community Proton tier
- **IStoreService type correction** — proper app classification (game/software/tool/dlc/demo/music)
- **SteamGridDB image cache** — hero images with 16:9 aspect ratio filtering

### Image System
- **Per-game image picker** — choose from cached images, SGDB alternatives, screenshots, or upload custom images
- **Shuffle pre-download** — batch-download all image variants with progress tracking and size estimates
- **Selected mode** — uses your chosen hero image per game
- **Shuffle mode** — random image each appearance, prefers disk cache when available

### Administration
- **Steam OpenID authentication** — only known Steam accounts can access the customizer
- **Dynamic log level** — DEBUG/INFO/WARNING/ERROR changeable at runtime
- **Log viewer** — searchable, filterable log viewer in Settings with file selection and archive access
- **Auto-enrichment** — libraries under configurable threshold auto-enrich on first startup

## Requirements

- **Python 3.11+**
- **Steam** installed with games
- **Steam Web API Key** — get one at https://steamcommunity.com/dev/apikey
- **SteamGridDB API Key** (optional) — for hero image alternatives, get one at https://www.steamgriddb.com/profile/preferences/api

## Quick Start

### Windows (PowerShell)

```powershell
1. Right-click Install-PSS.ps1 → "Run with PowerShell"
2. Enter your Steam API Key when prompted
3. Right-click Start-PSS.ps1 → "Run with PowerShell"
```

### Linux / macOS

```bash
git clone https://github.com/Rayce185/PlexifiedSteamScreensaver.git
cd PlexifiedSteamScreensaver
chmod +x install.sh start.sh update.sh
./install.sh
./start.sh
```

### Manual Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your STEAM_API_KEY and STEAM_PATH
python -m pss.server
```

Steam path is auto-detected on all platforms. Set `STEAM_PATH` in `.env` to override.

## First Run

On first launch the server will:
1. Create the SQLite database in `data/pss.db`
2. Detect your Steam account(s) from `loginusers.vdf`
3. Fetch your game library from Steam API
4. Scan local manifests for installed tools/software

Open in browser:
- **Customizer**: http://localhost:8787/customizer
- **Screensaver**: http://localhost:8787/screensaver

### Enrichment (recommended)

In the customizer, go to **Settings** and run enrichment in order:

1. **Steam Store Enrichment** — genres, developer, metacritic, controller/VR (~25 min / 1000 games)
2. **SteamSpy Enrichment** — owner counts, playtime stats, reviews (~4 min / 1000 games)
3. **Deck & ProtonDB Enrichment** — Deck compat, ProtonDB tier, type correction (~7 min / 1000 games)
4. **SteamGridDB Image Cache** — hero image alternatives (requires SGDB API key)

## Updating

- **Windows**: Run `Update-PSS.ps1` (preserves `data/` directory)
- **Linux/macOS**: Run `./update.sh`

## Running as a Background Service

PSS includes service managers for both platforms. These handle auto-start on boot,
background execution, and clean start/stop lifecycle.

### Windows

```powershell
.\pss-service.ps1 install    # Register auto-start at logon (uses pythonw, no console)
.\pss-service.ps1 start      # Start now
.\pss-service.ps1 status     # Show PID, uptime, memory
.\pss-service.ps1 stop       # Stop the server
.\pss-service.ps1 restart    # Stop + start
.\pss-service.ps1 uninstall  # Remove auto-start and stop
```

Uses Task Scheduler with `pythonw.exe` (no console window). Restarts automatically
up to 3 times on failure with 1-minute intervals.

### Linux

```bash
./pss-service.sh install    # Create systemd unit, enable on boot
./pss-service.sh start      # Start now
./pss-service.sh status     # Show PID, uptime, memory
./pss-service.sh stop       # Stop the server
./pss-service.sh restart    # Stop + start
./pss-service.sh logs       # Follow live journal output
./pss-service.sh uninstall  # Remove service and stop
```

Creates a hardened systemd service with `Restart=on-failure`, `NoNewPrivileges`,
`PrivateTmp`, and `ProtectSystem=strict`.

### Foreground Mode

For debugging or development, use the interactive launchers:

```powershell
.\Start-PSS.ps1    # Windows — console with colored output
./start.sh          # Linux/macOS — terminal with browser auto-open
```

## API Reference

### Library & Games
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/games` | Full game library (merged with enrichment) |
| POST | `/api/refresh-library` | Re-fetch from Steam API |
| GET | `/api/excluded` | Excluded appid list |
| POST | `/api/excluded` | Update exclusion list |
| POST | `/api/toggle-exclusion` | Toggle single game |
| POST | `/api/bulk-exclusion` | Bulk include/exclude |
| POST | `/api/exclusion-snapshot` | Save exclusion state |
| GET | `/api/exclusion-snapshots` | List saved states |
| POST | `/api/exclusion-restore/{id}` | Restore saved state |
| GET | `/api/filter-values` | Distinct filterable values |
| POST | `/api/repair-types` | Re-run type correction |

### Accounts & Auth
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/accounts` | All detected accounts |
| GET | `/api/accounts/active` | Current active account |
| GET | `/api/accounts/detect` | Re-scan loginusers.vdf |
| POST | `/api/accounts/switch` | Switch active account |
| POST | `/api/accounts/{id}/api-key` | Set per-account API key |
| DELETE | `/api/accounts/{id}/api-key` | Remove per-account API key |
| GET | `/api/auth/steam/login` | Steam OpenID login |
| GET | `/api/auth/steam/callback` | OpenID callback |
| GET | `/api/auth/status` | Session status |
| POST | `/api/auth/logout` | Logout |

### Enrichment Workers
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/enrichment/start` | Start Store enrichment |
| GET | `/api/enrichment/status` | Store enrichment progress |
| POST | `/api/enrichment/stop` | Stop Store enrichment |
| POST | `/api/steamspy/start` | Start SteamSpy enrichment |
| GET | `/api/steamspy/status` | SteamSpy progress |
| POST | `/api/steamspy/stop` | Stop SteamSpy |
| POST | `/api/deck/start` | Start Deck/ProtonDB enrichment |
| GET | `/api/deck/status` | Deck enrichment progress |
| POST | `/api/deck/stop` | Stop Deck enrichment |
| POST | `/api/cache/start` | Start SGDB image cache |
| GET | `/api/cache/status` | Image cache progress |
| POST | `/api/cache/stop` | Stop image cache |

### Images
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/image/{appid}/hero` | Serve hero image |
| GET | `/api/image/{appid}/options` | All image alternatives |
| GET | `/api/image/{appid}/random` | Random image (shuffle) |
| POST | `/api/image/{appid}/select` | Select specific image |
| POST | `/api/image/{appid}/upload` | Upload custom image |
| GET | `/api/shuffle-cache/estimate` | Pre-download size estimate |
| POST | `/api/shuffle-cache/start` | Start shuffle pre-download |
| GET | `/api/shuffle-cache/status` | Pre-download progress |
| POST | `/api/shuffle-cache/stop` | Stop pre-download |

### Configuration & System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/config` | Full configuration |
| POST | `/api/config` | Update config keys |
| GET | `/api/presets` | Saved filter presets |
| POST | `/api/presets` | Create/update preset |
| DELETE | `/api/presets/{id}` | Delete preset |
| GET | `/api/logs` | Log viewer (tail/filter/search) |
| POST | `/api/logs/level` | Change log level at runtime |

## Project Structure

```
PSS/
├── pss/
│   ├── __init__.py
│   ├── database.py          # SQLite schema v5 + migrations + queries
│   └── server.py            # FastAPI server, workers, auth, all API
├── web/
│   ├── screensaver.html     # Ken Burns slideshow (multi-row, shuffle)
│   ├── customizer.html      # 3-tab config UI (Games/Display/Settings)
│   ├── login.html           # Steam OpenID login page
│   └── setup.html           # First-run account detection
├── data/                    # SQLite database + image cache (gitignored)
├── logs/                    # Server logs with archive (gitignored)
├── Install-PSS.ps1          # Windows first-time setup (PowerShell)
├── Start-PSS.ps1            # Windows foreground launcher (PowerShell)
├── Update-PSS.ps1           # Windows updater (PowerShell, preserves data/)
├── pss-service.ps1          # Windows service manager (Task Scheduler)
├── pss-service.sh           # Linux service manager (systemd)
├── install.sh               # Linux/macOS first-time setup
├── start.sh                 # Linux/macOS server launcher
├── update.sh                # Linux/macOS updater
├── pss_start.pyw            # Headless Windows launcher
├── migrate_v2.py            # JSON → SQLite migration tool
├── requirements.txt
├── .env.example
├── VERSION                  # Current commit hash
└── .gitignore
```

## Database Schema

SQLite with 5 schema versions, auto-migrating on startup:
- **accounts** — Steam accounts with persona names and API keys
- **games** — core library (appid, name, type, playtime, installed status)
- **enrichment** — Store/SteamSpy/Deck data per game
- **image_cache** — SGDB image metadata with selection tracking
- **display_elements** — per-account element ordering with multi-row support
- **config** — key-value config with scoping (global, per-account)
- **presets** — saved filter/sort combinations
- **exclusion_snapshots** — undo history for bulk operations

## License

MIT
