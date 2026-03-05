#!/usr/bin/env python3
"""
PSS System Tray Application
Cross-platform tray icon for managing the PSS server.

Two modes:
  Bundled (.exe): Runs uvicorn in-thread. No Python install needed.
  Source:         Runs server as subprocess. Requires Python + deps.
"""

import sys
import os
import subprocess
import threading
import time
import webbrowser
import signal
import platform
from pathlib import Path

# ── Path Resolution ──
# PyInstaller sets sys._MEIPASS to the temp extraction directory.
# Bundled assets (web/, pss/) live there. User data lives next to the .exe.
IS_BUNDLED = getattr(sys, "frozen", False)

if IS_BUNDLED:
    BUNDLE_DIR = Path(sys._MEIPASS)          # Read-only: code, web assets
    APP_DIR = Path(sys.executable).parent     # Writable: next to .exe
else:
    BUNDLE_DIR = Path(__file__).parent.resolve()
    APP_DIR = BUNDLE_DIR

os.chdir(APP_DIR)

# Ensure data + logs directories exist
(APP_DIR / "data").mkdir(exist_ok=True)
(APP_DIR / "logs").mkdir(exist_ok=True)

# ── Logging ──
# In bundled mode (console=False), sys.stdout and sys.stderr are None.
# This crashes any logging StreamHandler AND uvicorn's isatty() check.
# Fix: redirect both to a real file BEFORE anything else touches them.
import logging
import traceback
from datetime import datetime

_log_ts = datetime.now().strftime("%y%m%d_%H%M%S")
_tray_log_path = APP_DIR / "logs" / f"pss_tray_{_log_ts}.log"

if IS_BUNDLED:
    _stderr_file = open(APP_DIR / "logs" / f"pss_stderr_{_log_ts}.log", "w")
    if sys.stdout is None:
        sys.stdout = _stderr_file
    if sys.stderr is None:
        sys.stderr = _stderr_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(_tray_log_path)],
)
tray_log = logging.getLogger("pss.tray")

tray_log.info(f"PSS Tray starting (bundled={IS_BUNDLED})")
tray_log.info(f"APP_DIR={APP_DIR}")
if IS_BUNDLED:
    tray_log.info(f"BUNDLE_DIR={BUNDLE_DIR}")

# Load .env if present (optional — setup.html handles key entry for exe users)
env_file = APP_DIR / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# Set DATA_DIR + LOG_DIR env vars so server.py finds the right paths
os.environ["PSS_DATA_DIR"] = str(APP_DIR / "data")
os.environ["PSS_LOG_DIR"] = str(APP_DIR / "logs")
if IS_BUNDLED:
    os.environ["PSS_WEB_DIR"] = str(BUNDLE_DIR / "web")
    # Ensure bundled pss package is importable
    if str(BUNDLE_DIR) not in sys.path:
        sys.path.insert(0, str(BUNDLE_DIR))

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError as e:
    tray_log.error(f"Missing dependency: {e}")
    tray_log.error("Run: pip install pystray Pillow")
    sys.exit(1)

# pywebview: native screensaver window
try:
    import webview
except ImportError as e:
    tray_log.error(f"Missing dependency: {e}")
    tray_log.error("Run: pip install pywebview")
    sys.exit(1)



# ── Native Screensaver Window ──

_screensaver_active = False

class ScreensaverApi:
    """JS API exposed to screensaver.html via pywebview."""
    def dismiss(self):
        global _screensaver_active
        _screensaver_active = False
        try:
            for w in webview.windows:
                w.destroy()
        except Exception:
            pass

def launch_screensaver_native(port):
    """Open screensaver in a native fullscreen window. Blocks until dismissed."""
    global _screensaver_active
    if _screensaver_active:
        return
    _screensaver_active = True
    try:
        api = ScreensaverApi()
        window = webview.create_window(
            "PSS Screensaver",
            f"http://localhost:{port}/screensaver",
            fullscreen=True,
            frameless=True,
            on_top=True,
            js_api=api,
        )
        webview.start()  # blocks until window is destroyed
    except Exception as e:
        tray_log.error(f"pywebview screensaver failed: {e}")
    finally:
        _screensaver_active = False


