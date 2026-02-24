# PSS — Plexified Steam Screensaver

A cinematic Ken Burns slideshow screensaver for your Steam game library, with a web-based customizer UI.

## Features

- **Ken Burns slideshow** — 8 animation variants with configurable intensity
- **14 display elements** — installed badge, playtime, device breakdown, genres, developer, metacritic, controller/VR support, Steam Deck compatibility, ProtonDB tier, platforms — all toggleable and reorderable
- **Web customizer** — 3-tab UI: Games (inclusion/exclusion with bulk operations), Display (element configuration), Settings (timing, enrichment, data management)
- **Multi-source enrichment:**
  - **Steam Store** — genres, developer, metacritic, controller/VR support, descriptions, screenshots
  - **SteamSpy** — owner counts, global avg playtime, review counts
  - **Steam Deck + ProtonDB** — Valve's official Deck compatibility + community Proton tier
  - **IStoreService type correction** — proper app classification (game/software/tool/dlc/demo/music) from Steam's authoritative catalog
- **Library management** — presets, filters (type, genre, installed, played, enriched, NSFW, shovelware, Deck status), sorting, bulk include/exclude
- **Shovelware detection** — configurable 6-signal scoring (low playtime, few reviews, poor ratio, low owners, unplayed, no metacritic)
- **NSFW auto-detection** — auto-excludes explicit content from screensaver pool
- **Complete library coverage** — Steam API v1 + local manifest scan for tools/software not in API
- **Auto-enrichment** — libraries under configurable threshold auto-enrich on first startup

## Requirements

- **Python 3.11+**
- **Steam** installed with games
- **Steam Web API Key** — get one at https://steamcommunity.com/dev/apikey

## Quick Start

### 1. Clone and install dependencies

```bash
git clone https://github.com/Rayce185/PlexifiedSteamScreensaver.git
cd PSS
pip install -r requirements.txt
```

### 2. Configure

Copy the example env file and fill in your Steam API key:

```bash
copy .env.example .env
```

Edit `.env`:
```
STEAM_API_KEY=your_key_here
STEAM_PATH=C:\Program Files (x86)\Steam
```

### 3. First run

```bash
python -m pss.server
```

On first launch the server will:
- Create the SQLite database in `data/pss.db`
- Detect your Steam account from `loginusers.vdf`
- Fetch your game library from Steam API v1 (including free games)
- Scan local manifests for installed tools/software not in the API

### 4. Open in browser

- **Customizer**: http://localhost:8787/customizer
- **Screensaver**: http://localhost:8787/screensaver

### 5. Enrich your library (recommended)

In the customizer, go to **Settings** and run enrichment in order:

1. **Steam Enrichment** — genres, developer, metacritic, controller/VR (~25 min for 1000 games)
2. **SteamSpy Enrichment** — owner counts, playtime stats, reviews (~4 min for 1000 games)
3. **Deck & ProtonDB Enrichment** — Deck compatibility, ProtonDB tier, type correction (~7 min for 1000 games)

## Updating

Use `UPDATE.bat` (Windows) to download the latest version while preserving your `data/` directory.

## Running as a background service

### Windows (Task Scheduler)

The `pss_start.pyw` launcher runs the server without a console window:

```powershell
$action = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument "C:\path\to\PSS\pss_start.pyw" -WorkingDirectory "C:\path\to\PSS"
$trigger = New-ScheduledTaskTrigger -AtLogon
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "PSS_Server" -Action $action -Trigger $trigger -Settings $settings -Force
```

Or just run `run.bat` for a console session.

## Migration from v2 (JSON prototype)

If you have data from the original prototype (games.json, excluded.json, etc.):

```bash
python migrate_v2.py --source C:\path\to\old\homebrew --db data\pss.db
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/screensaver` | Screensaver HTML |
| GET | `/customizer` | Customizer UI |
| GET | `/api/games` | Full game library (merged with enrichment) |
| GET | `/api/excluded` | Excluded appid list |
| POST | `/api/excluded` | Update exclusion list |
| POST | `/api/toggle-exclusion` | Toggle single game exclusion |
| POST | `/api/bulk-exclusion` | Bulk include/exclude |
| GET | `/api/config` | Configuration |
| POST | `/api/config` | Update configuration |
| GET | `/api/presets` | Saved filter presets |
| POST | `/api/presets` | Create/update preset |
| DELETE | `/api/presets/{id}` | Delete preset |
| GET | `/api/filter-values` | Distinct filterable values |
| POST | `/api/refresh-library` | Re-fetch from Steam API |
| POST | `/api/enrichment/start` | Start Store enrichment |
| GET | `/api/enrichment/status` | Store enrichment progress |
| POST | `/api/enrichment/stop` | Stop Store enrichment |
| POST | `/api/steamspy/start` | Start SteamSpy enrichment |
| GET | `/api/steamspy/status` | SteamSpy progress |
| POST | `/api/steamspy/stop` | Stop SteamSpy enrichment |
| POST | `/api/deck/start` | Start Deck/ProtonDB enrichment |
| GET | `/api/deck/status` | Deck enrichment progress |
| POST | `/api/deck/stop` | Stop Deck enrichment |

## Project Structure

```
PSS/
├── pss/
│   ├── __init__.py
│   ├── database.py        # SQLite schema v4 + query helpers
│   └── server.py          # FastAPI server + enrichment workers
├── web/
│   ├── screensaver.html   # Ken Burns slideshow (14 display elements)
│   └── customizer.html    # 3-tab configuration UI
├── data/                  # SQLite database (gitignored)
├── logs/                  # Server logs (gitignored)
├── migrate_v2.py          # JSON → SQLite migration tool
├── pss_start.pyw          # Headless Windows launcher
├── run.bat                # Console launcher
├── UPDATE.bat             # Git-free updater (preserves data/)
├── requirements.txt
├── .env.example
└── .gitignore
```

## License

MIT
