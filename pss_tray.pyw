#!/usr/bin/env python3
"""
PSS System Tray Application
Cross-platform tray icon for managing the PSS server.
Windows: .pyw extension = no console window
Linux: requires pystray with AppIndicator3 or X11 backend
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

# Add project root to path
SCRIPT_DIR = Path(__file__).parent.resolve()
os.chdir(SCRIPT_DIR)

# Load .env
env_file = SCRIPT_DIR / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Missing dependencies. Run: pip install pystray Pillow")
    sys.exit(1)


# ── Icon Generation ──

def create_icon(color="#4CAF50", bg="#1a1a2e"):
    """Generate a 64x64 tray icon. Green=running, gray=stopped, yellow=starting."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark rounded background
    draw.rounded_rectangle([2, 2, size - 3, size - 3], radius=12, fill=bg)

    # "PSS" text or a play triangle
    # Draw a stylized play/game controller shape
    cx, cy = size // 2, size // 2

    # Main shape: rounded rect "screen"
    draw.rounded_rectangle([12, 16, 52, 42], radius=4, fill=color, outline=None)

    # "Stand" below screen
    draw.rectangle([26, 42, 38, 48], fill=color)
    draw.rectangle([20, 48, 44, 52], fill=color)

    return img


ICON_RUNNING = create_icon("#4CAF50", "#1a1a2e")   # Green
ICON_STOPPED = create_icon("#666666", "#1a1a2e")    # Gray
ICON_STARTING = create_icon("#FFC107", "#1a1a2e")   # Yellow
ICON_ERROR = create_icon("#f44336", "#1a1a2e")       # Red


# ── Server Process Management ──

class PSSServer:
    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self._monitor_thread = None

    @property
    def running(self):
        with self.lock:
            return self.process is not None and self.process.poll() is None

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return False  # Already running

            python = sys.executable
            env = os.environ.copy()

            # Use pythonw on Windows to avoid console flash
            if platform.system() == "Windows":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE
                self.process = subprocess.Popen(
                    [python, "-m", "pss.server"],
                    cwd=str(SCRIPT_DIR),
                    env=env,
                    startupinfo=startupinfo,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                self.process = subprocess.Popen(
                    [python, "-m", "pss.server"],
                    cwd=str(SCRIPT_DIR),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return True

    def stop(self):
        with self.lock:
            if not self.process:
                return
            try:
                if platform.system() == "Windows":
                    self.process.terminate()
                else:
                    self.process.send_signal(signal.SIGTERM)
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass
            self.process = None

    def restart(self):
        self.stop()
        time.sleep(1)
        self.start()


# ── Autostart Management ──

def get_autostart_enabled():
    system = platform.system()
    if system == "Windows":
        startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return (startup_dir / "PSS.lnk").exists() or (startup_dir / "PSS Tray.lnk").exists()
    elif system == "Linux":
        autostart_dir = Path.home() / ".config" / "autostart"
        return (autostart_dir / "pss-tray.desktop").exists()
    return False


def set_autostart(enabled):
    system = platform.system()

    if system == "Windows":
        startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        lnk_path = startup_dir / "PSS Tray.lnk"

        if enabled:
            try:
                # Create shortcut via PowerShell (no COM dependency)
                pyw_path = SCRIPT_DIR / "pss_tray.pyw"
                ps_cmd = (
                    f'$ws = New-Object -ComObject WScript.Shell; '
                    f'$sc = $ws.CreateShortcut("{lnk_path}"); '
                    f'$sc.TargetPath = "{sys.executable}"; '
                    f'$sc.Arguments = """{pyw_path}"""; '
                    f'$sc.WorkingDirectory = "{SCRIPT_DIR}"; '
                    f'$sc.Description = "PSS System Tray"; '
                    f'$sc.Save()'
                )
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
            python = sys.executable
            desktop_file.write_text(
                f"[Desktop Entry]\n"
                f"Type=Application\n"
                f"Name=PSS Tray\n"
                f"Comment=Plexified Steam Screensaver\n"
                f"Exec={python} {SCRIPT_DIR / 'pss_tray.pyw'}\n"
                f"Path={SCRIPT_DIR}\n"
                f"Terminal=false\n"
                f"X-GNOME-Autostart-enabled=true\n"
                f"StartupNotify=false\n"
            )
        else:
            if desktop_file.exists():
                desktop_file.unlink()


# ── Tray Application ──

class PSSTray:
    def __init__(self):
        self.server = PSSServer()
        self.icon = None
        self._autostart = get_autostart_enabled()

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
        """Background thread: update icon state every 3 seconds."""
        while self.icon and self.icon.visible:
            self._update_icon()
            # Rebuild menu to reflect current state
            try:
                self.icon.update_menu()
            except Exception:
                pass
            time.sleep(3)

    def on_open_customizer(self, icon, item):
        webbrowser.open("http://localhost:8787/customizer")

    def on_open_screensaver(self, icon, item):
        webbrowser.open("http://localhost:8787/screensaver")

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

    def on_quit(self, icon, item):
        self.server.stop()
        icon.stop()

    def _is_running(self, item):
        return self.server.running

    def _is_stopped(self, item):
        return not self.server.running

    def _autostart_checked(self, item):
        return self._autostart

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
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.on_quit),
        )

    def run(self, autostart_server=True):
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

        # Start monitor thread
        threading.Thread(target=self._monitor_loop, daemon=True).start()

        # This blocks until quit
        self.icon.run()


def main():
    # Parse args
    no_server = "--no-server" in sys.argv

    tray = PSSTray()
    tray.run(autostart_server=not no_server)


if __name__ == "__main__":
    main()
