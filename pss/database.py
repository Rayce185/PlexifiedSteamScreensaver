"""PSS Database Module -- SQLite schema, connection management, query helpers."""

import sqlite3, json, os
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

DB_PATH = None
SCHEMA_VERSION = 3

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
    enriched_at TEXT, permanently_unenrichable INTEGER DEFAULT 0,
    steamspy_owners TEXT, steamspy_avg_playtime INTEGER,
    steamspy_positive INTEGER, steamspy_negative INTEGER,
    steamspy_enriched_at TEXT
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
CREATE TABLE IF NOT EXISTS presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL, name TEXT NOT NULL,
    filters TEXT NOT NULL DEFAULT '{}', sort_field TEXT DEFAULT 'name',
    sort_dir TEXT DEFAULT 'asc', pinned_filters TEXT DEFAULT '[]',
    is_builtin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(steamid64),
    UNIQUE(account_id, name)
);
CREATE INDEX IF NOT EXISTS idx_games_account ON games(account_id);
CREATE INDEX IF NOT EXISTS idx_games_appid ON games(appid);
CREATE INDEX IF NOT EXISTS idx_exclusions_account ON exclusions(account_id);
CREATE INDEX IF NOT EXISTS idx_enrichment_type ON enrichment(type);
CREATE INDEX IF NOT EXISTS idx_config_scope ON config(scope);
CREATE INDEX IF NOT EXISTS idx_presets_account ON presets(account_id);
"""

MUTABLE_CONFIG_KEYS = {
    "mute_after_seconds", "screensaver_after_seconds", "sleep_after_seconds",
    "screensaver_slide_duration_seconds", "screensaver_transition_seconds",
    "screensaver_title_delay_seconds", "ken_burns_intensity", "log_level",
    "screensaver_types",
    "shovelware_min_signals", "shovelware_avg_playtime_threshold",
    "shovelware_reviews_threshold", "shovelware_review_ratio_threshold",
    "shovelware_owners_threshold", "shovelware_require_unplayed",
    "shovelware_enable_avg_playtime", "shovelware_enable_reviews",
    "shovelware_enable_ratio", "shovelware_enable_owners",
    "shovelware_enable_user_playtime", "shovelware_enable_metacritic",
    "auto_enrich_threshold", "auto_refresh_on_startup"
}

BUILTIN_PRESETS = [
    {"name": "All Games", "filters": {"type": ["game"], "included": "included"}, "sort_field": "name", "sort_dir": "asc"},
    {"name": "Full Library", "filters": {}, "sort_field": "name", "sort_dir": "asc"},
    {"name": "Unplayed Backlog", "filters": {"type": ["game"], "included": "included", "played": "never"}, "sort_field": "name", "sort_dir": "asc"},
    {"name": "Top Rated", "filters": {"type": ["game"], "enriched": "yes", "metacritic_min": 70}, "sort_field": "metacritic_score", "sort_dir": "desc"},
    {"name": "Recently Played", "filters": {"type": ["game"], "played": "played"}, "sort_field": "last_played_ts", "sort_dir": "desc"},
]

def init_db(db_path):
    global DB_PATH
    DB_PATH = str(db_path)
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA_SQL)
        # Check schema version for migrations
        row = db.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        old_version = int(row["value"]) if row else 0
        if old_version < 2:
            _migrate_to_v2(db)
        if old_version < 3:
            _migrate_to_v3(db)
        db.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                    ("schema_version", str(SCHEMA_VERSION)))

def _migrate_to_v2(db):
    """Migration from schema v1 to v2: add presets table (handled by CREATE IF NOT EXISTS),
    seed builtin presets for existing accounts, seed screensaver_types config."""
    # Seed screensaver_types default if not exists
    existing = db.execute("SELECT 1 FROM config WHERE scope='global' AND key='screensaver_types'").fetchone()
    if not existing:
        db.execute("INSERT INTO config (scope, key, value) VALUES ('global', 'screensaver_types', ?)",
                   (json.dumps(["game"]),))
    # Seed builtin presets for all accounts
    accounts = db.execute("SELECT steamid64 FROM accounts").fetchall()
    for acct in accounts:
        _seed_builtin_presets(db, acct["steamid64"])

SHOVELWARE_DEFAULTS = {
    "shovelware_min_signals": 3,
    "shovelware_avg_playtime_threshold": 30,
    "shovelware_reviews_threshold": 100,
    "shovelware_review_ratio_threshold": 60,
    "shovelware_owners_threshold": 50000,
    "shovelware_require_unplayed": True,
    "shovelware_enable_avg_playtime": True,
    "shovelware_enable_reviews": True,
    "shovelware_enable_ratio": True,
    "shovelware_enable_owners": True,
    "shovelware_enable_user_playtime": True,
    "shovelware_enable_metacritic": True,
}

def _migrate_to_v3(db):
    """Migration v2 to v3: add SteamSpy columns to enrichment, seed shovelware config."""
    # Add columns if they don't exist
    existing = [r[1] for r in db.execute("PRAGMA table_info(enrichment)").fetchall()]
    for col, coltype, default in [
        ("steamspy_owners", "TEXT", None),
        ("steamspy_avg_playtime", "INTEGER", None),
        ("steamspy_positive", "INTEGER", None),
        ("steamspy_negative", "INTEGER", None),
        ("steamspy_enriched_at", "TEXT", None),
    ]:
        if col not in existing:
            db.execute(f"ALTER TABLE enrichment ADD COLUMN {col} {coltype}")
    # Seed shovelware config defaults
    for key, value in SHOVELWARE_DEFAULTS.items():
        existing_cfg = db.execute("SELECT 1 FROM config WHERE scope='global' AND key=?", (key,)).fetchone()
        if not existing_cfg:
            db.execute("INSERT INTO config (scope, key, value) VALUES ('global', ?, ?)",
                       (key, json.dumps(value)))

def _seed_builtin_presets(db, account_id):
    for bp in BUILTIN_PRESETS:
        existing = db.execute("SELECT 1 FROM presets WHERE account_id=? AND name=?",
                              (account_id, bp["name"])).fetchone()
        if not existing:
            db.execute("""INSERT INTO presets (account_id, name, filters, sort_field, sort_dir, is_builtin)
                          VALUES (?, ?, ?, ?, ?, 1)""",
                       (account_id, bp["name"], json.dumps(bp["filters"]),
                        bp["sort_field"], bp["sort_dir"]))

DEFAULT_DISPLAY_ELEMENTS = [
    {"id": "installed_badge", "enabled": True, "order": 0},
    {"id": "playtime", "enabled": True, "order": 1},
    {"id": "device_breakdown", "enabled": True, "order": 2},
    {"id": "last_played", "enabled": True, "order": 3},
    {"id": "genres", "enabled": True, "order": 4},
    {"id": "developer", "enabled": True, "order": 5},
    {"id": "metacritic", "enabled": True, "order": 6},
    {"id": "release_date", "enabled": True, "order": 7},
    {"id": "description", "enabled": True, "order": 8},
    {"id": "controller_support", "enabled": True, "order": 9},
    {"id": "vr_support", "enabled": True, "order": 10},
    {"id": "platforms", "enabled": True, "order": 11},
]

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
        # Seed builtin presets for new accounts
        _seed_builtin_presets(db, steamid64)

def get_games(account_id):
    with get_db() as db:
        rows = db.execute("""
            SELECT g.*, e.type, e.genres, e.categories, e.developer, e.publisher,
                   e.release_date, e.coming_soon, e.metacritic_score, e.short_description,
                   e.controller_support, e.vr_support, e.platforms AS native_platforms,
                   e.screenshots, e.is_free, e.enriched_at,
                   e.steamspy_owners, e.steamspy_avg_playtime,
                   e.steamspy_positive, e.steamspy_negative,
                   CASE WHEN e.appid IS NOT NULL THEN 1 ELSE 0 END AS enriched,
                   CASE WHEN x.appid IS NOT NULL THEN 1 ELSE 0 END AS excluded
            FROM games g
            LEFT JOIN enrichment e ON g.appid = e.appid
            LEFT JOIN exclusions x ON g.account_id = x.account_id AND g.appid = x.appid
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
        g["excluded"] = bool(g["excluded"])
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
        # Default type for unenriched games
        if not g.get("type"):
            g["type"] = "game"
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

