# PSS — Plexified Steam Screensaver

A cinematic Ken Burns slideshow screensaver for your Steam game library, with a web-based customizer UI.

## Features

- **Ken Burns slideshow** — 8 animation variants with configurable intensity
- **12 display elements** — installed badge, playtime, device breakdown, genres, developer, metacritic, and more — all toggleable and reorderable
- **Web customizer** — 3-tab UI: Games (inclusion/exclusion), Display (element configuration), Settings (timing, enrichment, data management)
- **Steam Store enrichment** — fetches genres, developer, metacritic score, controller/VR support, descriptions for your library
- **NSFW auto-detection** — auto-excludes explicit content from screensaver pool

## Requirements

- **Python 3.11+**
- **Steam** installed with games
- **Steam Web API Key** — get one at https://steamcommunity.com/dev/apikey

## Quick Start

### 1. Clone and install dependencies

```bash
git clone https://github.com/YOUR_USER/PSS.git
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
- Detect your Steam account
- Fetch your game library from the Steam API

### 4. Open in browser

- **Customizer**: http://localhost:8787/customizer
- **Screensaver**: http://localhost:8787/screensaver

### 5. Enrich your library (optional but recommended)

In the customizer, go to **Settings → Steam Enrichment → Start Enrichment**.
This fetches genres, developer info, metacritic scores etc. from Steam Store pages.
Takes ~25 minutes for a full library due to rate limits.

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

This imports all games, enrichment data, exclusions, display preferences, and settings.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/screensaver` | Screensaver HTML |
| GET | `/customizer` | Customizer UI |
| GET | `/api/games` | Full game library (merged with enrichment) |
| GET | `/api/excluded` | Excluded appid list |
| POST | `/api/excluded` | Update exclusion list |
| GET | `/api/config` | Configuration |
| POST | `/api/config` | Update configuration |
| POST | `/api/refresh-library` | Re-fetch from Steam API |
| POST | `/api/enrichment/start` | Start enrichment |
| GET | `/api/enrichment/status` | Enrichment progress |
| POST | `/api/enrichment/stop` | Stop enrichment |

## Project Structure

```
PSS/
├── pss/
│   ├── __init__.py
│   ├── database.py      # SQLite schema + query helpers
│   └── server.py         # FastAPI server + Steam API + enrichment
├── web/
│   ├── screensaver.html   # Ken Burns slideshow
│   └── customizer.html    # 3-tab configuration UI
├── data/                  # SQLite database (gitignored)
├── logs/                  # Server logs (gitignored)
├── migrate_v2.py          # JSON → SQLite migration tool
├── pss_start.pyw          # Headless Windows launcher
├── run.bat                # Console launcher
├── requirements.txt
├── .env.example
└── .gitignore
```

## License

MIT
