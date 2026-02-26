# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for PSS — Plexified Steam Screensaver

Builds a single-directory distribution with pss_tray.pyw as entry point.
The exe bundles Python, all dependencies, web assets, and server code.

Build: pyinstaller pss.spec
Output: dist/PSS/PSS.exe
"""

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['pss_tray.pyw'],
    pathex=[],
    binaries=[],
    datas=[
        ('web', 'web'),              # HTML/CSS/JS
        ('VERSION', '.'),            # Commit hash
        ('.env.example', '.'),       # Reference config
    ],
    hiddenimports=[
        # uvicorn internals
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # pystray backends
        'pystray._win32' if sys.platform == 'win32' else 'pystray._xorg',
        # PSS modules
        'pss',
        'pss.server',
        'pss.database',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'xmlrpc', 'pydoc',
        'doctest', 'argparse', 'difflib', 'pdb',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PSS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,         # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,             # TODO: add PSS icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PSS',
)
