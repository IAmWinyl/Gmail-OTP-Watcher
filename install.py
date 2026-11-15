"""Set up OTP Watcher on this machine: build the app, put your Google
credentials in place, and start it now and at every login. The first launch
opens your browser to sign in to Gmail.

Rerun it after pulling new code to update.

Usage:
  python3 install.py              install or update
  python3 install.py --uninstall  stop it and remove it from login
"""
import argparse
import glob
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "OTP Watcher"
LABEL = "com.gmail-otp-watcher"
SYSTEM = platform.system()
MAC = SYSTEM == "Darwin"
WINDOWS = SYSTEM == "Windows"

VENV = os.path.join(HERE, ".venv")
VENV_PY = (os.path.join(VENV, "Scripts", "python.exe") if WINDOWS
           else os.path.join(VENV, "bin", "python3"))

if MAC:
    APP = os.path.expanduser(f"~/Applications/{NAME}.app")
    DATA = os.path.expanduser(f"~/Library/Application Support/{NAME}")
    AGENTS = os.path.expanduser("~/Library/LaunchAgents")
    AGENT = os.path.join(AGENTS, LABEL + ".plist")
    DOMAIN = f"gui/{os.getuid()}"
elif WINDOWS:
    APP = os.path.join(os.environ["LOCALAPPDATA"], "Programs", NAME, NAME + ".exe")
    DATA = os.path.dirname(APP)  # the .exe keeps its files beside itself
    SHORTCUT = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu",
                            "Programs", "Startup", NAME + ".lnk")
else:
    sys.exit("Only macOS and Windows are supported.")

README_SETUP = "the 'Google API setup' section of README.md"


def run(*cmd, check=True, quiet=False):
    return subprocess.run(cmd, check=check,
                          stderr=subprocess.DEVNULL if quiet else None)


def find_credentials():
    """The OAuth client file: already installed, in the project folder, or
    freshly downloaded (Google names it client_secret_<id>.json)."""
    candidates = [os.path.join(DATA, "credentials.json"),
                  os.path.join(HERE, "credentials.json"),
                  os.path.join(HERE, "dist", "credentials.json")]  # old .exe setup
    downloads = (glob.glob(os.path.join(HERE, "client_secret_*.json"))
                 + glob.glob(os.path.expanduser("~/Downloads/client_secret_*.json")))
    candidates += sorted(downloads, key=os.path.getmtime, reverse=True)
    for path in candidates:
        if not os.path.exists(path):
            continue
        with open(path) as f:
            client = json.load(f)
        if "installed" not in client:
            sys.exit(f"{path} is not a Desktop app OAuth client. Create one of type "
                     f"'Desktop app' instead, as described in {README_SETUP}.")
        return path
    sys.exit("Couldn't find your Google OAuth client file (credentials.json, or a "
             "client_secret_*.json in this folder or Downloads).\n"
             f"Follow {README_SETUP}, then run this again.")


def build():
    if not os.path.exists(VENV_PY):
        print("Creating the virtual environment...")
        run(sys.executable, "-m", "venv", VENV)
    print("Installing packages...")
    run(VENV_PY, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
        "-r", os.path.join(HERE, "requirements.txt"), "pyinstaller")
    print("Building the app (this takes a minute)...")
    run(VENV_PY, os.path.join(HERE, "build.py"))
    return os.path.join(HERE, "dist", NAME + (".app" if MAC else ".exe"))


def stop():
    """Stop any running copy, including launch agents from the old manual
    setup (labelled com.<name>.gmailotpwatcher), which would otherwise keep
    starting a second watcher."""
    if MAC:
        run("launchctl", "bootout", f"{DOMAIN}/{LABEL}", check=False, quiet=True)
        for path in glob.glob(os.path.join(AGENTS, "*.plist")):
            with open(path, "rb") as f:
                try:
                    label = plistlib.load(f).get("Label", "")
                except Exception:
                    continue
            if "gmailotpwatcher" in label:
                print(f"Removing the old launch agent {label}")
                run("launchctl", "bootout", f"{DOMAIN}/{label}", check=False)
                os.remove(path)
        run("pkill", "-f", f"{NAME}.app/Contents/MacOS/", check=False)
    else:
        run("taskkill", "/IM", NAME + ".exe", "/F", check=False, quiet=True)
        time.sleep(1)  # let Windows release the .exe before it's replaced


def place_secrets(creds):
    """Copy the credentials (and any existing sign-in) into the app's data
    folder. Done before building, since the old .exe setup kept them in dist/."""
    os.makedirs(DATA, exist_ok=True)
    dest = os.path.join(DATA, "credentials.json")
    if os.path.abspath(creds) != os.path.abspath(dest):
        shutil.copy2(creds, dest)
    # Reuse a sign-in from running it from source or the old .exe setup, so
    # there's no browser trip.
    for token in (os.path.join(HERE, "token.json"), os.path.join(HERE, "dist", "token.json")):
        if os.path.exists(token) and not os.path.exists(os.path.join(DATA, "token.json")):
            shutil.copy2(token, os.path.join(DATA, "token.json"))


def place_app(built):
    if MAC:
        shutil.rmtree(APP, ignore_errors=True)
        os.makedirs(os.path.dirname(APP), exist_ok=True)
        # ditto keeps the bundle's symlinks and signature intact.
        run("ditto", built, APP)
    else:
        shutil.copy2(built, APP)


def start_at_login():
    if MAC:
        os.makedirs(AGENTS, exist_ok=True)
        # No KeepAlive: the tray keeps a failed watcher alive and red on its
        # own, and Quit from the menu should stay quit until next login.
        agent = {
            "Label": LABEL,
            "ProgramArguments": [os.path.join(APP, "Contents", "MacOS", NAME)],
            "RunAtLoad": True,
            "LimitLoadToSessionType": "Aqua",
        }
        with open(AGENT, "wb") as f:
            plistlib.dump(agent, f)
        run("launchctl", "bootstrap", DOMAIN, AGENT)
    else:
        quote = lambda s: s.replace("'", "''")
        ps = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{quote(SHORTCUT)}'); "
              f"$s.TargetPath = '{quote(APP)}'; "
              f"$s.WorkingDirectory = '{quote(DATA)}'; $s.Save()")
        run("powershell", "-NoProfile", "-Command", ps)
        os.startfile(APP)


def install():
    place_secrets(find_credentials())
    built = build()
    stop()
    place_app(built)
    start_at_login()
    print(f"\n{NAME} is installed at {APP} and starts at every login.")
    if not os.path.exists(os.path.join(DATA, "token.json")):
        print("Your browser will open so you can let it read your Gmail. "
              "After that, the tray icon turns green.")
    print(f"Its sign-in and log live in {DATA}")


def uninstall():
    stop()
    if MAC:
        if os.path.exists(AGENT):
            os.remove(AGENT)
        shutil.rmtree(APP, ignore_errors=True)
    else:
        for path in (SHORTCUT, APP):
            if os.path.exists(path):
                os.remove(path)
    print(f"{NAME} is stopped and won't start at login.\n"
          f"Your sign-in and log are still in {DATA} (delete it to forget them).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Install or remove OTP Watcher.")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()
    uninstall() if args.uninstall else install()