def upsert_steamspy(appid, data):
    with get_db() as db:
        db.execute("""
            UPDATE enrichment SET
                steamspy_owners = ?, steamspy_avg_playtime = ?,
                steamspy_positive = ?, steamspy_negative = ?,
                steamspy_enriched_at = ?
            WHERE appid = ?
        """, (
            data.get("owners", ""), data.get("average_forever", 0),
            data.get("positive", 0), data.get("negative", 0),
            data.get("enriched_at"), appid
        ))

def get_steamspy_unenriched_appids(account_id):
    """Get appids that have store enrichment but no SteamSpy data."""
    with get_db() as db:
        rows = db.execute("""
            SELECT g.appid, g.name FROM games g
            INNER JOIN enrichment e ON g.appid = e.appid
            WHERE g.account_id = ? AND e.steamspy_enriched_at IS NULL
            ORDER BY g.name COLLATE NOCASE
        """, (account_id,)).fetchall()
        return [(r["appid"], r["name"]) for r in rows]

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

def toggle_exclusion(account_id, appid, exclude):
    """Toggle a single game's exclusion status. Returns new state."""
    with get_db() as db:
        if exclude:
            db.execute("INSERT OR IGNORE INTO exclusions (account_id, appid) VALUES (?, ?)",
                       (account_id, appid))
        else:
            db.execute("DELETE FROM exclusions WHERE account_id = ? AND appid = ?",
                       (account_id, appid))
    return exclude

