"""Set up OTP Watcher on this machine: build the app, put your Google
credentials in place, and start it now and at every login. The first launch
opens your browser to sign in to Gmail.

Rerun it after pulling new code to update.

Usage:
  python3 install.py              install or update
  python3 install.py --uninstall  stop it and remove it completely
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
    # The main Applications folder is writable for admin accounts; anyone else
    # gets the one in their home folder.
    APP = (f"/Applications/{NAME}.app" if os.access("/Applications", os.W_OK)
           else os.path.expanduser(f"~/Applications/{NAME}.app"))
    DATA = os.path.expanduser(f"~/Library/Application Support/{NAME}")
    AGENTS = os.path.expanduser("~/Library/LaunchAgents")
    AGENT = os.path.join(AGENTS, LABEL + ".plist")
    DOMAIN = f"gui/{os.getuid()}"
elif WINDOWS:
    APP = os.path.join(os.environ["LOCALAPPDATA"], "Programs", NAME, NAME + ".exe")
    DATA = os.path.join(os.environ["LOCALAPPDATA"], NAME)
    SHORTCUT = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu",
                            "Programs", "Startup", NAME + ".lnk")
else:
    sys.exit("Only macOS and Windows are supported.")

README_SETUP = "the 'Google API setup' section of README.md"


def run(*cmd, check=True, quiet=False):
    return subprocess.run(cmd, check=check,
                          stderr=subprocess.DEVNULL if quiet else None)


def find_credentials():
    """The OAuth client file, if it's already installed or in the project
    folder (Google names the download client_secret_<id>.json). None means the
    app asks for it with a file picker on first launch."""
    candidates = [os.path.join(DATA, "credentials.json"),
                  os.path.join(HERE, "credentials.json"),
                  os.path.join(HERE, "dist", "credentials.json")]  # old .exe setup
    candidates += sorted(glob.glob(os.path.join(HERE, "client_secret_*.json")),
                         key=os.path.getmtime, reverse=True)
    for path in candidates:
        if not os.path.exists(path):
            continue
        with open(path) as f:
            client = json.load(f)
        if "installed" not in client:
            sys.exit(f"{path} is not a Desktop app OAuth client. Create one of type "
                     f"'Desktop app' instead, as described in {README_SETUP}.")
        return path
    return None


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
    if creds and os.path.abspath(creds) != os.path.abspath(dest):
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
        os.makedirs(os.path.dirname(APP), exist_ok=True)
        shutil.copy2(built, APP)


def launch():
    """Start the app. It registers itself to start at login on launch."""
    if MAC:
        run("open", APP)
    else:
        os.startfile(APP)


def install():
    place_secrets(find_credentials())
    built = build()
    stop()
    place_app(built)
    launch()
    print(f"\n{NAME} is installed at {APP} and starts at every login.")
    if not os.path.exists(os.path.join(DATA, "credentials.json")):
        print("It will ask you to choose the client_secret file you downloaded "
              "from your Google Cloud project.")
    if not os.path.exists(os.path.join(DATA, "token.json")):
        print("Your browser will open so you can let it read your Gmail. "
              "After that, the tray icon turns green.")
    print(f"Its sign-in and log live in {DATA}")


def uninstall():
    """Stop it, remove it from login, and delete the app along with its
    sign-in, credentials and log. The project folder isn't touched."""
    stop()
    if MAC:
        if os.path.exists(AGENT):
            os.remove(AGENT)
        shutil.rmtree(APP, ignore_errors=True)
    else:
        if os.path.exists(SHORTCUT):
            os.remove(SHORTCUT)
        import winreg
        try:  # the Installed apps entry the app adds for itself
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Uninstall\OTPWatcher")
        except FileNotFoundError:
            pass
        shutil.rmtree(os.path.dirname(APP), ignore_errors=True)
    shutil.rmtree(DATA, ignore_errors=True)
    print(f"{NAME} is uninstalled: stopped, removed from login, and its app, "
          "sign-in and log are deleted.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Install or remove OTP Watcher.")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()
    uninstall() if args.uninstall else install()
