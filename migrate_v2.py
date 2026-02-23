#!/usr/bin/env python3
"""PSS Migration: v2 JSON -> SQLite"""

import json, sys, shutil
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from pss.database import (init_db, get_db, upsert_account, upsert_games,
    upsert_enrichment, set_exclusions, set_config, set_display_elements)

STEAM_ID = "76561197969687090"

def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  WARNING: Could not load {path}: {e}")
        return default

def migrate(source_dir, db_path):
    source = Path(source_dir)
    db = Path(db_path)
    print(f"PSS Migration v2 -> SQLite")
    print(f"  Source: {source}\n  Target: {db}\n  Account: {STEAM_ID}\n")

    paths = {
        "games": source / "data" / "games.json" if (source / "data").exists() else source / "games.json",
        "enriched": source / "data" / "games_enriched.json" if (source / "data").exists() else source / "games_enriched.json",
        "excluded": source / "data" / "excluded.json" if (source / "data").exists() else source / "excluded.json",
        "config": source / "config.json",
    }

    for name, p in paths.items():
        if not p.exists():
            print(f"  ERROR: {name} not found at {p}"); sys.exit(1)
        print(f"  Found: {p} ({p.stat().st_size:,} bytes)")

    if db.exists():
        bak = db.with_suffix(f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(db, bak); print(f"\n  Backed up existing DB to: {bak}")

    print(f"\n[1/5] Schema..."); init_db(db_path)
    print(f"[2/5] Account..."); upsert_account(STEAM_ID, persona_name="rayce185", is_active=True)

    print(f"[3/5] Games...")
    games = load_json(paths["games"], [])
    if not games: print("  ERROR: No games"); sys.exit(1)
    upsert_games(STEAM_ID, games); print(f"  {len(games)} games")

    print(f"[4/5] Enrichment...")
    enriched = load_json(paths["enriched"], {})
    for appid_str, data in enriched.items():
        upsert_enrichment(int(appid_str), data)
    print(f"  {len(enriched)} enrichment records")

    print(f"[5/5] Config + exclusions...")
    excl = load_json(paths["excluded"], {"excluded": []}).get("excluded", [])
    set_exclusions(STEAM_ID, excl); print(f"  {len(excl)} exclusions")
    config = load_json(paths["config"], {})
    de = config.pop("display_elements", [])
    set_config({k: v for k, v in config.items()}, scope="global")
    if de: set_display_elements(STEAM_ID, de); print(f"  {len(de)} display elements")

    print(f"\n{'='*50}\nVERIFICATION\n{'='*50}")
    with get_db() as conn:
        counts = {t: conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()["c"]
                  for t in ["accounts","games","enrichment","exclusions","config","display_elements"]}
    for t, c in counts.items(): print(f"  {t}: {c}")

    ok = (counts["games"] == len(games) and counts["enrichment"] == len(enriched)
          and counts["exclusions"] == len(excl))
    print(f"\n  {'All counts match!' if ok else 'MISMATCH DETECTED'}")
    print(f"  Database: {db} ({db.stat().st_size:,} bytes)")
    if not ok: sys.exit(1)

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--db", required=True)
    migrate(p.parse_args().source, p.parse_args().db)
