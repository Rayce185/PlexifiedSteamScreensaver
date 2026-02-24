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
    upsert_enrichment, upsert_steamspy, upsert_deck_protondb,
    get_unenriched_appids, get_steamspy_unenriched_appids,
    get_deck_unenriched_appids, get_all_enriched_appids,
    get_enrichment_count, bulk_update_app_types,
    get_exclusions, set_exclusions, toggle_exclusion, bulk_set_exclusions,
    get_full_config, get_config, set_config, set_display_elements,
    get_presets, save_preset, delete_preset, get_distinct_values,
    MUTABLE_CONFIG_KEYS
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
    "phase": "idle", "message": "", "rate_delay": 1.5,
    "error_details": []
}
enrichment_lock = threading.Lock()
enrichment_thread = None

steamspy_state = {
    "running": False, "stop_requested": False,
    "total": 0, "completed": 0, "errors": 0, "skipped": 0,
    "current_game": "", "current_appid": 0,
    "started_at": None, "eta_seconds": 0,
    "phase": "idle", "message": ""
}
steamspy_lock = threading.Lock()
steamspy_thread = None

deck_state = {
    "running": False, "stop_requested": False,
    "total": 0, "completed": 0, "errors": 0, "skipped": 0,
    "current_game": "", "current_appid": 0,
    "started_at": None, "eta_seconds": 0,
    "phase": "idle", "message": "",
    "types_corrected": 0
}
deck_lock = threading.Lock()
deck_thread = None


def fetch_steamspy_data(appid):
    url = f"https://steamspy.com/api.php?request=appdetails&appid={appid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PSS/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if not data or data.get("name") == "Unknown":
            return None
        data["enriched_at"] = datetime.utcnow().isoformat()
        return data
    except Exception:
        return None


def steamspy_worker():
    global steamspy_state
    account = get_active_account()
    if not account:
        with steamspy_lock:
            steamspy_state.update(phase="error", message="No active account", running=False)
        return
    to_enrich = get_steamspy_unenriched_appids(account["steamid64"])
    with steamspy_lock:
        steamspy_state.update(total=len(to_enrich), completed=0, errors=0,
            phase="running", message=f"Fetching SteamSpy data for {len(to_enrich)} games",
            started_at=time.time())
    log.info(f"SteamSpy enrichment starting: {len(to_enrich)} to process")
    for i, (appid, name) in enumerate(to_enrich):
        if steamspy_state["stop_requested"]:
            with steamspy_lock:
                steamspy_state.update(phase="stopped", message=f"Stopped at {i}/{len(to_enrich)}", running=False)
            return
        with steamspy_lock:
            steamspy_state["current_game"] = name
            steamspy_state["current_appid"] = appid
            steamspy_state["completed"] = i
            elapsed = time.time() - steamspy_state["started_at"]
            if i > 0:
                steamspy_state["eta_seconds"] = int((len(to_enrich) - i) * (elapsed / i))
        result = fetch_steamspy_data(appid)
        if result:
            upsert_steamspy(appid, result)
        else:
            with steamspy_lock:
                steamspy_state["skipped"] += 1
        # SteamSpy allows ~4 req/sec, use 0.3s to be safe
        time.sleep(0.3)
        if (i + 1) % 100 == 0:
            log.info(f"SteamSpy checkpoint: {i+1}/{len(to_enrich)}")
    with steamspy_lock:
        steamspy_state.update(completed=len(to_enrich), phase="complete", running=False,
            message=f"Done! {len(to_enrich)} processed, {steamspy_state['skipped']} skipped")
    log.info(f"SteamSpy enrichment complete: {len(to_enrich)} processed, "
             f"{steamspy_state['skipped']} skipped, {steamspy_state['errors']} errors")


def fetch_steam_library(api_key, steamid):
    url = (f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
           f"?key={api_key}&steamid={steamid}"
           f"&include_appinfo=1&include_played_free_games=1&skip_unvetted_apps=false&include_free_sub=1&format=json")
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


