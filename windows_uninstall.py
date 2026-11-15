"""Windows: list the app under Settings > Apps > Installed apps, and do the
uninstall that entry runs ("OTP Watcher.exe" --uninstall).

Only per-user registry keys are used (HKCU), so no admin rights are needed.
"""
import os
import subprocess
import sys

import autostart
import updater
from main import APP_NAME, ask

KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\OTPWatcher"
_NO_WINDOW = 0x08000000

# Everything the app writes beside itself. Only these are deleted, since the
# .exe may be sitting in a shared folder like Downloads.
OWN_FILES = ("credentials.json", "token.json", "otp_watcher.lock",
             os.path.basename(autostart.OFF_MARKER), APP_NAME + ".old.exe",
             "otp_watcher.log", "otp_watcher.log.1", "otp_watcher.log.2", "otp_watcher.log.3")


def register():
    """Add or refresh the Installed apps entry for the running .exe."""
    import winreg
    exe = sys.executable
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
        for name, value in {
            "DisplayName": APP_NAME,
            "DisplayIcon": exe,
            "DisplayVersion": updater.VERSION or "",
            "InstallLocation": os.path.dirname(exe),
            "UninstallString": f'"{exe}" --uninstall',
        }.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name in ("NoModify", "NoRepair"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)


def unregister():
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, KEY)
    except FileNotFoundError:
        pass


def uninstall():
    """Stop the running copy, remove it from login and Installed apps, and
    delete its sign-in, log and the .exe itself."""
    if not ask(APP_NAME, "Uninstall OTP Watcher? This removes the app, its Gmail "
               "sign-in and its log.", yes="Uninstall", no="Cancel"):
        return 1
    exe, me = sys.executable, os.getpid()
    subprocess.run(["taskkill", "/IM", os.path.basename(exe), "/F", "/FI", f"PID ne {me}"],
                   capture_output=True, creationflags=_NO_WINDOW)
    if os.path.exists(autostart.ENTRY):
        os.remove(autostart.ENTRY)
    unregister()
    folder = os.path.dirname(exe)
    for name in OWN_FILES:
        path = os.path.join(folder, name)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    # A running .exe can't delete itself, so a helper removes it once this
    # process exits, and the folder too if nothing else is left in it.
    q = lambda s: s.replace("'", "''")
    subprocess.Popen(["powershell", "-NoProfile", "-Command",
                      f"Wait-Process -Id {me} -ErrorAction SilentlyContinue; "
                      f"Remove-Item -LiteralPath '{q(exe)}' -Force; "
                      f"if (-not (Get-ChildItem -LiteralPath '{q(folder)}' -Force)) "
                      f"{{ Remove-Item -LiteralPath '{q(folder)}' -Force }}"],
                     creationflags=_NO_WINDOW)
    return 0
