"""PSS Server -- FastAPI replacement for game_server.py v2."""

import json, os, re, logging, threading, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from pss.database import (
    init_db, get_active_account, upsert_account, get_games, upsert_games,
    upsert_enrichment, get_unenriched_appids, get_enrichment_count,
    get_exclusions, set_exclusions, get_full_config, get_config,
    set_config, set_display_elements, MUTABLE_CONFIG_KEYS
)

PSS_ROOT = Path(__file__).parent.parent
DATA_DIR = PSS_ROOT / "data"
WEB_DIR = PSS_ROOT / "web"
LOG_DIR = PSS_ROOT / "logs"
DB_PATH = DATA_DIR / "pss.db"

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "pss_server.log"), logging.StreamHandler()]
)
log = logging.getLogger("pss")

STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "")
STEAM_PATH = os.environ.get("STEAM_PATH", r"C:\Program Files (x86)\Steam")

enrichment_state = {
    "running": False, "stop_requested": False,
    "total": 0, "completed": 0, "errors": 0, "skipped": 0,
    "current_game": "", "current_appid": 0,
    "started_at": None, "eta_seconds": 0,
    "phase": "idle", "message": "", "rate_delay": 1.5
}
enrichment_lock = threading.Lock()
enrichment_thread = None


def fetch_steam_library(api_key, steamid):
    url = (f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
           f"?key={api_key}&steamid={steamid}"
           f"&include_appinfo=1&include_played_free_games=1&format=json")
    log.info("Fetching Steam library...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PSS/0.1"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        raw = data.get("response", {}).get("games", [])
        log.info(f"Fetched {len(raw)} games from Steam API")
        return process_games(raw)
    except Exception as e:
        log.error(f"Steam API fetch failed: {e}")
        return None


def get_installed_appids():
    manifests_dir = Path(STEAM_PATH) / "steamapps"
    installed = set()
    if not manifests_dir.exists():
        return installed
    for m in manifests_dir.glob("appmanifest_*.acf"):
        try:
            content = m.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                if '"appid"' in line:
                    installed.add(int(line.split('"')[3]))
                    break
        except Exception:
            pass
    return installed


def parse_loginusers_vdf() -> tuple[str, str] | tuple[None, None]:
    """Read SteamID64 and persona name from Steam's loginusers.vdf."""
    vdf_path = Path(STEAM_PATH) / "config" / "loginusers.vdf"
    if not vdf_path.exists():
        log.warning(f"loginusers.vdf not found at {vdf_path}")
        return None, None
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="ignore")
        # Find the most recently logged-in user (MostRecent=1)
        # VDF is a simple nested key-value format
        current_id = None
        current_name = None
        best_id = None
        best_name = None
        most_recent = False
        for line in text.splitlines():
            line = line.strip().strip('"')
            # SteamID64 lines are bare numbers as keys
            if re.match(r"^7656\d{13}$", line):
                if current_id and most_recent:
                    best_id, best_name = current_id, current_name
                current_id = line
                current_name = None
                most_recent = False
            elif '"PersonaName"' in line or '"personaname"' in line:
                parts = line.split('"')
                if len(parts) >= 4:
                    current_name = parts[3]
            elif '"MostRecent"' in line or '"mostrecent"' in line:
                if '"1"' in line:
                    most_recent = True
        # Check last entry
        if current_id and most_recent:
            best_id, best_name = current_id, current_name
        # If no MostRecent flag found, use first account
        if not best_id and current_id:
            best_id = current_id
            best_name = current_name
        if best_id:
            log.info(f"Detected Steam account: {best_id} ({best_name or 'unknown'})")
        return best_id, best_name
    except Exception as e:
        log.error(f"Failed to parse loginusers.vdf: {e}")
        return None, None


