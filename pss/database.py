"""PSS Database Module -- SQLite schema, connection management, query helpers."""

import sqlite3, json, os
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

DB_PATH = None
SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
    steamid64 TEXT PRIMARY KEY, persona_name TEXT, avatar_url TEXT,
    is_active INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS games (
    account_id TEXT NOT NULL, appid INTEGER NOT NULL, name TEXT NOT NULL,
    playtime_hours REAL DEFAULT 0, playtime_windows_hours REAL DEFAULT 0,
    playtime_linux_hours REAL DEFAULT 0, playtime_deck_hours REAL DEFAULT 0,
    playtime_mac_hours REAL DEFAULT 0, last_played TEXT, last_played_ts INTEGER DEFAULT 0,
    primary_device TEXT, installed INTEGER DEFAULT 0, ever_played INTEGER DEFAULT 0,
    nsfw_auto INTEGER DEFAULT 0, hero_2x TEXT, hero_1x TEXT, header TEXT, logo TEXT,
    store_url TEXT, created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, appid),
    FOREIGN KEY (account_id) REFERENCES accounts(steamid64)
);
CREATE TABLE IF NOT EXISTS enrichment (
    appid INTEGER PRIMARY KEY, type TEXT DEFAULT 'game', genres TEXT, categories TEXT,
    developer TEXT, publisher TEXT, release_date TEXT, coming_soon INTEGER DEFAULT 0,
    metacritic_score INTEGER, short_description TEXT, controller_support TEXT DEFAULT 'none',
    vr_support INTEGER DEFAULT 0, platforms TEXT, screenshots TEXT, is_free INTEGER DEFAULT 0,
    enriched_at TEXT, permanently_unenrichable INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS exclusions (
    account_id TEXT NOT NULL, appid INTEGER NOT NULL, reason TEXT DEFAULT 'manual',
    excluded_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, appid),
    FOREIGN KEY (account_id) REFERENCES accounts(steamid64)
);
CREATE TABLE IF NOT EXISTS config (
    scope TEXT NOT NULL DEFAULT 'global', key TEXT NOT NULL, value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')), PRIMARY KEY (scope, key)
);
CREATE TABLE IF NOT EXISTS display_elements (
    account_id TEXT NOT NULL, element_id TEXT NOT NULL, enabled INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 0, style TEXT,
    PRIMARY KEY (account_id, element_id),
    FOREIGN KEY (account_id) REFERENCES accounts(steamid64)
);
CREATE INDEX IF NOT EXISTS idx_games_account ON games(account_id);
CREATE INDEX IF NOT EXISTS idx_games_appid ON games(appid);
CREATE INDEX IF NOT EXISTS idx_exclusions_account ON exclusions(account_id);
CREATE INDEX IF NOT EXISTS idx_enrichment_type ON enrichment(type);
CREATE INDEX IF NOT EXISTS idx_config_scope ON config(scope);
"""

MUTABLE_CONFIG_KEYS = {
    "mute_after_seconds", "screensaver_after_seconds", "sleep_after_seconds",
    "screensaver_slide_duration_seconds", "screensaver_transition_seconds",
    "screensaver_title_delay_seconds", "ken_burns_intensity", "log_level"
}

def init_db(db_path):
    global DB_PATH
    DB_PATH = str(db_path)
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA_SQL)
        db.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                    ("schema_version", str(SCHEMA_VERSION)))

@contextmanager
def get_db():
    if DB_PATH is None:
        raise RuntimeError("Database not initialized -- call init_db() first")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_active_account():
    with get_db() as db:
        row = db.execute("SELECT * FROM accounts WHERE is_active = 1 LIMIT 1").fetchone()
        return dict(row) if row else None

def upsert_account(steamid64, persona_name=None, is_active=False):
    with get_db() as db:
        db.execute("""
            INSERT INTO accounts (steamid64, persona_name, is_active) VALUES (?, ?, ?)
            ON CONFLICT(steamid64) DO UPDATE SET
                persona_name = COALESCE(excluded.persona_name, accounts.persona_name),
                is_active = excluded.is_active, updated_at = datetime('now')
        """, (steamid64, persona_name, int(is_active)))

def get_games(account_id):
    with get_db() as db:
        rows = db.execute("""
            SELECT g.*, e.type, e.genres, e.categories, e.developer, e.publisher,
                   e.release_date, e.coming_soon, e.metacritic_score, e.short_description,
                   e.controller_support, e.vr_support, e.platforms AS native_platforms,
                   e.screenshots, e.is_free, e.enriched_at,
                   CASE WHEN e.appid IS NOT NULL THEN 1 ELSE 0 END AS enriched
            FROM games g LEFT JOIN enrichment e ON g.appid = e.appid
            WHERE g.account_id = ?
            ORDER BY g.name COLLATE NOCASE
        """, (account_id,)).fetchall()
    result = []
    for row in rows:
        g = dict(row)
        g["installed_htpc"] = bool(g.pop("installed"))
        g["ever_played"] = bool(g["ever_played"])
        g["nsfw_auto"] = bool(g["nsfw_auto"])
        g["enriched"] = bool(g["enriched"])
        for col in ("genres", "categories", "screenshots"):
            if g.get(col):
                try: g[col] = json.loads(g[col])
                except: g[col] = []
            else:
                g[col] = [] if g["enriched"] else None
        if g.get("native_platforms"):
            try: g["native_platforms"] = json.loads(g["native_platforms"])
            except: g["native_platforms"] = None
        if g["enriched"]:
            g["vr_support"] = bool(g.get("vr_support"))
            g["coming_soon"] = bool(g.get("coming_soon"))
            g["is_free"] = bool(g.get("is_free"))
        for k in ("account_id", "created_at", "updated_at"):
            g.pop(k, None)
        result.append(g)
    return result

def upsert_games(account_id, games):
    with get_db() as db:
        for g in games:
            db.execute("""
                INSERT INTO games (account_id, appid, name, playtime_hours,
                    playtime_windows_hours, playtime_linux_hours, playtime_deck_hours,
                    playtime_mac_hours, last_played, last_played_ts, primary_device,
                    installed, ever_played, nsfw_auto, hero_2x, hero_1x, header, logo, store_url)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account_id, appid) DO UPDATE SET
                    name=excluded.name, playtime_hours=excluded.playtime_hours,
                    playtime_windows_hours=excluded.playtime_windows_hours,
                    playtime_linux_hours=excluded.playtime_linux_hours,
                    playtime_deck_hours=excluded.playtime_deck_hours,
                    playtime_mac_hours=excluded.playtime_mac_hours,
                    last_played=excluded.last_played, last_played_ts=excluded.last_played_ts,
                    primary_device=excluded.primary_device, installed=excluded.installed,
                    ever_played=excluded.ever_played, nsfw_auto=excluded.nsfw_auto,
                    hero_2x=excluded.hero_2x, hero_1x=excluded.hero_1x,
                    header=excluded.header, logo=excluded.logo, store_url=excluded.store_url,
                    updated_at=datetime('now')
            """, (
                account_id, g["appid"], g["name"],
                g.get("playtime_hours", 0), g.get("playtime_windows_hours", 0),
                g.get("playtime_linux_hours", 0), g.get("playtime_deck_hours", 0),
                g.get("playtime_mac_hours", 0), g.get("last_played"),
                g.get("last_played_ts", 0), g.get("primary_device"),
                int(g.get("installed_htpc", False)), int(g.get("ever_played", False)),
                int(g.get("nsfw_auto", False)),
                g.get("hero_2x"), g.get("hero_1x"), g.get("header"),
                g.get("logo"), g.get("store_url")
            ))
    return len(games)

def upsert_enrichment(appid, data):
    with get_db() as db:
        db.execute("""
            INSERT INTO enrichment (appid, type, genres, categories, developer, publisher,
                release_date, coming_soon, metacritic_score, short_description,
                controller_support, vr_support, platforms, screenshots, is_free, enriched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(appid) DO UPDATE SET
                type=excluded.type, genres=excluded.genres, categories=excluded.categories,
                developer=excluded.developer, publisher=excluded.publisher,
                release_date=excluded.release_date, coming_soon=excluded.coming_soon,
                metacritic_score=excluded.metacritic_score,
                short_description=excluded.short_description,
                controller_support=excluded.controller_support,
                vr_support=excluded.vr_support, platforms=excluded.platforms,
                screenshots=excluded.screenshots, is_free=excluded.is_free,
                enriched_at=excluded.enriched_at
        """, (
            appid, data.get("type", "game"),
            json.dumps(data.get("genres", [])), json.dumps(data.get("categories", [])),
            data.get("developer", ""), data.get("publisher", ""),
            data.get("release_date", ""), int(data.get("coming_soon", False)),
            data.get("metacritic_score"), data.get("short_description", ""),
            data.get("controller_support", "none"), int(data.get("vr_support", False)),
            json.dumps(data.get("native_platforms") or data.get("platforms", {})),
            json.dumps(data.get("screenshots", [])),
            int(data.get("is_free", False)), data.get("enriched_at")
        ))

def get_unenriched_appids(account_id):
    with get_db() as db:
        rows = db.execute("""
            SELECT g.appid, g.name FROM games g
            LEFT JOIN enrichment e ON g.appid = e.appid
            WHERE g.account_id = ? AND e.appid IS NULL
            ORDER BY g.name COLLATE NOCASE
        """, (account_id,)).fetchall()
        return [(r["appid"], r["name"]) for r in rows]

def get_enrichment_count():
    with get_db() as db:
        return db.execute("SELECT COUNT(*) as cnt FROM enrichment").fetchone()["cnt"]

def get_exclusions(account_id):
    with get_db() as db:
        rows = db.execute("SELECT appid FROM exclusions WHERE account_id = ?", (account_id,)).fetchall()
        return [r["appid"] for r in rows]

def set_exclusions(account_id, appids):
    with get_db() as db:
        db.execute("DELETE FROM exclusions WHERE account_id = ?", (account_id,))
        for appid in appids:
            db.execute("INSERT INTO exclusions (account_id, appid) VALUES (?, ?)", (account_id, appid))
    return len(appids)

def get_config(scope="global"):
    with get_db() as db:
        rows = db.execute("SELECT key, value FROM config WHERE scope = ?", (scope,)).fetchall()
    config = {}
    for r in rows:
        try: config[r["key"]] = json.loads(r["value"])
        except: config[r["key"]] = r["value"]
    return config

def get_full_config(account_id=None):
    config = get_config("global")
    acct = account_id or (get_active_account() or {}).get("steamid64")
    if acct:
        config["display_elements"] = get_display_elements(acct)
    return config

def set_config(updates, scope="global"):
    updated = []
    with get_db() as db:
        for key, value in updates.items():
            if key == "display_elements": continue
            db.execute("""
                INSERT INTO config (scope, key, value, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(scope, key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at
            """, (scope, key, json.dumps(value)))
            updated.append(key)
    return updated

def get_display_elements(account_id):
    with get_db() as db:
        rows = db.execute("""
            SELECT element_id as id, enabled, sort_order as "order"
            FROM display_elements WHERE account_id = ? ORDER BY sort_order
        """, (account_id,)).fetchall()
        return [{"id": r["id"], "enabled": bool(r["enabled"]), "order": r["order"]} for r in rows]

def set_display_elements(account_id, elements):
    with get_db() as db:
        db.execute("DELETE FROM display_elements WHERE account_id = ?", (account_id,))
        for el in elements:
            db.execute("INSERT INTO display_elements (account_id, element_id, enabled, sort_order) VALUES (?,?,?,?)",
                       (account_id, el["id"], int(el.get("enabled", True)), el.get("order", 0)))
