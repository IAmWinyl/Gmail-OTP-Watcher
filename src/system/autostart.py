"""Start-at-login registration for the packaged app, owned by the app itself so
a downloaded copy sets itself up on first launch.

macOS: a launch agent in ~/Library/LaunchAgents. Windows: a shortcut in the
Startup folder. Both point at whatever executable is running, so moving the
app and relaunching it re-points them.
"""
import os
import platform
import plistlib
import subprocess
import sys

from system.runtime import APP_NAME, NO_WINDOW, app_dir

LABEL = "com.gmail-otp-watcher"
MAC = platform.system() == "Darwin"

# Present when you've turned off start-at-login from the tray menu, so a later
# launch doesn't quietly turn it back on.
OFF_MARKER = os.path.join(app_dir(), "start-at-login-off")

if MAC:
    ENTRY = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
else:
    ENTRY = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                         "Start Menu", "Programs", "Startup", APP_NAME + ".lnk")


def is_on():
    return os.path.exists(ENTRY)


def _write_entry():
    if MAC:
        agent = {
            "Label": LABEL,
            "ProgramArguments": [sys.executable],
            "RunAtLoad": True,
            "LimitLoadToSessionType": "Aqua",
        }
        if os.path.exists(ENTRY):
            with open(ENTRY, "rb") as f:
                if plistlib.load(f) == agent:
                    return
        os.makedirs(os.path.dirname(ENTRY), exist_ok=True)
        # Written, not loaded: loading it now would start a second copy. launchd
        # picks it up at the next login.
        with open(ENTRY, "wb") as f:
            plistlib.dump(agent, f)
    else:
        q = lambda s: s.replace("'", "''")
        ps = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{q(ENTRY)}'); "
              f"$s.TargetPath = '{q(sys.executable)}'; "
              f"$s.WorkingDirectory = '{q(os.path.dirname(sys.executable))}'; $s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       check=True, creationflags=NO_WINDOW)


def sync():
    """On launch: register (or re-point) start-at-login unless it's been
    turned off. Only for the packaged app; source runs aren't registered."""
    if getattr(sys, "frozen", False) and not os.path.exists(OFF_MARKER):
        try:
            _write_entry()
        except Exception as e:  # never worth keeping the watcher from starting
            print(f"(couldn't register start at login: {e})")


def turn_on():
    if os.path.exists(OFF_MARKER):
        os.remove(OFF_MARKER)
    _write_entry()


def turn_off():
    open(OFF_MARKER, "w").close()
    if os.path.exists(ENTRY):
        os.remove(ENTRY)