def bulk_set_exclusions(account_id, appids, exclude):
    """Bulk include/exclude a list of appids. Returns count affected."""
    with get_db() as db:
        if exclude:
            for appid in appids:
                db.execute("INSERT OR IGNORE INTO exclusions (account_id, appid) VALUES (?, ?)",
                           (account_id, appid))
        else:
            placeholders = ",".join("?" * len(appids))
            db.execute(f"DELETE FROM exclusions WHERE account_id = ? AND appid IN ({placeholders})",
                       [account_id] + list(appids))
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
    # Ensure screensaver_types has a default
    if "screensaver_types" not in config:
        config["screensaver_types"] = ["game"]
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
        elements = [{"id": r["id"], "enabled": bool(r["enabled"]), "order": r["order"]} for r in rows]
        if not elements:
            # Seed defaults on first access
            set_display_elements(account_id, DEFAULT_DISPLAY_ELEMENTS)
            return list(DEFAULT_DISPLAY_ELEMENTS)
        return elements

def set_display_elements(account_id, elements):
    with get_db() as db:
        db.execute("DELETE FROM display_elements WHERE account_id = ?", (account_id,))
        for el in elements:
            db.execute("INSERT INTO display_elements (account_id, element_id, enabled, sort_order) VALUES (?,?,?,?)",
                       (account_id, el["id"], int(el.get("enabled", True)), el.get("order", 0)))

# === PRESETS ===

def get_presets(account_id):
    with get_db() as db:
        rows = db.execute("""
            SELECT id, name, filters, sort_field, sort_dir, pinned_filters, is_builtin
            FROM presets WHERE account_id = ? ORDER BY is_builtin DESC, name COLLATE NOCASE
        """, (account_id,)).fetchall()
    result = []
    for r in rows:
        p = dict(r)
        try: p["filters"] = json.loads(p["filters"])
        except: p["filters"] = {}
        try: p["pinned_filters"] = json.loads(p["pinned_filters"])
        except: p["pinned_filters"] = []
        p["is_builtin"] = bool(p["is_builtin"])
        result.append(p)
    return result

def save_preset(account_id, name, filters, sort_field="name", sort_dir="asc", pinned_filters=None):
    with get_db() as db:
        db.execute("""
            INSERT INTO presets (account_id, name, filters, sort_field, sort_dir, pinned_filters)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, name) DO UPDATE SET
                filters=excluded.filters, sort_field=excluded.sort_field,
                sort_dir=excluded.sort_dir, pinned_filters=excluded.pinned_filters,
                updated_at=datetime('now')
        """, (account_id, name, json.dumps(filters), sort_field, sort_dir,
              json.dumps(pinned_filters or [])))
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]

def delete_preset(account_id, preset_id):
    with get_db() as db:
        # Don't delete builtins
        db.execute("DELETE FROM presets WHERE id = ? AND account_id = ? AND is_builtin = 0",
                   (preset_id, account_id))

def get_distinct_values(account_id):
    """Get all distinct filterable values from the library for populating filter dropdowns."""
    with get_db() as db:
        # Types
        types = db.execute("""
            SELECT DISTINCT COALESCE(e.type, 'game') as type FROM games g
            LEFT JOIN enrichment e ON g.appid = e.appid WHERE g.account_id = ?
        """, (account_id,)).fetchall()
        # Genres (stored as JSON arrays)
        genre_rows = db.execute("""
            SELECT e.genres FROM enrichment e
            INNER JOIN games g ON g.appid = e.appid
            WHERE g.account_id = ? AND e.genres IS NOT NULL AND e.genres != '[]'
        """, (account_id,)).fetchall()
        genres = set()
        for r in genre_rows:
            try:
                for g in json.loads(r["genres"]):
                    genres.add(g)
            except: pass
        # Developers
        devs = db.execute("""
            SELECT DISTINCT e.developer FROM enrichment e
            INNER JOIN games g ON g.appid = e.appid
            WHERE g.account_id = ? AND e.developer IS NOT NULL AND e.developer != ''
        """, (account_id,)).fetchall()
        # Controller support values
        controllers = db.execute("""
            SELECT DISTINCT e.controller_support FROM enrichment e
            INNER JOIN games g ON g.appid = e.appid WHERE g.account_id = ?
        """, (account_id,)).fetchall()
    return {
        "types": sorted(set(r["type"] for r in types)),
        "genres": sorted(genres),
        "developers": sorted(set(r["developer"] for r in devs)),
        "controller_support": sorted(set(r["controller_support"] for r in controllers)),
    }
