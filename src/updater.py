"""Ask-first updates from the GitHub releases of the repo the app was built from.

Release builds (made by the GitHub release workflow) download the new version
and swap themselves out. Self-built copies only say a new version is out, since
replacing them could throw away local code changes.
"""
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

import requests

APP_NAME = "OTP Watcher"
MAC = platform.system() == "Darwin"
_NO_WINDOW = 0x08000000


def _git(*args):
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        out = subprocess.run(["git", "-C", here, *args],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def from_git():
    """(version, "owner/repo") from the checkout: the nearest release tag and
    the GitHub repo it was cloned from. Either can be None."""
    version = _git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    m = re.search(r"github\.com[:/](.+?/.+?)(?:\.git)?/?$", _git("remote", "get-url", "origin") or "")
    return version, (m.group(1) if m else None)


try:
    from _version import VERSION, REPO, RELEASE  # written into builds by build.py
except ImportError:
    VERSION, REPO = from_git()
    RELEASE = False


def _parse(tag):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag or "")
    return tuple(int(x) for x in m.groups()) if m else None


def asset_name():
    if MAC:
        return f"OTP-Watcher-macos-{platform.machine()}.zip"  # arm64 / x86_64
    return "OTP-Watcher-windows.exe"


def newer_release():
    """The newest GitHub release if it's newer than this copy, else None."""
    if not (_parse(VERSION) and REPO):
        return None  # untagged build or not from GitHub: nothing to compare to
    resp = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest",
                        headers={"Accept": "application/vnd.github+json"}, timeout=15)
    if resp.status_code == 404:
        return None  # no releases yet
    resp.raise_for_status()
    release = resp.json()
    newest = _parse(release.get("tag_name"))
    return release if newest and newest > _parse(VERSION) else None


def cleanup():
    """Windows: remove the old .exe an update left behind (it can't delete
    itself while running)."""
    if getattr(sys, "frozen", False) and not MAC:
        old = sys.executable[:-4] + ".old.exe"
        if os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass


def apply(release):
    """Download the release for this machine, put it in place of the running
    app, and start it once this process exits. The caller then quits."""
    asset = next((a for a in release.get("assets", []) if a["name"] == asset_name()), None)
    if asset is None:
        raise RuntimeError(f"Release {release.get('tag_name')} has no {asset_name()}")

    tmp = tempfile.mkdtemp(prefix="otp-watcher-update-")
    download = os.path.join(tmp, asset["name"])
    with requests.get(asset["browser_download_url"], stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(download, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)

    pid = os.getpid()
    if MAC:
        app = os.path.abspath(os.path.join(sys.executable, "..", "..", ".."))
        subprocess.run(["ditto", "-x", "-k", download, tmp], check=True)
        staged = app + ".new"
        shutil.rmtree(staged, ignore_errors=True)
        # Stage beside the app first so a failure never leaves it half-copied.
        # ditto keeps the bundle's symlinks and signature intact.
        subprocess.run(["ditto", os.path.join(tmp, APP_NAME + ".app"), staged], check=True)
        os.rename(app, os.path.join(tmp, "previous.app"))
        os.rename(staged, app)
        subprocess.Popen(["/bin/sh", "-c",
                          f'while kill -0 {pid} 2>/dev/null; do sleep 0.2; done; open "$0"', app],
                         start_new_session=True)
    else:
        exe = sys.executable
        old = exe[:-4] + ".old.exe"
        if os.path.exists(old):
            os.remove(old)
        # Windows won't overwrite a running .exe but will rename it.
        os.rename(exe, old)
        shutil.move(download, exe)
        q = exe.replace("'", "''")
        subprocess.Popen(["powershell", "-NoProfile", "-Command",
                          f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue; Start-Process '{q}'"],
                         creationflags=_NO_WINDOW)