def process_games(raw_games):
    EXPLICIT_DESCRIPTORS = {3}
    EXPLICIT_KEYWORDS = [
        'sex with', 'hentai', 'nukitashi', 'genital jousting', 'huniepop',
        'deep space waifu', 'sakura swim', 'lewd', 'uncensor', 'strip poker', 'oppai'
    ]
    installed = get_installed_appids()
    processed = []
    for g in raw_games:
        appid = g["appid"]
        name = g.get("name", "")
        descriptors = set(g.get("content_descriptorids", []))
        is_nsfw = bool(descriptors & EXPLICIT_DESCRIPTORS) or \
                  any(kw in name.lower() for kw in EXPLICIT_KEYWORDS)
        pt = g.get("playtime_forever", 0)
        pt_win = g.get("playtime_windows_forever", 0)
        pt_linux = g.get("playtime_linux_forever", 0)
        pt_deck = g.get("playtime_deck_forever", 0)
        pt_mac = g.get("playtime_mac_forever", 0)
        rtime = g.get("rtime_last_played", 0)
        last_played = datetime.utcfromtimestamp(rtime).strftime("%Y-%m-%d") if rtime > 0 else None
        dh = {"Windows": pt_win/60, "Linux": pt_linux/60, "Deck": pt_deck/60, "Mac": pt_mac/60}
        primary = max(dh, key=dh.get) if pt > 0 else None
        if primary and dh[primary] == 0: primary = None
        processed.append({
            "appid": appid, "name": name,
            "playtime_hours": round(pt/60, 1), "playtime_windows_hours": round(pt_win/60, 1),
            "playtime_linux_hours": round(pt_linux/60, 1), "playtime_deck_hours": round(pt_deck/60, 1),
            "playtime_mac_hours": round(pt_mac/60, 1),
            "last_played": last_played, "last_played_ts": rtime,
            "primary_device": primary, "installed_htpc": appid in installed,
            "ever_played": pt > 0, "nsfw_auto": is_nsfw,
            "hero_2x": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_hero_2x.jpg",
            "hero_1x": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_hero.jpg",
            "header": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
            "logo": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/logo.png",
            "store_url": f"https://store.steampowered.com/app/{appid}"
        })
    processed.sort(key=lambda x: x["name"].lower())
    return processed


def fetch_app_details(appid):
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=ch&l=english"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "PSS/0.1", "Accept-Language": "en-US,en;q=0.9"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        entry = data.get(str(appid), {})
        if not entry.get("success"): return None
        d = entry.get("data", {})
        genres = [g["description"] for g in d.get("genres", [])]
        categories = [c["description"] for c in d.get("categories", [])]
        meta = d.get("metacritic", {})
        release = d.get("release_date", {})
        controller = "none"
        for cat in categories:
            cl = cat.lower()
            if "full controller" in cl: controller = "full"; break
            elif "partial controller" in cl or "controller" in cl: controller = "partial"
        return {
            "genres": genres[:5], "categories": categories,
            "developer": ", ".join(d.get("developers", [])[:2]),
            "publisher": ", ".join(d.get("publishers", [])[:2]),
            "release_date": release.get("date", ""),
            "coming_soon": release.get("coming_soon", False),
            "metacritic_score": meta.get("score"),
            "short_description": d.get("short_description", "")[:200],
            "controller_support": controller,
            "vr_support": any("vr" in c.lower() for c in categories),
            "native_platforms": d.get("platforms", {}),
            "screenshots": [s["path_thumbnail"] for s in d.get("screenshots", [])[:3]],
            "type": d.get("type", "game"), "is_free": d.get("is_free", False),
            "enriched_at": datetime.utcnow().isoformat()
        }
    except urllib.error.HTTPError as e:
        return "RATE_LIMITED" if e.code == 429 else None
    except Exception:
        return None