def scan_local_manifests():
    """Scan appmanifest_*.acf for all locally installed apps (including tools/software/soundtracks).
    Returns dict of {appid: name}."""
    manifests_dir = Path(STEAM_PATH) / "steamapps"
    result = {}
    if not manifests_dir.exists():
        return result
    for m in manifests_dir.glob("appmanifest_*.acf"):
        try:
            text = m.read_text(encoding="utf-8", errors="ignore")
            appid = None
            name = None
            for line in text.splitlines():
                if '"appid"' in line:
                    appid = int(line.split('"')[3])
                elif '"name"' in line:
                    name = line.split('"')[3]
            if appid and name:
                result[appid] = name
        except Exception:
            pass
    return result


def get_installed_appids():
    """Returns set of installed appids (for marking installed status)."""
    return set(scan_local_manifests().keys())


def parse_loginusers_vdf() -> tuple[str, str] | tuple[None, None]:
    """Read SteamID64 and persona name from Steam's loginusers.vdf."""
    vdf_path = Path(STEAM_PATH) / "config" / "loginusers.vdf"
    if not vdf_path.exists():
        log.warning(f"loginusers.vdf not found at {vdf_path}")
        return None, None
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="ignore")
        current_id = None
        current_name = None
        best_id = None
        best_name = None
        most_recent = False
        for line in text.splitlines():
            line = line.strip().strip('"')
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
        if current_id and most_recent:
            best_id, best_name = current_id, current_name
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
    # Supplement with locally-installed apps not in API response (tools, software, soundtracks)
    api_appids = {g["appid"] for g in processed}
    local_apps = scan_local_manifests()
    supplemented = 0
    for appid, name in local_apps.items():
        if appid not in api_appids:
            processed.append({
                "appid": appid, "name": name,
                "playtime_hours": 0, "playtime_windows_hours": 0,
                "playtime_linux_hours": 0, "playtime_deck_hours": 0,
                "playtime_mac_hours": 0,
                "last_played": None, "last_played_ts": 0,
                "primary_device": None, "installed_htpc": True,
                "ever_played": False, "nsfw_auto": False,
                "hero_2x": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_hero_2x.jpg",
                "hero_1x": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_hero.jpg",
                "header": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
                "logo": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/logo.png",
                "store_url": f"https://store.steampowered.com/app/{appid}"
            })
            supplemented += 1
    if supplemented:
        log.info(f"Added {supplemented} locally-installed apps not in Steam API (tools/software/soundtracks)")

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
        if e.code == 429: return "RATE_LIMITED"
        if e.code in (404, 403): return None  # delisted/blocked = skip
        return "ERROR"  # real server error
    except urllib.error.URLError:
        return "ERROR"  # network error
    except Exception:
        return None  # parse error = probably no store page


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
        enrichment_state.update(total=len(to_enrich), completed=0, errors=0, skipped=0, error_details=[],
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
            # 404/no store page = skipped (delisted/removed), not an error
            with enrichment_lock: enrichment_state["skipped"] += 1
            consecutive_errors = 0  # skips are expected, don't count toward pause
        elif result == "ERROR":
            with enrichment_lock:
                enrichment_state["errors"] += 1
                if len(enrichment_state["error_details"]) < 50:
                    enrichment_state["error_details"].append(f"{appid} ({name})")
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
            message=f"Done! {total} enriched, {enrichment_state['skipped']} skipped, {enrichment_state['errors']} errors")
    log.info(f"Enrichment complete: {total} total, {enrichment_state['skipped']} skipped, {enrichment_state['errors']} errors")



def fetch_deck_compatibility(appid):
    """Fetch Steam Deck compatibility rating for an app."""
    url = f"https://store.steampowered.com/saleaction/ajaxgetdeckappcompatibilityreport?nAppID={appid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PSS/0.2"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if not data.get("success"):
            return None
        results = data.get("results", {})
        category = results.get("resolved_category", 0)  # 0=Unknown, 1=Unsupported, 2=Playable, 3=Verified
        # Check loc_tokens for type hints
        type_hint = None
        for item in results.get("resolved_items", []) + results.get("steamos_resolved_items", []):
            tok = item.get("loc_token", "")
            if "_Software" in tok:
                type_hint = "software"
            elif "_Tool" in tok:
                type_hint = "tool"
        return {"deck_verified": category, "type_hint": type_hint}
    except Exception:
        return None


