"""Windows: list the app under Settings > Apps > Installed apps, and do the
uninstall that entry runs ("OTP Watcher.exe" --uninstall).

Only per-user registry keys are used (HKCU), so no admin rights are needed.
"""
import os
import shutil
import subprocess
import sys

from system.dialogs import ask
from system.runtime import APP_NAME, NO_WINDOW, app_dir
from system import autostart, updater

KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\OTPWatcher"


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
                   capture_output=True, creationflags=NO_WINDOW)
    if os.path.exists(autostart.ENTRY):
        os.remove(autostart.ENTRY)
    unregister()
    shutil.rmtree(app_dir(), ignore_errors=True)  # sign-in, credentials, log
    # A running .exe can't delete itself, so a helper removes it (and the copy
    # an update left behind) once this process exits.
    q = lambda s: s.replace("'", "''")
    subprocess.Popen(["powershell", "-NoProfile", "-Command",
                      f"Wait-Process -Id {me} -ErrorAction SilentlyContinue; "
                      f"Remove-Item -LiteralPath '{q(exe)}', '{q(exe[:-4] + '.old.exe')}' "
                      f"-Force -ErrorAction SilentlyContinue"],
                     creationflags=NO_WINDOW)
    return 0
