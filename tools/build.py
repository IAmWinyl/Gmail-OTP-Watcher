"""Builds the standalone app: OTP Watcher.exe on Windows, OTP Watcher.app on
macOS. Python and all packages are inside it.

Run: python tools/build.py   (install.py runs this for you)
     python tools/build.py --release   (the GitHub release workflow; lets the app
                                  update itself from later releases)

The result lands in dist/. Secrets are deliberately not baked in: the app reads
credentials.json and writes token.json / its log from app_dir() in main.py.
"""
import os
import platform
import plistlib
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
BUILD = os.path.join(ROOT, "build")
NAME = "OTP Watcher"
BUNDLE_ID = "com.gmail-otp-watcher"
MAC = platform.system() == "Darwin"


def make_icon():
    """Render the app icon from the same art the tray icon uses."""
    sys.path.insert(0, SRC)
    import tray
    img = tray._icon_image(tray.STATES["running"][0], out=256)
    os.makedirs(BUILD, exist_ok=True)
    if MAC:
        # PyInstaller converts a PNG to .icns itself (via Pillow).
        path = os.path.join(BUILD, "otp_watcher.png")
        img.save(path, format="PNG")
    else:
        path = os.path.join(BUILD, "otp_watcher.ico")
        img.save(path, format="ICO",
                 sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
    return path


def hide_from_dock(app):
    """Mark the .app as menu-bar-only (no Dock icon, no app menu). PyInstaller
    has no flag for this, and editing Info.plist breaks the bundle's ad-hoc
    signature, which Apple Silicon refuses to launch, so re-sign after."""
    info = os.path.join(app, "Contents", "Info.plist")
    with open(info, "rb") as f:
        plist = plistlib.load(f)
    plist["LSUIElement"] = True
    with open(info, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", app], check=True)


def write_version(release):
    """Bake the version, source repo and build kind into the app, read by
    updater.py. Removed after the build so source runs ask git instead."""
    sys.path.insert(0, SRC)
    import updater
    version, repo = updater.from_git()
    path = os.path.join(SRC, "_version.py")
    with open(path, "w") as f:
        f.write(f"VERSION = {version!r}\nREPO = {repo!r}\nRELEASE = {release!r}\n")
    print(f"Version {version or '(untagged)'} from {repo or '(no GitHub remote)'}")
    return path


def main():
    release = "--release" in sys.argv
    icon = make_icon()
    version_file = write_version(release)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--log-level", "ERROR",
        # --windowed: no console window, since this is a tray app. On macOS it
        # also makes the .app bundle.
        "--windowed",
        "--name", NAME,
        "--icon", icon,
        # The chime is read through bundled_path(), so ship it inside.
        "--add-data", f"{os.path.join(SRC, 'otp_chime.wav')}{os.pathsep}.",
        # Keep PyInstaller's generated spec file out of the project root.
        "--specpath", BUILD,
    ]
    if MAC:
        # pystray picks its backend at runtime; name it so it gets packed.
        cmd += ["--osx-bundle-identifier", BUNDLE_ID,
                "--hidden-import", "pystray._darwin"]
    else:
        # --onefile: one .exe to copy around. (On macOS a .app is already one
        # thing to copy, and PyInstaller is dropping onefile .app support.)
        cmd += ["--onefile",
                "--hidden-import", "pystray._win32",
                "--hidden-import", "PIL._tkinter_finder"]
    cmd.append(os.path.join(SRC, "main.py"))

    print(" ".join(cmd))
    try:
        if subprocess.run(cmd, cwd=ROOT).returncode != 0:
            sys.exit("build failed")
    finally:
        os.remove(version_file)

    if MAC:
        built = os.path.join(ROOT, "dist", NAME + ".app")
        hide_from_dock(built)
    else:
        built = os.path.join(ROOT, "dist", NAME + ".exe")
    print(f"\nBuilt {built}")
    return built


if __name__ == "__main__":
    main()