# ── Icon Generation ──

def create_icon(color="#4CAF50", bg="#1a1a2e"):
    """Generate a 64x64 tray icon programmatically."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, size - 3, size - 3], radius=12, fill=bg)
    # Stylized monitor shape
    draw.rounded_rectangle([12, 16, 52, 42], radius=4, fill=color)
    draw.rectangle([26, 42, 38, 48], fill=color)
    draw.rectangle([20, 48, 44, 52], fill=color)
    return img

ICON_RUNNING  = create_icon("#4CAF50", "#1a1a2e")
ICON_STOPPED  = create_icon("#666666", "#1a1a2e")
ICON_STARTING = create_icon("#FFC107", "#1a1a2e")
ICON_ERROR    = create_icon("#f44336", "#1a1a2e")

PORT = 8787


# ── Server Process Management ──

class PSSServer:
    """Manages the PSS server — in-thread when bundled, subprocess when source."""

    def __init__(self):
        self._lock = threading.Lock()
        self._process = None      # subprocess mode
        self._thread = None       # in-thread mode
        self._uvicorn_server = None
        self._running = False

    @property
    def running(self):
        with self._lock:
            if IS_BUNDLED:
                return self._running and self._thread is not None and self._thread.is_alive()
            else:
                return self._process is not None and self._process.poll() is None

    def start(self):
        if self.running:
            return False

        if IS_BUNDLED:
            return self._start_inthread()
        else:
            return self._start_subprocess()

    def _start_inthread(self):
        """Run uvicorn in a background thread (bundled exe mode)."""
        with self._lock:
            self._running = True

        def _run():
            try:
                tray_log.info("Importing server modules...")
                import uvicorn
                tray_log.info("uvicorn imported OK")
                from pss.database import init_db
                tray_log.info("pss.database imported OK")
                from pss.server import app, DB_PATH
                tray_log.info(f"pss.server imported OK, DB_PATH={DB_PATH}")

                tray_log.info("Initializing database...")
                init_db(str(DB_PATH))
                tray_log.info("Database initialized")

                tray_log.info(f"Starting uvicorn on port {PORT}...")
                config = uvicorn.Config(
                    app, host="0.0.0.0", port=PORT,
                    log_level="info", access_log=False,
                    log_config=None,
                )
                self._uvicorn_server = uvicorn.Server(config)
                self._uvicorn_server.run()
            except Exception as e:
                tray_log.error(f"Server failed to start: {e}")
                tray_log.error(traceback.format_exc())
            finally:
                with self._lock:
                    self._running = False

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return True

    def _start_subprocess(self):
        """Run server as subprocess (source/development mode)."""
        with self._lock:
            python = sys.executable
            env = os.environ.copy()

            if platform.system() == "Windows":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
                self._process = subprocess.Popen(
                    [python, "-m", "pss.server"],
                    cwd=str(APP_DIR), env=env,
                    startupinfo=startupinfo,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                self._process = subprocess.Popen(
                    [python, "-m", "pss.server"],
                    cwd=str(APP_DIR), env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            return True

    def stop(self):
        if IS_BUNDLED:
            self._stop_inthread()
        else:
            self._stop_subprocess()

    def _stop_inthread(self):
        with self._lock:
            if self._uvicorn_server:
                self._uvicorn_server.should_exit = True
            self._running = False
        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._thread = None
        self._uvicorn_server = None

    def _stop_subprocess(self):
        with self._lock:
            if not self._process:
                return
            try:
                if platform.system() == "Windows":
                    self._process.terminate()
                else:
                    self._process.send_signal(signal.SIGTERM)
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            except Exception:
                pass
            self._process = None

    def restart(self):
        self.stop()
        time.sleep(1)
        self.start()


# ── Autostart Management ──

def get_autostart_enabled():
    system = platform.system()
    if system == "Windows":
        startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return (startup_dir / "PSS Tray.lnk").exists()
    elif system == "Linux":
        return (Path.home() / ".config" / "autostart" / "pss-tray.desktop").exists()
    return False


def set_autostart(enabled):
    system = platform.system()

    if system == "Windows":
        startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        lnk_path = startup_dir / "PSS Tray.lnk"

        if enabled:
            target = sys.executable if IS_BUNDLED else str(APP_DIR / "pss_tray.pyw")
            args = "" if IS_BUNDLED else f'"{target}"'
            exe = sys.executable if IS_BUNDLED else sys.executable

            ps_cmd = (
                f'$ws = New-Object -ComObject WScript.Shell; '
                f'$sc = $ws.CreateShortcut("{lnk_path}"); '
                f'$sc.TargetPath = "{exe}"; '
            )
            if not IS_BUNDLED:
                ps_cmd += f'$sc.Arguments = """{target}"""; '
            ps_cmd += (
                f'$sc.WorkingDirectory = "{APP_DIR}"; '
                f'$sc.Description = "PSS System Tray"; '
                f'$sc.Save()'
            )
            try:
                subprocess.run(["powershell", "-Command", ps_cmd],
                             capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception:
                pass
        else:
            for name in ("PSS.lnk", "PSS Tray.lnk"):
                p = startup_dir / name
                if p.exists():
                    p.unlink()

    elif system == "Linux":
        autostart_dir = Path.home() / ".config" / "autostart"
        desktop_file = autostart_dir / "pss-tray.desktop"

        if enabled:
            autostart_dir.mkdir(parents=True, exist_ok=True)
            exe = sys.executable
            desktop_file.write_text(
                f"[Desktop Entry]\n"
                f"Type=Application\n"
                f"Name=PSS Tray\n"
                f"Comment=Plexified Steam Screensaver\n"
                f"Exec={exe}" + ("" if IS_BUNDLED else f" {APP_DIR / 'pss_tray.pyw'}") + "\n"
                f"Path={APP_DIR}\n"
                f"Terminal=false\n"
                f"X-GNOME-Autostart-enabled=true\n"
                f"StartupNotify=false\n"
            )
        else:
            if desktop_file.exists():
                desktop_file.unlink()


# ── Update Checker ──

def check_for_updates():
    """Check GitHub releases for a newer version. Returns (has_update, latest_tag, download_url) or None."""
    try:
        import urllib.request
        import json
        url = "https://api.github.com/repos/Rayce185/PlexifiedSteamScreensaver/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "PSS"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        latest_tag = data.get("tag_name", "")
        current = ""
        version_file = APP_DIR / "VERSION"
        if version_file.exists():
            current = version_file.read_text().strip()

        if latest_tag and latest_tag.lstrip("v") != current.lstrip("v"):
            # Find the right asset
            dl_url = data.get("html_url", "")
            for asset in data.get("assets", []):
                name = asset.get("name", "").lower()
                if platform.system() == "Windows" and name.endswith(".exe"):
                    dl_url = asset["browser_download_url"]
                    break
                elif platform.system() == "Linux" and ("linux" in name or name.endswith(".AppImage")):
                    dl_url = asset["browser_download_url"]
                    break
            return (True, latest_tag, dl_url)
        return (False, latest_tag, "")
    except Exception:
        return None


# ── Tray Application ──

class PSSTray:
    def __init__(self):
        self.server = PSSServer()
        self.icon = None
        self._autostart = get_autostart_enabled()
        self._update_info = None  # (has_update, tag, url)

    def _update_icon(self):
        if not self.icon:
            return
        if self.server.running:
            self.icon.icon = ICON_RUNNING
            self.icon.title = "PSS — Running"
        else:
            self.icon.icon = ICON_STOPPED
            self.icon.title = "PSS — Stopped"

    def _monitor_loop(self):
        """Background thread: update icon + periodic update check."""
        check_counter = 0
        while self.icon and self.icon.visible:
            self._update_icon()
            try:
                self.icon.update_menu()
            except Exception:
                pass

            # Check for updates every ~30 minutes
            check_counter += 1
            if check_counter >= 600:  # 600 * 3s = 30 min
                check_counter = 0
                self._update_info = check_for_updates()

            time.sleep(3)

    def on_open_customizer(self, icon, item):
        webbrowser.open(f"http://localhost:{PORT}/customizer")

    def on_open_screensaver(self, icon, item):
        threading.Thread(target=launch_screensaver_native, args=(PORT,), daemon=True).start()

    def on_start(self, icon, item):
        if self.server.running:
            return
        self.icon.icon = ICON_STARTING
        self.icon.title = "PSS — Starting..."
        threading.Thread(target=self._do_start, daemon=True).start()

    def _do_start(self):
        self.server.start()
        time.sleep(2)
        self._update_icon()

    def on_stop(self, icon, item):
        if not self.server.running:
            return
        threading.Thread(target=self._do_stop, daemon=True).start()

    def _do_stop(self):
        self.server.stop()
        time.sleep(1)
        self._update_icon()

    def on_restart(self, icon, item):
        self.icon.icon = ICON_STARTING
        self.icon.title = "PSS — Restarting..."
        threading.Thread(target=self._do_restart, daemon=True).start()

    def _do_restart(self):
        self.server.restart()
        time.sleep(2)
        self._update_icon()

    def on_autostart(self, icon, item):
        self._autostart = not self._autostart
        set_autostart(self._autostart)

    def on_check_updates(self, icon, item):
        threading.Thread(target=self._do_check_updates, daemon=True).start()

    def _do_check_updates(self):
        self._update_info = check_for_updates()
        if self._update_info is None:
            return
        has_update, tag, url = self._update_info
        if has_update and url:
            webbrowser.open(url)
        elif has_update:
            webbrowser.open("https://github.com/Rayce185/PlexifiedSteamScreensaver/releases/latest")

    def on_quit(self, icon, item):
        self.server.stop()
        icon.stop()

    def _is_running(self, item):
        return self.server.running

    def _is_stopped(self, item):
        return not self.server.running

    def _autostart_checked(self, item):
        return self._autostart

    def _update_label(self, item):
        if self._update_info and self._update_info[0]:
            return f"Update Available ({self._update_info[1]})"
        return "Check for Updates"

    def build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("Open Customizer", self.on_open_customizer, default=True),
            pystray.MenuItem("Open Screensaver", self.on_open_screensaver),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start Server", self.on_start, visible=self._is_stopped),
            pystray.MenuItem("Stop Server", self.on_stop, visible=self._is_running),
            pystray.MenuItem("Restart Server", self.on_restart, visible=self._is_running),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start with OS", self.on_autostart, checked=self._autostart_checked),
            pystray.MenuItem(self._update_label, self.on_check_updates),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.on_quit),
        )

    def run(self, autostart_server=True, open_browser=True):
        self.icon = pystray.Icon(
            name="PSS",
            icon=ICON_STOPPED,
            title="PSS — Stopped",
            menu=self.build_menu(),
        )

        if autostart_server:
            self.icon.icon = ICON_STARTING
            self.icon.title = "PSS — Starting..."
            threading.Thread(target=self._do_start, daemon=True).start()

            # Open browser after server starts (skipped with --no-browser)
            if open_browser:
                def _open_browser():
                    time.sleep(4)
                    webbrowser.open(f"http://localhost:{PORT}/customizer")
                threading.Thread(target=_open_browser, daemon=True).start()

        # Initial update check
        threading.Thread(target=lambda: setattr(self, '_update_info', check_for_updates()), daemon=True).start()

        # Monitor thread
        threading.Thread(target=self._monitor_loop, daemon=True).start()

        self.icon.run()


def main():
    no_server = "--no-server" in sys.argv
    no_browser = "--no-browser" in sys.argv
    tray = PSSTray()
    tray.run(autostart_server=not no_server, open_browser=not no_browser)


if __name__ == "__main__":
    main()
