"""PSS Server -- FastAPI replacement for game_server.py v2."""

import json, os, re, logging, threading, time, secrets, random, hashlib, urllib.request, urllib.error, urllib.parse
from pathlib import Path
from datetime import datetime
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from pss.database import (
    init_db, get_active_account, get_all_accounts, set_active_account,
    upsert_account, get_games, upsert_games,
    upsert_enrichment, upsert_steamspy, upsert_deck_protondb,
    get_unenriched_appids, get_steamspy_unenriched_appids,
    get_deck_unenriched_appids, get_all_enriched_appids,
    get_enrichment_count, bulk_update_app_types,
    get_exclusions, set_exclusions, toggle_exclusion, bulk_set_exclusions,
    get_full_config, get_config, set_config, set_display_elements,
    get_account_config, set_account_config, delete_account_config,
    get_presets, save_preset, delete_preset, get_distinct_values,
    snapshot_exclusions, get_exclusion_snapshots, restore_exclusion_snapshot,
    get_cached_hero, get_all_cached_heroes, upsert_image_cache, get_uncached_appids, get_image_cache_stats,
    select_cached_image, delete_cached_images,
    MUTABLE_CONFIG_KEYS
)

PSS_ROOT = Path(__file__).parent.parent
DATA_DIR = PSS_ROOT / "data"
WEB_DIR = PSS_ROOT / "web"
LOG_DIR = PSS_ROOT / "logs"
CACHE_DIR = DATA_DIR / "cache" / "heroes"
DB_PATH = DATA_DIR / "pss.db"

LOG_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Read commit hash from VERSION file
_version = "unknown"
_version_file = PSS_ROOT / "VERSION"
if _version_file.exists():
    _version = _version_file.read_text().strip()[:7]

# Archive logs from older commits
_archive_dir = LOG_DIR / "archive"
for old_log in LOG_DIR.glob("pss*.log"):
    if _version not in old_log.stem:
        _archive_dir.mkdir(parents=True, exist_ok=True)
        old_log.rename(_archive_dir / old_log.name)

_log_ts = datetime.now().strftime("%y%m%d_%H%M%S")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / f"pss_{_log_ts}_{_version}.log"), logging.StreamHandler()]
)
log = logging.getLogger("pss")
log.info(f"PSS version: {_version}")

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


def parse_loginusers_vdf() -> list[dict]:
    """Parse ALL accounts from Steam's loginusers.vdf.
    Returns list of {steamid64, persona_name, most_recent, timestamp}."""
    vdf_path = Path(STEAM_PATH) / "config" / "loginusers.vdf"
    if not vdf_path.exists():
        log.warning(f"loginusers.vdf not found at {vdf_path}")
        return []
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="ignore")
        accounts = []
        current = {}
        for line in text.splitlines():
            stripped = line.strip().strip('"')
            if re.match(r"^7656\d{13}$", stripped):
                if current.get("steamid64"):
                    accounts.append(current)
                current = {"steamid64": stripped, "persona_name": None,
                           "most_recent": False, "timestamp": 0}
            elif current.get("steamid64"):
                if '"PersonaName"' in line or '"personaname"' in line:
                    parts = line.split('"')
                    if len(parts) >= 4:
                        current["persona_name"] = parts[3]
                elif '"MostRecent"' in line or '"mostrecent"' in line:
                    current["most_recent"] = '"1"' in line
                elif '"Timestamp"' in line or '"timestamp"' in line:
                    parts = line.split('"')
                    if len(parts) >= 4:
                        try: current["timestamp"] = int(parts[3])
                        except: pass
        if current.get("steamid64"):
            accounts.append(current)
        if accounts:
            active = [a for a in accounts if a["most_recent"]]
            log.info(f"VDF: {len(accounts)} account(s), active: "
                     f"{active[0]['steamid64'] if active else 'none'}")
        return accounts
    except Exception as e:
        log.error(f"Failed to parse loginusers.vdf: {e}")
        return []


def get_vdf_active() -> tuple[str, str] | tuple[None, None]:
    """Convenience: get the MostRecent account from VDF. Returns (steamid64, persona)."""
    accounts = parse_loginusers_vdf()
    active = [a for a in accounts if a["most_recent"]]
    if active:
        return active[0]["steamid64"], active[0]["persona_name"]
    if accounts:
        return accounts[0]["steamid64"], accounts[0]["persona_name"]
    return None, None


def get_api_key_for(steamid64: str) -> str:
    """Resolve API key for an account: per-account config -> global .env -> empty."""
    per_account = get_account_config(steamid64, "steam_api_key")
    if per_account:
        return per_account
    return STEAM_API_KEY  # global default from .env


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


# Steam misclassifies some delisted games as "advertising"
_STEAM_TYPE_REMAP = {"advertising": "game", "dlc": "game"}

def _remap_steam_type(t):
    return _STEAM_TYPE_REMAP.get(t, t)


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
            "type": _remap_steam_type(d.get("type", "game")), "is_free": d.get("is_free", False),
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

    # Auto-chain: start Deck enrichment if any apps still need it
    _auto_chain_deck()