def enrichment_worker():
    global enrichment_state
    account = get_active_account()
    if not account:
        with enrichment_lock:
            enrichment_state.update(phase="error", message="No active account", running=False)
        return
    to_enrich = get_unenriched_appids(account["steamid64"])
    already_done = get_enrichment_count()
    with enrichment_lock:
        enrichment_state.update(total=len(to_enrich), completed=0, errors=0, skipped=already_done,
            phase="running", message=f"Enriching {len(to_enrich)} games ({already_done} already done)",
            started_at=time.time())
    log.info(f"Enrichment starting: {len(to_enrich)} to process, {already_done} cached")
    rate_delay = enrichment_state.get("rate_delay", 1.5)
    consecutive_errors = 0
    for i, (appid, name) in enumerate(to_enrich):
        if enrichment_state["stop_requested"]:
            with enrichment_lock:
                enrichment_state.update(phase="stopped", message=f"Stopped at {i}/{len(to_enrich)}", running=False)
            return
        with enrichment_lock:
            enrichment_state["current_game"] = name
            enrichment_state["current_appid"] = appid
            enrichment_state["completed"] = i
            elapsed = time.time() - enrichment_state["started_at"]
            if i > 0:
                enrichment_state["eta_seconds"] = int((len(to_enrich) - i) * (elapsed / i))
        result = fetch_app_details(appid)
        if result == "RATE_LIMITED":
            with enrichment_lock:
                enrichment_state.update(phase="rate_limited", message=f"Rate limited, waiting 60s... ({i}/{len(to_enrich)})")
            time.sleep(60)
            result = fetch_app_details(appid)
            with enrichment_lock: enrichment_state["phase"] = "running"
        if result and result != "RATE_LIMITED":
            upsert_enrichment(appid, result)
            consecutive_errors = 0
        elif result is None:
            with enrichment_lock: enrichment_state["errors"] += 1
            consecutive_errors += 1
            if consecutive_errors >= 10:
                with enrichment_lock:
                    enrichment_state.update(phase="error", message="10 consecutive errors, pausing 120s")
                time.sleep(120); consecutive_errors = 0
        if (i + 1) % 25 == 0:
            log.info(f"Enrichment checkpoint: {i+1}/{len(to_enrich)}")
        time.sleep(rate_delay)
    with enrichment_lock:
        total = get_enrichment_count()
        enrichment_state.update(completed=len(to_enrich), phase="complete", running=False,
            message=f"Done! {total} games enriched, {enrichment_state['errors']} errors")
    log.info(f"Enrichment complete: {total} total, {enrichment_state['errors']} errors")


@asynccontextmanager
async def lifespan(app):
    init_db(str(DB_PATH))
    log.info(f"Database initialized at {DB_PATH}")
    global STEAM_API_KEY, STEAM_PATH
    env_file = PSS_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip(); value = value.strip().strip('"').strip("'")
                if key == "STEAM_API_KEY": STEAM_API_KEY = value
                elif key == "STEAM_PATH": STEAM_PATH = value
    config = get_config("global")
    STEAM_PATH = config.get("steam_path", STEAM_PATH)
    account = get_active_account()
    if not account and STEAM_API_KEY:
        # First run: detect Steam account and auto-fetch library
        steamid, persona = parse_loginusers_vdf()
        if steamid:
            upsert_account(steamid, persona_name=persona or "Owner", is_active=True)
            account = get_active_account()
            log.info(f"Auto-created account: {steamid} ({persona or 'Owner'})")
            # Fetch library on first run
            games = fetch_steam_library(STEAM_API_KEY, steamid)
            if games:
                count = upsert_games(steamid, games)
                log.info(f"First-run library fetch: {count} games loaded")
            else:
                log.error("First-run library fetch failed")
        else:
            log.warning("Could not detect Steam account — start enrichment manually after setup")
    elif account:
        log.info(f"Active account: {account['steamid64']} ({account.get('persona_name', 'unknown')})")
    yield
    log.info("PSS server shutting down")