def fetch_protondb_tier(appid):
    """Fetch ProtonDB community compatibility tier."""
    url = f"https://www.protondb.com/api/v1/reports/summaries/{appid}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PSS/0.2"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return {
            "protondb_tier": data.get("tier"),
            "protondb_confidence": data.get("confidence"),
            "protondb_total": data.get("total", 0)
        }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"protondb_tier": "pending", "protondb_confidence": None, "protondb_total": 0}
        return None
    except Exception:
        return None


def fetch_type_catalog():
    """Download Steam app catalog from IStoreService/GetAppList for type correction.
    Returns dict of {appid: app_type}."""
    if not STEAM_API_KEY:
        log.warning("No Steam API key for IStoreService type lookup")
        return {}
    type_map = {}
    last_appid = 0
    page = 0
    while True:
        url = (f"https://api.steampowered.com/IStoreService/GetAppList/v1/"
               f"?key={STEAM_API_KEY}&max_results=50000&last_appid={last_appid}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PSS/0.2"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            apps = data.get("response", {}).get("apps", [])
            if not apps:
                break
            for app in apps:
                aid = app.get("appid")
                atype = app.get("type", "").lower()
                # IStoreService uses: game, dlc, music, application, tool, demo, episode, hardware
                # Normalize to our types
                if atype in ("game", "dlc", "music", "demo"):
                    type_map[aid] = atype
                elif atype == "application":
                    type_map[aid] = "software"
                elif atype in ("tool", "config"):
                    type_map[aid] = "tool"
                elif atype:
                    type_map[aid] = atype
            last_appid = apps[-1]["appid"]
            page += 1
            log.info(f"IStoreService catalog page {page}: {len(apps)} apps (total {len(type_map)})")
            if len(apps) < 50000:
                break
        except Exception as e:
            log.error(f"IStoreService catalog fetch failed on page {page}: {e}")
            break
    return type_map


def deck_worker():
    """Combined worker: IStoreService type correction + Deck compatibility + ProtonDB."""
    global deck_state
    account = get_active_account()
    if not account:
        with deck_lock:
            deck_state.update(phase="error", message="No active account", running=False)
        return

    # Phase 1: Type correction from IStoreService catalog
    with deck_lock:
        deck_state.update(phase="types", message="Downloading Steam catalog for type correction...")
    log.info("Deck enrichment Phase 1: fetching IStoreService catalog for type correction")
    catalog = fetch_type_catalog()
    if catalog:
        owned_appids = get_all_enriched_appids(account["steamid64"])
        relevant = {aid: t for aid, t in catalog.items() if aid in owned_appids}
        corrected = bulk_update_app_types(relevant)
        with deck_lock:
            deck_state["types_corrected"] = corrected
        log.info(f"Type correction: {corrected} apps reclassified from {len(relevant)} matches")
    else:
        log.warning("Type correction skipped — catalog download failed or no API key")

    # Phase 2: Deck + ProtonDB per-app enrichment
    to_enrich = get_deck_unenriched_appids(account["steamid64"])
    with deck_lock:
        deck_state.update(total=len(to_enrich), completed=0, errors=0, skipped=0,
            phase="running", message=f"Fetching Deck/ProtonDB data for {len(to_enrich)} games",
            started_at=time.time())
    log.info(f"Deck enrichment Phase 2: {len(to_enrich)} apps to process")

    for i, (appid, name) in enumerate(to_enrich):
        if deck_state["stop_requested"]:
            with deck_lock:
                deck_state.update(phase="stopped", message=f"Stopped at {i}/{len(to_enrich)}", running=False)
            return
        with deck_lock:
            deck_state["current_game"] = name
            deck_state["current_appid"] = appid
            deck_state["completed"] = i
            elapsed = time.time() - deck_state["started_at"]
            if i > 0:
                deck_state["eta_seconds"] = int((len(to_enrich) - i) * (elapsed / i))

        # Fetch both
        deck_data = fetch_deck_compatibility(appid)
        proton_data = fetch_protondb_tier(appid)

        if deck_data is None and proton_data is None:
            with deck_lock:
                deck_state["skipped"] += 1
        else:
            merged = {
                "deck_verified": (deck_data or {}).get("deck_verified", 0),
                "deck_enriched_at": datetime.utcnow().isoformat(),
                "protondb_tier": (proton_data or {}).get("protondb_tier"),
                "protondb_confidence": (proton_data or {}).get("protondb_confidence"),
                "protondb_total": (proton_data or {}).get("protondb_total", 0),
            }
            upsert_deck_protondb(appid, merged)
            # Type hint from Deck endpoint (software/tool detection)
            type_hint = (deck_data or {}).get("type_hint")
            if type_hint:
                from pss.database import update_app_type
                update_app_type(appid, type_hint)

        # Rate limit: ~0.4s per app (2 calls with 0.2s each)
        time.sleep(0.25)
        if (i + 1) % 100 == 0:
            log.info(f"Deck enrichment checkpoint: {i+1}/{len(to_enrich)}")

    with deck_lock:
        deck_state.update(completed=len(to_enrich), phase="complete", running=False,
            message=f"Done! {len(to_enrich)} processed, {deck_state['skipped']} skipped, "
                    f"{deck_state['types_corrected']} types corrected")
    log.info(f"Deck enrichment complete: {len(to_enrich)} processed, "
             f"{deck_state['skipped']} skipped, {deck_state['types_corrected']} types corrected")


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
        steamid, persona = parse_loginusers_vdf()
        if steamid:
            upsert_account(steamid, persona_name=persona or "Owner", is_active=True)
            account = get_active_account()
            log.info(f"Auto-created account: {steamid} ({persona or 'Owner'})")
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
        # Auto-refresh library on startup if configured
        cfg = get_full_config()
        if cfg.get("auto_refresh_on_startup", False) and STEAM_API_KEY:
            log.info("Auto-refreshing library on startup...")
            games = fetch_steam_library(STEAM_API_KEY, account["steamid64"])
            if games:
                count = upsert_games(account["steamid64"], games)
                log.info(f"Auto-refresh complete: {count} games")

    # Auto-enrich on first run if library is small enough
    if account and STEAM_API_KEY:
        cfg = get_full_config()
        threshold = cfg.get("auto_enrich_threshold", 200)
        existing_games = get_games(account["steamid64"])
        unenriched = get_unenriched_appids(account["steamid64"])
        total = len(existing_games)
        need_enrich = len(unenriched)
        if need_enrich > 0 and total <= threshold and not enrichment_state["running"]:
            log.info(f"Auto-enrich: {total} games in library (<= {threshold} threshold), "
                     f"{need_enrich} unenriched — starting automatically")
            enrichment_state.update(running=True, stop_requested=False, phase="starting",
                                    message="Auto-enrichment starting...", errors=0)
            enrichment_thread = threading.Thread(target=enrichment_worker, daemon=True)
            enrichment_thread.start()
        elif need_enrich > 0 and total > threshold:
            log.info(f"Library has {total} games (> {threshold} threshold) — "
                     f"skipping auto-enrich, {need_enrich} unenriched")
    yield
    log.info("PSS server shutting down")


app = FastAPI(title="PSS", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# === PAGE ROUTES ===

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


# === GAMES API ===

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

@app.post("/api/toggle-exclusion")
async def api_toggle_exclusion(request: Request):
    acct = get_active_account()
    if not acct: return JSONResponse({"error": "No active account"}, status_code=400)
    try: data = await request.json()
    except: return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    appid = data.get("appid")
    exclude = data.get("exclude", True)
    if appid is None: return JSONResponse({"error": "Missing appid"}, status_code=400)
    toggle_exclusion(acct["steamid64"], appid, exclude)
    return JSONResponse({"ok": True, "appid": appid, "excluded": exclude})

@app.post("/api/bulk-exclusion")
async def api_bulk_exclusion(request: Request):
    acct = get_active_account()
    if not acct: return JSONResponse({"error": "No active account"}, status_code=400)
    try: data = await request.json()
    except: return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    appids = data.get("appids", [])
    exclude = data.get("exclude", True)
    if not appids: return JSONResponse({"error": "No appids"}, status_code=400)
    count = bulk_set_exclusions(acct["steamid64"], appids, exclude)
    log.info(f"Bulk exclusion: {count} games {'excluded' if exclude else 'included'}")
    return JSONResponse({"ok": True, "count": count, "excluded": exclude})


# === CONFIG API ===

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


# === PRESETS API ===

@app.get("/api/presets")
async def api_presets_get():
    acct = get_active_account()
    if not acct: return JSONResponse([])
    return JSONResponse(get_presets(acct["steamid64"]))

@app.post("/api/presets")
async def api_presets_post(request: Request):
    acct = get_active_account()
    if not acct: return JSONResponse({"error": "No active account"}, status_code=400)
    try: data = await request.json()
    except: return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    name = data.get("name", "").strip()
    if not name: return JSONResponse({"error": "Name required"}, status_code=400)
    preset_id = save_preset(
        acct["steamid64"], name,
        filters=data.get("filters", {}),
        sort_field=data.get("sort_field", "name"),
        sort_dir=data.get("sort_dir", "asc"),
        pinned_filters=data.get("pinned_filters")
    )
    log.info(f"Preset saved: '{name}'")
    return JSONResponse({"ok": True, "id": preset_id, "name": name})

@app.delete("/api/presets/{preset_id}")
async def api_presets_delete(preset_id: int):
    acct = get_active_account()
    if not acct: return JSONResponse({"error": "No active account"}, status_code=400)
    delete_preset(acct["steamid64"], preset_id)
    log.info(f"Preset deleted: {preset_id}")
    return JSONResponse({"ok": True})


# === FILTER VALUES API ===

@app.get("/api/filter-values")
async def api_filter_values():
    acct = get_active_account()
    if not acct: return JSONResponse({})
    return JSONResponse(get_distinct_values(acct["steamid64"]))


# === LIBRARY OPERATIONS ===

@app.post("/api/refresh-library")
async def api_refresh_library():
    acct = get_active_account()
    if not acct: return JSONResponse({"error": "No active account"}, status_code=400)
    if not STEAM_API_KEY: return JSONResponse({"error": "No STEAM_API_KEY"}, status_code=500)
    games = fetch_steam_library(STEAM_API_KEY, acct["steamid64"])
    if games is not None:
        return JSONResponse({"ok": True, "count": upsert_games(acct["steamid64"], games)})
    return JSONResponse({"error": "Steam API fetch failed"}, status_code=500)


# === ENRICHMENT API ===

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


# === STEAMSPY ENRICHMENT API ===

@app.post("/api/steamspy/start")
async def api_steamspy_start():
    global steamspy_thread
    if steamspy_state["running"]: return JSONResponse({"error": "Already running"}, status_code=409)
    with steamspy_lock:
        steamspy_state.update(running=True, stop_requested=False, phase="starting",
                              message="Starting SteamSpy enrichment...", errors=0)
    steamspy_thread = threading.Thread(target=steamspy_worker, daemon=True)
    steamspy_thread.start()
    return JSONResponse({"ok": True, "message": "SteamSpy enrichment started"})

@app.get("/api/steamspy/status")
async def api_steamspy_status():
    with steamspy_lock: return JSONResponse(dict(steamspy_state))

@app.post("/api/steamspy/stop")
async def api_steamspy_stop():
    if not steamspy_state["running"]: return JSONResponse({"error": "Not running"}, status_code=409)
    with steamspy_lock:
        steamspy_state.update(stop_requested=True, message="Stopping...")
    return JSONResponse({"ok": True, "message": "Stop requested"})



# === DECK/PROTONDB ENRICHMENT API ===

@app.post("/api/deck/start")
async def api_deck_start():
    global deck_thread
    if deck_state["running"]: return JSONResponse({"error": "Already running"}, status_code=409)
    with deck_lock:
        deck_state.update(running=True, stop_requested=False, phase="starting",
                          message="Starting Deck/ProtonDB enrichment...", errors=0, skipped=0,
                          types_corrected=0)
    deck_thread = threading.Thread(target=deck_worker, daemon=True)
    deck_thread.start()
    return JSONResponse({"ok": True, "message": "Deck/ProtonDB enrichment started"})

@app.get("/api/deck/status")
async def api_deck_status():
    with deck_lock: return JSONResponse(dict(deck_state))

@app.post("/api/deck/stop")
async def api_deck_stop():
    if not deck_state["running"]: return JSONResponse({"error": "Not running"}, status_code=409)
    with deck_lock:
        deck_state.update(stop_requested=True, message="Stopping...")
    return JSONResponse({"ok": True, "message": "Stop requested"})


def main():
    init_db(str(DB_PATH))
    config = get_config("global")
    port = config.get("server_port", 8787)
    if isinstance(port, str): port = int(port)
    log.info(f"PSS Server v0.2.0 on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=False)

if __name__ == "__main__":
    main()
