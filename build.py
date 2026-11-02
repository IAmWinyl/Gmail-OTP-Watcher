"""Builds OTP Watcher.exe -- a single windowless executable.

Run: python build.py

The .exe lands in dist/. It is self-contained (Python and all packages are
inside it), but credentials.json must sit NEXT TO it: secrets are deliberately
not baked into the binary, and token.json / otp_watcher.log / the lock file are
written alongside the .exe at runtime. See app_dir() in main.py.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "OTP Watcher"


def make_icon():
    """Render otp_watcher.ico from the same art the tray icon uses."""
    sys.path.insert(0, HERE)
    import tray
    path = os.path.join(HERE, "otp_watcher.ico")
    tray._icon_image(tray.STATES["running"][0], out=256).save(
        path, format="ICO", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
    return path


def main():
    icon = make_icon()
    # --windowed: no console window ever flashes up, since this is a tray app.
    # --onefile: one .exe to copy around, nothing to install.
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", NAME,
        "--icon", icon,
        # The chime is read through bundled_path(), so ship it inside.
        "--add-data", f"otp_chime.wav{os.pathsep}.",
        # pystray/PIL pick their backend at runtime; name it so it gets packed.
        "--hidden-import", "pystray._win32",
        "--hidden-import", "PIL._tkinter_finder",
        "main.py",
    ]
    print(" ".join(cmd))
    if subprocess.run(cmd, cwd=HERE).returncode != 0:
        sys.exit("build failed")

    exe = os.path.join(HERE, "dist", NAME + ".exe")
    print(f"\nBuilt {exe}  ({os.path.getsize(exe) / 1e6:.1f} MB)")

    # Secrets are deliberately NOT baked into the binary, so copy them beside
    # the .exe where app_dir() looks. Without token.json the app opens a
    # browser for consent on first run, which works but is avoidable here.
    for name in ("credentials.json", "token.json"):
        src = os.path.join(HERE, name)
        dst = os.path.join(HERE, "dist", name)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            print(f"Copied {name} next to the .exe")
    print("\nNext: run it once from dist/ to sign in, then pin the tray icon.")


if __name__ == "__main__":
    main()