app = FastAPI(title="PSS", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/")
async def root():
    return RedirectResponse(url="/customizer")

@app.get("/screensaver", response_class=HTMLResponse)
async def screensaver():
    p = WEB_DIR / "screensaver.html"
    if not p.exists(): return HTMLResponse("Not found", status_code=404)
    return HTMLResponse(content=p.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

@app.get("/customizer", response_class=HTMLResponse)
async def customizer():
    p = WEB_DIR / "customizer.html"
    if not p.exists(): return HTMLResponse("Not found", status_code=404)
    return HTMLResponse(content=p.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

@app.get("/api/games")
async def api_games():
    acct = get_active_account()
    return JSONResponse(get_games(acct["steamid64"]) if acct else [])

@app.get("/api/excluded")
async def api_excluded_get():
    acct = get_active_account()
    return JSONResponse({"excluded": get_exclusions(acct["steamid64"]) if acct else []})

@app.post("/api/excluded")
async def api_excluded_post(request: Request):
    acct = get_active_account()
    if not acct: return JSONResponse({"error": "No active account"}, status_code=400)
    try: data = await request.json()
    except: return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    if "excluded" not in data or not isinstance(data["excluded"], list):
        return JSONResponse({"error": "Expected {excluded: [int, ...]}"}, status_code=400)
    count = set_exclusions(acct["steamid64"], data["excluded"])
    log.info(f"Exclusion list updated: {count} excluded")
    return JSONResponse({"ok": True, "count": count})

@app.get("/api/config")
async def api_config_get():
    return JSONResponse(get_full_config())

@app.post("/api/config")
async def api_config_post(request: Request):
    acct = get_active_account()
    try: updates = await request.json()
    except: return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    de = updates.pop("display_elements", None)
    filtered = {k: v for k, v in updates.items() if k in MUTABLE_CONFIG_KEYS}
    if not filtered and de is None:
        return JSONResponse({"error": "No valid config keys"}, status_code=400)
    updated = set_config(filtered) if filtered else []
    if de is not None and acct:
        set_display_elements(acct["steamid64"], de)
        updated.append("display_elements")
    log.info(f"Config updated: {updated}")
    return JSONResponse({"ok": True, "updated": updated})

@app.post("/api/refresh-library")
async def api_refresh_library():
    acct = get_active_account()
    if not acct: return JSONResponse({"error": "No active account"}, status_code=400)
    if not STEAM_API_KEY: return JSONResponse({"error": "No STEAM_API_KEY"}, status_code=500)
    games = fetch_steam_library(STEAM_API_KEY, acct["steamid64"])
    if games is not None:
        return JSONResponse({"ok": True, "count": upsert_games(acct["steamid64"], games)})
    return JSONResponse({"error": "Steam API fetch failed"}, status_code=500)

@app.post("/api/enrichment/start")
async def api_enrichment_start():
    global enrichment_thread
    if enrichment_state["running"]: return JSONResponse({"error": "Already running"}, status_code=409)
    with enrichment_lock:
        enrichment_state.update(running=True, stop_requested=False, phase="starting",
                                message="Starting enrichment...", errors=0)
    enrichment_thread = threading.Thread(target=enrichment_worker, daemon=True)
    enrichment_thread.start()
    return JSONResponse({"ok": True, "message": "Enrichment started"})

@app.get("/api/enrichment/status")
async def api_enrichment_status():
    with enrichment_lock: return JSONResponse(dict(enrichment_state))

@app.post("/api/enrichment/stop")
async def api_enrichment_stop():
    if not enrichment_state["running"]: return JSONResponse({"error": "Not running"}, status_code=409)
    with enrichment_lock:
        enrichment_state.update(stop_requested=True, message="Stopping...")
    return JSONResponse({"ok": True, "message": "Stop requested"})


def main():
    init_db(str(DB_PATH))
    config = get_config("global")
    port = config.get("server_port", 8787)
    if isinstance(port, str): port = int(port)
    log.info(f"PSS Server v0.1.0 on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=False)

if __name__ == "__main__":
    main()
