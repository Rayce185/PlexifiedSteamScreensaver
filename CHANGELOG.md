# Changelog

All notable changes to PSS are documented here.

## v1.0.0 — 2026-02-27

### Initial Public Release

**Screensaver**
- Cinematic Ken Burns slideshow with 8 animation variants
- Native fullscreen window via pywebview (no external browser)
- Dismisses on any input (mouse, keyboard, click) — real screensaver behavior
- Multi-row badge display with 14+ data elements
- WYSIWYG layout editor for game info and clock positioning
- Image shuffle mode with SteamGridDB, screenshots, and headers
- Shovelware filtering in screensaver (server-side detection)

**Customizer UI**
- Three-tab interface: Games, Display, Settings
- Opens in your default browser (separate from screensaver)
- Real-time search, filtering, sorting across 10+ dimensions
- Bulk include/exclude with undo snapshots
- Per-game type correction and Type Management
- Shovelware detection with 6 configurable signals
- Filter presets with save/load

**Library Management**
- Multi-account support with auto-detection
- Per-account Steam API keys
- Steam API v1 + local manifest scanning

**Enrichment Pipeline**
- Steam Store: genres, developer, Metacritic, controller/VR, descriptions
- SteamSpy: owner counts, global playtime, review stats
- Steam Deck + ProtonDB: compatibility tier, confidence scores
- SteamGridDB: hero image alternatives with 16:9 filtering
- Type correction from Steam's authoritative catalog

**System**
- System tray app with server management
- Steam OpenID authentication
- WebSocket live progress for all workers
- Auto-update checker (GitHub releases)
- Windows exe + Linux binary + source distribution
- GitHub Actions CI/CD for automated builds
