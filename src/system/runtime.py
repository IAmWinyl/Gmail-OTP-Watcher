"""What kind of run this is (packaged or from source) and where the app's
files live."""
import os
import platform
import sys

# The project folder when run from source (src/system/ is two levels down).
_PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows: run helper programs (PowerShell, taskkill) without flashing a
# console window.
NO_WINDOW = 0x08000000


# True when running from the PyInstaller-built .exe rather than source.
FROZEN = getattr(sys, "frozen", False)
# Tray mode puts a status icon in the notification area so a dead
# watcher is visible at a glance rather than silently absent. The packaged
# app is always a tray app, so there are no flags to remember.
TRAY = "--tray" in sys.argv or FROZEN


APP_NAME = "OTP Watcher"


def user_data_dir():
    """The per-user app data folder: Application Support on macOS,
    %LOCALAPPDATA% on Windows."""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    return os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")


def app_dir():
    """Where the user's own files live (token, credentials, log, lock). The
    packaged app uses the per-user app data folder, so the app itself can sit
    anywhere and gets replaced freely on update. Source runs use the project
    folder."""
    if FROZEN:
        path = user_data_dir()
        os.makedirs(path, exist_ok=True)
        return path
    return _PROJECT


def bundled_path(name):
    """A read-only asset shipped inside the build (unpacked to _MEIPASS), or
    from resources/ when run from source."""
    return os.path.join(getattr(sys, "_MEIPASS", None)
                        or os.path.join(_PROJECT, "resources"), name)