def _auto_chain_deck():
    """Start Deck/ProtonDB enrichment automatically after Store enrichment."""
    global deck_thread
    account = get_active_account()
    if not account:
        return
    unenriched = get_deck_unenriched_appids(account["steamid64"])
    if not unenriched:
        log.info("Auto-chain: no Deck-unenriched apps, skipping")
        return
    if deck_state["running"]:
        log.info("Auto-chain: Deck enrichment already running, skipping")
        return
    log.info(f"Auto-chain: starting Deck enrichment for {len(unenriched)} apps")
    with deck_lock:
        deck_state.update(running=True, stop_requested=False, phase="starting",
                          message="Auto-chained from Store enrichment...", errors=0, skipped=0,
                          types_corrected=0)
    deck_thread = threading.Thread(target=deck_worker, daemon=True)
    deck_thread.start()



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


def _fetch_type_page(type_filter, our_type, api_key=None):
    """Paginate IStoreService/GetAppList with a single include_* filter.
    Returns set of appids matching that type."""
    # Build include params: all false EXCEPT the one we want
    ALL_INCLUDES = ["include_games", "include_dlc", "include_software", "include_videos", "include_hardware"]
    params = "&".join(f"{p}={'true' if p == type_filter else 'false'}" for p in ALL_INCLUDES)
    appids = set()
    last_appid = 0
    page = 0
    while True:
        url = (f"https://api.steampowered.com/IStoreService/GetAppList/v1/"
               f"?key={api_key or STEAM_API_KEY}&max_results=50000&last_appid={last_appid}&{params}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PSS/0.2"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            apps = data.get("response", {}).get("apps", [])
            if not apps:
                break
            appids.update(app.get("appid") for app in apps)
            last_appid = apps[-1]["appid"]
            page += 1
            log.info(f"IStoreService {our_type} page {page}: {len(apps)} apps (running total {len(appids)})")
            if len(apps) < 50000:
                break
        except Exception as e:
            log.error(f"IStoreService {our_type} fetch failed on page {page}: {e}")
            break
    return appids


def fetch_type_catalog(api_key=None):
    """Download Steam app catalog from IStoreService/GetAppList for type correction.
    IStoreService does NOT return a type field — you must query per type using include_* filters.
    Returns dict of {appid: app_type} for non-game types only (software, dlc, hardware)."""
    key = api_key or STEAM_API_KEY
    if not key:
        log.warning("No Steam API key for IStoreService type lookup")
        return {}
    type_map = {}
    # Query each non-game type separately; games are the default from appdetails
    for filter_param, our_type in [
        ("include_software", "software"),
        ("include_hardware", "hardware"),
    ]:
        appids = _fetch_type_page(filter_param, our_type, api_key=key)
        for aid in appids:
            type_map[aid] = our_type
        log.info(f"IStoreService {our_type}: {len(appids)} apps cataloged")
    log.info(f"IStoreService type catalog complete: {len(type_map)} non-game apps")
    return type_map


def repair_types():
    """Reset all enriched types to 'game', then apply correct IStoreService corrections.
    Used to recover from type corruption."""
    account = get_active_account()
    if not account:
        return {"error": "No active account"}
    api_key = get_api_key_for(account["steamid64"])
    from pss.database import get_db
    # Step 1: Reset all enriched types to 'game'
    with get_db() as db:
        r = db.execute("UPDATE enrichment SET type = 'game' WHERE type != 'game'")
        reset_count = r.rowcount
    log.info(f"Type repair: reset {reset_count} apps to 'game'")
    # Step 2: Fetch correct catalog
    catalog = fetch_type_catalog(api_key=api_key)
    if not catalog:
        return {"error": "Catalog fetch failed", "reset": reset_count}
    # Step 3: Apply correct non-game types
    owned = get_all_enriched_appids(account["steamid64"])
    relevant = {aid: t for aid, t in catalog.items() if aid in owned}
    corrected = bulk_update_app_types(relevant)
    log.info(f"Type repair: {corrected} apps reclassified from {len(relevant)} matches")
    return {"ok": True, "reset": reset_count, "corrected": corrected}


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
    api_key = get_api_key_for(account["steamid64"])
    log.info("Deck enrichment Phase 1: fetching IStoreService catalog for type correction")
    catalog = fetch_type_catalog(api_key=api_key)
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





# === STEAMGRIDDB + IMAGE CACHE ===

SGDB_API_BASE = "https://www.steamgriddb.com/api/v2"

cache_state = {
    "running": False, "stop_requested": False,
    "total": 0, "completed": 0, "errors": 0, "skipped": 0,
    "current_game": "", "current_appid": 0,
    "started_at": None, "eta_seconds": 0,
    "phase": "idle", "message": "",
    "sgdb_hits": 0, "screenshot_hits": 0, "header_hits": 0
}
cache_lock = threading.Lock()
cache_thread = None


def get_sgdb_key():
    """Get SteamGridDB API key from config."""
    cfg = get_config("global")
    return cfg.get("sgdb_api_key", "")


def fetch_sgdb_heroes(appid, api_key):
    """Fetch hero images from SteamGridDB for a Steam appid."""
    url = f"{SGDB_API_BASE}/heroes/steam/{appid}"
    try:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "PSS/0.3"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if not data.get("success"):
            return []
        heroes = data.get("data", [])
        # Return sorted by score descending
        return sorted(heroes, key=lambda h: h.get("score", 0), reverse=True)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []  # No heroes for this game
        if e.code == 429:
            return "RATE_LIMITED"
        return []
    except Exception:
        return []


def download_image(url, local_path):
    """Download an image URL to local path. Returns True on success."""
    try:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "PSS/0.3"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if len(data) < 1000:  # Too small = probably error page
            return False
        with open(local_path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def get_screenshot_urls(appid):
    """Get full-size screenshot URLs from enrichment data.
    Converts thumbnail URLs (600x338) to full-size (1920x1080)."""
    from pss.database import get_db
    with get_db() as db:
        row = db.execute("SELECT screenshots FROM enrichment WHERE appid = ?", (appid,)).fetchone()
    if not row or not row["screenshots"]:
        return []
    try:
        thumbs = json.loads(row["screenshots"])
    except:
        return []
    full_urls = []
    for t in thumbs:
        # Convert .600x338.jpg or .116x65.jpg to .1920x1080.jpg
        full = re.sub(r'\.\d+x\d+\.', '.1920x1080.', t)
        if full != t:
            full_urls.append(full)
        else:
            full_urls.append(t)  # Use as-is if no size suffix found
    return full_urls


def cache_image_for_appid(appid, name=""):
    """Cache the best 16:9 image for an appid.
    Priority: SteamGridDB heroes (16:9 filtered) > Steam screenshots (16:9 native) > header.
    Returns: 'sgdb', 'screenshot', 'header', or None."""
    cache_dir = CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_path = str(cache_dir / f"{appid}.jpg")

    # Already cached on disk?
    if Path(local_path).exists():
        return "cached"

    sgdb_key = get_sgdb_key()

    # Try SteamGridDB first (heroes, filtered for reasonable aspect ratios)
    if sgdb_key:
        heroes = fetch_sgdb_heroes(appid, sgdb_key)
        if heroes == "RATE_LIMITED":
            return "rate_limited"
        if heroes:
            # Prefer heroes close to 16:9 (ratio 1.5 - 2.0)
            for hero in heroes[:5]:
                w = hero.get("width", 0)
                h = hero.get("height", 0)
                if w > 0 and h > 0:
                    ratio = w / h
                    if ratio > 2.0 or ratio < 1.3:
                        continue  # Skip ultra-wide or too-square
                hero_url = hero.get("url", "")
                if hero_url and download_image(hero_url, local_path):
                    upsert_image_cache(appid, "sgdb", hero_url, local_path,
                                       style=hero.get("style"), score=hero.get("score", 0),
                                       width=hero.get("width"), height=hero.get("height"),
                                       selected=True)
                    return "sgdb"

    # Try Steam screenshots (natively 16:9, 1920x1080)
    ss_urls = get_screenshot_urls(appid)
    for url in ss_urls[:3]:
        if download_image(url, local_path):
            upsert_image_cache(appid, "screenshot", url, local_path, selected=True,
                               width=1920, height=1080)
            return "screenshot"

    # Last resort: Steam header image (460x215, ~2:1 but better than nothing)
    header_url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
    if download_image(header_url, local_path):
        upsert_image_cache(appid, "header", header_url, local_path, selected=True,
                           width=460, height=215)
        return "header"

    return None


def image_cache_worker():
    """Background worker: cache hero images for all games."""
    global cache_state
    account = get_active_account()
    if not account:
        with cache_lock:
            cache_state.update(phase="error", message="No active account", running=False)
        return

    to_cache = get_uncached_appids(account["steamid64"])
    sgdb_key = get_sgdb_key()

    with cache_lock:
        cache_state.update(total=len(to_cache), completed=0, errors=0, skipped=0,
            sgdb_hits=0, screenshot_hits=0, header_hits=0,
            phase="running",
            message=f"Caching heroes for {len(to_cache)} games"
                    + (f" (SteamGridDB {'enabled' if sgdb_key else 'disabled'})"),
            started_at=time.time())
    log.info(f"Image cache starting: {len(to_cache)} to process, SGDB={'yes' if sgdb_key else 'no'}")

    for i, (appid, name) in enumerate(to_cache):
        if cache_state["stop_requested"]:
            with cache_lock:
                cache_state.update(phase="stopped", message=f"Stopped at {i}/{len(to_cache)}", running=False)
            return

        with cache_lock:
            cache_state["current_game"] = name
            cache_state["current_appid"] = appid
            cache_state["completed"] = i
            elapsed = time.time() - cache_state["started_at"]
            if i > 0:
                cache_state["eta_seconds"] = int((len(to_cache) - i) * (elapsed / i))

        result = cache_image_for_appid(appid, name)

        if result == "rate_limited":
            with cache_lock:
                cache_state.update(phase="rate_limited", message="SteamGridDB rate limited, waiting 30s...")
            time.sleep(30)
            result = cache_image_for_appid(appid, name)
            with cache_lock:
                cache_state["phase"] = "running"

        if result == "sgdb":
            with cache_lock: cache_state["sgdb_hits"] += 1
        elif result == "screenshot":
            with cache_lock: cache_state["screenshot_hits"] += 1
        elif result == "header":
            with cache_lock: cache_state["header_hits"] += 1
        elif result is None:
            with cache_lock: cache_state["skipped"] += 1

        # Rate limit: be gentle with SGDB
        delay = 0.4 if sgdb_key else 0.15
        time.sleep(delay)

        if (i + 1) % 100 == 0:
            log.info(f"Image cache checkpoint: {i+1}/{len(to_cache)} "
                     f"(SGDB: {cache_state['sgdb_hits']}, SS: {cache_state['screenshot_hits']}, HDR: {cache_state['header_hits']})")

    with cache_lock:
        cache_state.update(completed=len(to_cache), phase="complete", running=False,
            message=f"Done! SGDB: {cache_state['sgdb_hits']}, Screenshots: {cache_state['screenshot_hits']}, Headers: {cache_state['header_hits']}, "
                    f"skipped: {cache_state['skipped']}")
    log.info(f"Image cache complete: SGDB={cache_state['sgdb_hits']}, "
             f"Screenshots={cache_state['screenshot_hits']}, Headers={cache_state['header_hits']}, skipped={cache_state['skipped']}")


# === AUTH (Steam OpenID) ===
SESSION_COOKIE = "pss_session"
SESSION_EXPIRY_DAYS = 7
STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"

def create_session(steamid64: str) -> str:
    """Create a session token tied to a SteamID64."""
    token = secrets.token_hex(32)
    from datetime import timedelta
    expiry = (datetime.utcnow() + timedelta(days=SESSION_EXPIRY_DAYS)).isoformat()
    set_config({"session_token": token, "session_steamid64": steamid64,
                "session_expiry": expiry}, scope="session")
    return token

def verify_session(token: str) -> dict | None:
    """Verify session token. Returns {steamid64} if valid, None if not."""
    if not token:
        return None
    stored = get_config("session")
    if stored.get("session_token") != token:
        return None
    expiry_str = stored.get("session_expiry", "")
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            if datetime.utcnow() > expiry:
                return None
        except Exception:
            pass
    sid = stored.get("session_steamid64")
    return {"steamid64": sid} if sid else None

def invalidate_sessions():
    """Clear all sessions."""
    from pss.database import get_db
    with get_db() as db:
        db.execute("DELETE FROM config WHERE scope = 'session'")

def has_accounts() -> bool:
    """Check if any accounts exist in DB (setup is complete)."""
    return bool(get_all_accounts())

def get_session_from_request(request) -> dict | None:
    """Extract and verify session from cookie. Returns {steamid64} or None."""
    token = request.cookies.get(SESSION_COOKIE, "")
    return verify_session(token)

def build_openid_redirect(return_to: str, realm: str) -> str:
    """Build Steam OpenID redirect URL."""
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return STEAM_OPENID_URL + "?" + urllib.parse.urlencode(params)

def validate_openid_response(params: dict) -> str | None:
    """Validate Steam OpenID response. Returns SteamID64 if valid, None if not."""
    # Must be a positive assertion
    if params.get("openid.mode") != "id_res":
        return None
    # Verify claimed_id is a Steam URL
    claimed = params.get("openid.claimed_id", "")
    match = re.match(r"^https://steamcommunity\.com/openid/id/(7656\d{13})$", claimed)
    if not match:
        return None
    steamid64 = match.group(1)
    # Verify with Steam (check_authentication)
    verify_params = dict(params)
    verify_params["openid.mode"] = "check_authentication"
    post_data = urllib.parse.urlencode(verify_params).encode()
    try:
        req = urllib.request.Request(STEAM_OPENID_URL, data=post_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
        if "is_valid:true" in body:
            return steamid64
        log.warning(f"Steam OpenID validation failed: {body[:200]}")
        return None
    except Exception as e:
        log.error(f"Steam OpenID verification error: {e}")
        return None


# === VDF WATCHDOG ===
watchdog_state = {
    "last_check": None, "last_active": None, "interval": 30,
    "switches": 0, "running": False
}

async def vdf_watchdog():
    """Background task: poll loginusers.vdf for account switches."""
    watchdog_state["running"] = True
    cfg = get_config("global")
    interval = cfg.get("watchdog_interval", 30)
    watchdog_state["interval"] = interval
    log.info(f"VDF watchdog started (interval={interval}s)")
    while True:
        try:
            await asyncio.sleep(interval)
            steamid, persona = get_vdf_active()
            watchdog_state["last_check"] = datetime.utcnow().isoformat()
            if not steamid:
                continue
            current = get_active_account()
            current_id = current["steamid64"] if current else None
            watchdog_state["last_active"] = steamid
            if steamid != current_id:
                log.info(f"VDF watchdog: account switch detected {current_id} -> {steamid}")
                # Ensure account exists in DB
                upsert_account(steamid, persona_name=persona, is_active=True)
                set_active_account(steamid)
                watchdog_state["switches"] += 1
                # If new account has no games, auto-fetch
                new_acct = get_active_account()
                games = get_games(steamid)
                if not games:
                    api_key = get_api_key_for(steamid)
                    if api_key:
                        log.info(f"Watchdog: new account {steamid} has no games, fetching library...")
                        fetched = fetch_steam_library(api_key, steamid)
                        if fetched:
                            count = upsert_games(steamid, fetched)
                            log.info(f"Watchdog: loaded {count} games for {steamid}")
        except asyncio.CancelledError:
            log.info("VDF watchdog stopped")
            watchdog_state["running"] = False
            return
        except Exception as e:
            log.error(f"VDF watchdog error: {e}")


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
    # Register all VDF accounts in DB
    vdf_accounts = parse_loginusers_vdf()
    for va in vdf_accounts:
        upsert_account(va["steamid64"], persona_name=va["persona_name"],
                       is_active=va["most_recent"])

    account = get_active_account()
    if not account and vdf_accounts:
        # Fallback: activate first VDF account
        first = vdf_accounts[0]
        upsert_account(first["steamid64"], persona_name=first["persona_name"], is_active=True)
        account = get_active_account()

    if account:
        api_key = get_api_key_for(account["steamid64"])
        log.info(f"Active account: {account['steamid64']} ({account.get('persona_name', 'unknown')})")
        if not get_games(account["steamid64"]) and api_key:
            log.info(f"First-run: fetching library for {account['steamid64']}...")
            games = fetch_steam_library(api_key, account["steamid64"])
            if games:
                count = upsert_games(account["steamid64"], games)
                log.info(f"First-run library fetch: {count} games loaded")
            else:
                log.error("First-run library fetch failed")
        else:
            cfg = get_full_config()
            if cfg.get("auto_refresh_on_startup", False) and api_key:
                log.info("Auto-refreshing library on startup...")
                games = fetch_steam_library(api_key, account["steamid64"])
                if games:
                    count = upsert_games(account["steamid64"], games)
                    log.info(f"Auto-refresh complete: {count} games")
    else:
        log.warning("No accounts detected — configure via /customizer")

    api_key_for_enrich = get_api_key_for(account["steamid64"]) if account else ""

    # One-time repair: fix type corruption from IStoreService duplicate param bug
    if account and api_key_for_enrich:
        from pss.database import get_db
        with get_db() as db:
            hw_count = db.execute("SELECT COUNT(*) as c FROM enrichment WHERE type='hardware'").fetchone()["c"]
        if hw_count > 20:  # Normal libraries have 0-2 hardware apps
            log.warning(f"Type corruption detected: {hw_count} hardware apps. Running auto-repair...")
            result = repair_types()
            log.info(f"Auto-repair result: {result}")
        # Remap any stale types that should be 'game'
        with get_db() as db:
            db.execute("UPDATE enrichment SET type = 'game' WHERE type IN ('advertising', 'dlc')")

    # Auto-enrich on first run if library is small enough
    if account and api_key_for_enrich:
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
    # Start VDF watchdog
    watchdog_task = asyncio.create_task(vdf_watchdog())
    yield
    watchdog_task.cancel()
    try: await watchdog_task
    except asyncio.CancelledError: pass
    log.info("PSS server shutting down")


app = FastAPI(title="PSS", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])



# Auth middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Always open: screensaver, static assets, auth endpoints
    open_paths = ("/screensaver", "/api/auth/", "/favicon")
    if any(path.startswith(p) for p in open_paths):
        return await call_next(request)
    if path.startswith("/api/image/") and request.method == "GET":
        return await call_next(request)
    # No accounts in DB = first run, setup page is open
    if not has_accounts():
        if path == "/setup" or path.startswith("/api/accounts"):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"error": "Setup required", "redirect": "/setup"}, status_code=403)
        return RedirectResponse(url="/setup")
    # Setup page locks after accounts exist
    if path == "/setup":
        return RedirectResponse(url="/login")
    # Login page: accessible without session, skip if already authed
    if path == "/login":
        if get_session_from_request(request):
            return RedirectResponse(url="/customizer")
        return await call_next(request)
    # Everything else requires valid Steam session
    session = get_session_from_request(request)
    if not session:
        if path.startswith("/api/"):
            return JSONResponse({"error": "Unauthorized", "redirect": "/login"}, status_code=401)
        return RedirectResponse(url="/login")
    # Verify the logged-in SteamID is a known account
    all_ids = {a["steamid64"] for a in get_all_accounts()}
    if session["steamid64"] not in all_ids:
        invalidate_sessions()
        if path.startswith("/api/"):
            return JSONResponse({"error": "Account not recognized"}, status_code=403)
        return RedirectResponse(url="/login")
    return await call_next(request)


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

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    p = WEB_DIR / "login.html"
    if not p.exists(): return HTMLResponse("<h1>Login page not found</h1>", status_code=500)
    return HTMLResponse(content=p.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

@app.get("/setup", response_class=HTMLResponse)
async def setup_page():
    if has_accounts():
        return RedirectResponse(url="/login")
    p = WEB_DIR / "setup.html"
    if not p.exists(): return HTMLResponse("<h1>Setup page not found</h1>", status_code=500)
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
    snapshot_exclusions(acct["steamid64"], "pre-set-exclusions")
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
    snapshot_exclusions(acct["steamid64"], f"pre-bulk-{'exclude' if exclude else 'include'}")
    count = bulk_set_exclusions(acct["steamid64"], appids, exclude)
    log.info(f"Bulk exclusion: {count} games {'excluded' if exclude else 'included'}")
    return JSONResponse({"ok": True, "count": count, "excluded": exclude})


@app.post("/api/exclusion-snapshot")
async def api_exclusion_snapshot(request: Request):
    """Manually snapshot current exclusion state."""
    acct = get_active_account()
    if not acct: return JSONResponse({"error": "No active account"}, status_code=400)
    try: data = await request.json()
    except: data = {}
    label = data.get("label", "manual")
    count = snapshot_exclusions(acct["steamid64"], label)
    log.info(f"Exclusion snapshot: {count} exclusions saved (label={label})")
    return JSONResponse({"ok": True, "count": count})

@app.get("/api/exclusion-snapshots")
async def api_exclusion_snapshots():
    acct = get_active_account()
    if not acct: return JSONResponse([])
    return JSONResponse(get_exclusion_snapshots(acct["steamid64"]))

@app.post("/api/exclusion-restore/{snapshot_id}")
async def api_exclusion_restore(snapshot_id: int):
    acct = get_active_account()
    if not acct: return JSONResponse({"error": "No active account"}, status_code=400)
    count = restore_exclusion_snapshot(acct["steamid64"], snapshot_id)
    if count is None: return JSONResponse({"error": "Snapshot not found"}, status_code=404)
    log.info(f"Exclusion restored from snapshot {snapshot_id}: {count} exclusions")
    return JSONResponse({"ok": True, "count": count})


# === CONFIG API ===

@app.get("/api/config")
async def api_config_get():
    config = get_full_config()
    acct = get_active_account()
    if acct:
        config["active_account"] = {
            "steamid64": acct["steamid64"],
            "persona_name": acct.get("persona_name", "Unknown")
        }
    return JSONResponse(config)

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
    api_key = get_api_key_for(acct["steamid64"])
    if not api_key: return JSONResponse({"error": "No API key for this account"}, status_code=500)
    games = fetch_steam_library(api_key, acct["steamid64"])
    if games is not None:
        return JSONResponse({"ok": True, "count": upsert_games(acct["steamid64"], games)})
    return JSONResponse({"error": "Steam API fetch failed"}, status_code=500)




# === ACCOUNT MANAGEMENT API ===

@app.get("/api/accounts")
async def api_accounts():
    """List all known accounts with stats."""
    accounts = get_all_accounts()
    # Add whether the global default key is available
    return JSONResponse({
        "accounts": accounts,
        "has_default_key": bool(STEAM_API_KEY),
        "watchdog": dict(watchdog_state)
    })

@app.post("/api/accounts/switch")
async def api_accounts_switch(request: Request):
    """Manually switch active account."""
    try: data = await request.json()
    except: return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    steamid = data.get("steamid64")
    if not steamid: return JSONResponse({"error": "Missing steamid64"}, status_code=400)
    acct = set_active_account(steamid)
    if not acct: return JSONResponse({"error": "Account not found"}, status_code=404)
    log.info(f"Manual account switch to {steamid} ({acct.get('persona_name', 'unknown')})")
    return JSONResponse({"ok": True, "account": acct})

@app.get("/api/accounts/detect")
async def api_accounts_detect():
    """Force re-read VDF and register any new accounts."""
    vdf_accounts = parse_loginusers_vdf()
    for va in vdf_accounts:
        upsert_account(va["steamid64"], persona_name=va["persona_name"],
                       is_active=va["most_recent"])
    # If VDF says a different account is active, switch
    active_vdf = [a for a in vdf_accounts if a["most_recent"]]
    if active_vdf:
        current = get_active_account()
        if not current or current["steamid64"] != active_vdf[0]["steamid64"]:
            set_active_account(active_vdf[0]["steamid64"])
    return JSONResponse({
        "vdf_accounts": vdf_accounts,
        "accounts": get_all_accounts()
    })

@app.post("/api/accounts/{steamid64}/api-key")
async def api_accounts_set_key(steamid64: str, request: Request):
    """Set or validate a per-account API key."""
    try: data = await request.json()
    except: return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    api_key = data.get("api_key", "").strip()
    if not api_key: return JSONResponse({"error": "Missing api_key"}, status_code=400)
    # Validate against Steam API
    test_url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key={api_key}&steamids={steamid64}"
    try:
        req = urllib.request.Request(test_url, headers={"User-Agent": "PSS/0.2"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        players = result.get("response", {}).get("players", [])
        if not players:
            return JSONResponse({"error": "API key valid but no player data returned"}, status_code=400)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return JSONResponse({"error": "Invalid API key (403 Forbidden)"}, status_code=400)
        return JSONResponse({"error": f"Validation failed: HTTP {e.code}"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": f"Validation failed: {e}"}, status_code=500)
    set_account_config(steamid64, "steam_api_key", api_key)
    log.info(f"API key set for account {steamid64}")
    return JSONResponse({"ok": True, "steamid64": steamid64, "validated": True})

@app.delete("/api/accounts/{steamid64}/api-key")
async def api_accounts_delete_key(steamid64: str):
    """Remove per-account API key (falls back to default)."""
    delete_account_config(steamid64, "steam_api_key")
    log.info(f"API key removed for account {steamid64}")
    return JSONResponse({"ok": True, "steamid64": steamid64, "using_default": bool(STEAM_API_KEY)})

@app.get("/api/accounts/active")
async def api_accounts_active():
    """Get current active account info (for frontend polling)."""
    acct = get_active_account()
    if not acct: return JSONResponse({"active": None})
    return JSONResponse({"active": acct})



# === AUTH API (Steam OpenID) ===

@app.get("/api/auth/status")
async def api_auth_status(request: Request):
    """Check setup and auth state."""
    session = get_session_from_request(request)
    return JSONResponse({
        "setup_complete": has_accounts(),
        "authenticated": session is not None,
        "steamid64": session["steamid64"] if session else None
    })

@app.get("/api/auth/steam/login")
async def api_auth_steam_login(request: Request):
    """Redirect to Steam OpenID login."""
    # Build return URL from the request origin
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost:8787"))
    base = f"{scheme}://{host}"
    callback = f"{base}/api/auth/steam/callback"
    redirect_url = build_openid_redirect(callback, base)
    return RedirectResponse(url=redirect_url)

@app.get("/api/auth/steam/callback")
async def api_auth_steam_callback(request: Request):
    """Handle Steam OpenID callback."""
    params = dict(request.query_params)
    steamid64 = validate_openid_response(params)
    if not steamid64:
        log.warning(f"Steam OpenID validation failed from {request.client.host}")
        return HTMLResponse(
            "<h2>Login failed</h2><p>Steam verification failed.</p>"
            "<p><a href=\"/login\">Try again</a></p>",
            status_code=403)
    # Check if this SteamID is a known account
    all_accounts = get_all_accounts()
    known_ids = {a["steamid64"] for a in all_accounts}
    if steamid64 not in known_ids:
        log.warning(f"Steam login rejected: {steamid64} not in accounts table")
        return HTMLResponse(
            "<h2>Access denied</h2><p>Your Steam account is not configured in PSS.</p>"
            "<p>Only accounts detected from this machine\'s Steam installation can log in.</p>"
            "<p><a href=\"/login\">Back</a></p>",
            status_code=403)
    # Create session and redirect to customizer
    token = create_session(steamid64)
    log.info(f"Steam login: {steamid64} from {request.client.host}")
    response = RedirectResponse(url="/customizer")
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_EXPIRY_DAYS * 86400,
                        httponly=True, samesite="lax")
    return response

@app.post("/api/auth/logout")
async def api_auth_logout():
    """Logout: clear session."""
    invalidate_sessions()
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


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



# === IMAGE CACHE API ===

@app.get("/api/image/{appid}/hero")
async def api_image_hero(appid: int):
    """Serve cached hero image, or redirect to Steam CDN if not cached."""
    local_path = CACHE_DIR / f"{appid}.jpg"
    if local_path.exists():
        return FileResponse(str(local_path), media_type="image/jpeg",
                           headers={"Cache-Control": "public, max-age=86400"})
    # Not cached — redirect to Steam CDN
    return RedirectResponse(
        url=f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_hero_2x.jpg",
        status_code=302)


@app.get("/api/image/{appid}/random")
async def api_image_random(appid: int):
    """Serve a random image for this game from all available sources.
    Downloads and caches as {appid}_{hash}.jpg if not already on disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Gather all available URLs
    urls = []

    # 1. Screenshots from enrichment
    from pss.database import get_db
    with get_db() as db:
        row = db.execute("SELECT screenshots FROM enrichment WHERE appid = ?", (appid,)).fetchone()
    if row and row["screenshots"]:
        try:
            thumbs = json.loads(row["screenshots"])
            for t in thumbs:
                full = re.sub(r'\.\d+x\d+\.', '.1920x1080.', t)
                urls.append(("screenshot", full))
        except Exception:
            pass

    # 2. SGDB heroes (from DB cache, not live API — avoid rate limits)
    cached = get_all_cached_heroes(appid)
    for c in cached:
        if c.get("source") == "sgdb" and c.get("url"):
            urls.append(("sgdb", c["url"]))

    # 3. Header fallback
    urls.append(("header", f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"))

    if not urls:
        # Fall back to selected hero
        return RedirectResponse(url=f"/api/image/{appid}/hero", status_code=302)

    # Pick random
    source, url = random.choice(urls)

    # Check if already cached on disk
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    local_path = CACHE_DIR / f"{appid}_{url_hash}.jpg"

    if local_path.exists():
        return FileResponse(str(local_path), media_type="image/jpeg",
                           headers={"Cache-Control": "public, max-age=3600"})

    # Download it
    if download_image(url, str(local_path)):
        return FileResponse(str(local_path), media_type="image/jpeg",
                           headers={"Cache-Control": "public, max-age=3600"})

    # Download failed — fall back to selected hero
    if local_path.exists():
        local_path.unlink()
    return RedirectResponse(url=f"/api/image/{appid}/hero", status_code=302)


@app.get("/api/image/{appid}/options")
async def api_image_options(appid: int):
    """Return all available image options for a game's screensaver image."""
    result = {"appid": appid, "current": None, "cached": [], "screenshots": [], "header": None}

    # Current selected + all cached
    cached = get_all_cached_heroes(appid)
    result["cached"] = cached
    current = next((c for c in cached if c.get("selected")), cached[0] if cached else None)
    result["current"] = current

    # Screenshot thumbnails from enrichment data
    from pss.database import get_db
    with get_db() as db:
        row = db.execute("SELECT screenshots FROM enrichment WHERE appid = ?", (appid,)).fetchone()
    if row and row["screenshots"]:
        try:
            thumbs = json.loads(row["screenshots"])
            result["screenshots"] = [{"thumb": t, "full": re.sub(r'\.\d+x\d+\.', '.1920x1080.', t)} for t in thumbs]
        except Exception:
            pass

    # SGDB alternatives (live API call, if key configured)
    sgdb_key = get_sgdb_key()
    if sgdb_key:
        heroes = fetch_sgdb_heroes(appid, sgdb_key)
        if heroes and heroes != "RATE_LIMITED":
            result["sgdb"] = [{"url": h.get("url",""), "thumb": h.get("thumb",""),
                               "width": h.get("width",0), "height": h.get("height",0),
                               "score": h.get("score",0), "style": h.get("style","")}
                              for h in heroes[:12]]
        elif heroes == "RATE_LIMITED":
            result["sgdb_error"] = "rate_limited"
    else:
        result["sgdb"] = []

    # Header fallback URL
    result["header"] = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"

    return JSONResponse(result)


@app.post("/api/image/{appid}/select")
async def api_image_select(appid: int, request: Request):
    """Select an image for a game. Downloads it and sets as active.
    Body: {source: 'sgdb'|'screenshot'|'header'|'reset', url: '...'} """
    body = await request.json()
    source = body.get("source", "")
    url = body.get("url", "")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local_path = str(CACHE_DIR / f"{appid}.jpg")

    if source == "reset":
        # Delete cached file + DB entries, re-run auto-select
        if Path(local_path).exists():
            Path(local_path).unlink()
        delete_cached_images(appid)
        result = cache_image_for_appid(appid)
        return JSONResponse({"ok": True, "result": result or "no_image"})

    if not url:
        return JSONResponse({"error": "url required"}, status_code=400)

    # Download the image
    if not download_image(url, local_path):
        return JSONResponse({"error": "Failed to download image"}, status_code=502)

    # Upsert into cache DB + set as selected
    upsert_image_cache(appid, source, url, local_path, selected=True)
    select_cached_image(appid, source, url)

    return JSONResponse({"ok": True, "source": source})


@app.post("/api/image/{appid}/upload")
async def api_image_upload(appid: int, file: UploadFile = File(...)):
    """Upload a custom image for a game."""
    # Validate it's an image
    if not file.content_type or not file.content_type.startswith("image/"):
        return JSONResponse({"error": "File must be an image"}, status_code=400)

    data = await file.read()
    if len(data) < 1000:
        return JSONResponse({"error": "File too small"}, status_code=400)
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse({"error": "File too large (max 10MB)"}, status_code=400)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local_path = str(CACHE_DIR / f"{appid}.jpg")

    with open(local_path, "wb") as f:
        f.write(data)

    # Record in DB as custom source
    custom_url = f"custom://{appid}"
    upsert_image_cache(appid, "custom", custom_url, local_path, selected=True)
    select_cached_image(appid, "custom", custom_url)

    return JSONResponse({"ok": True, "source": "custom"})
@app.post("/api/cache/start")
async def api_cache_start():
    global cache_thread
    if cache_state["running"]: return JSONResponse({"error": "Already running"}, status_code=409)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with cache_lock:
        cache_state.update(running=True, stop_requested=False, phase="starting",
                           message="Starting image cache...", errors=0, skipped=0,
                           sgdb_hits=0, screenshot_hits=0, header_hits=0)
    cache_thread = threading.Thread(target=image_cache_worker, daemon=True)
    cache_thread.start()
    return JSONResponse({"ok": True, "message": "Image caching started"})

@app.get("/api/cache/status")
async def api_cache_status():
    with cache_lock:
        state = dict(cache_state)
    acct = get_active_account()
    if acct:
        state["stats"] = get_image_cache_stats(acct["steamid64"])
    return JSONResponse(state)

@app.post("/api/cache/stop")
async def api_cache_stop():
    if not cache_state["running"]: return JSONResponse({"error": "Not running"}, status_code=409)
    with cache_lock:
        cache_state.update(stop_requested=True, message="Stopping...")
    return JSONResponse({"ok": True, "message": "Stop requested"})


@app.post("/api/repair-types")
async def api_repair_types():
    result = repair_types()
    if "error" in result:
        return JSONResponse(result, status_code=400 if result["error"] == "No active account" else 500)
    return JSONResponse(result)


def main():
    init_db(str(DB_PATH))
    config = get_config("global")
    port = config.get("server_port", 8787)
    if isinstance(port, str): port = int(port)
    log.info(f"PSS Server v0.2.0 on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=False)

if __name__ == "__main__":
    main()
