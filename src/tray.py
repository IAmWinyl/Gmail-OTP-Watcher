"""System-tray front end for the OTP watcher.

Keeps a status icon in the notification area for as long as the process lives,
so "no icon" means not running and a red icon means running-but-broken. Without
this, a dead watcher looks exactly like a quiet one.
"""
import os
import sys
import threading
import time
import subprocess
import platform

import pystray
from PIL import Image, ImageDraw

from system import autostart, updater
from system.dialogs import ask, notify
from system.logs import log
from system.runtime import app_dir

UPDATE_CHECK_SECONDS = 24 * 3600
# How often to look at the clock between checks. A plain 24h sleep only counts
# time the computer is awake, so a laptop that's mostly asleep would go days
# between checks; waking up briefly and comparing wall-clock time catches up
# within this long of the computer waking.
UPDATE_POLL_SECONDS = 15 * 60

HERE = app_dir()
LOG_PATH = os.path.join(HERE, "otp_watcher.log")

# state -> (dot color, human label)
STATES = {
    "starting": ("#d4a017", "Starting..."),
    "running": ("#2e9e4f", "Watching"),
    "stopped": ("#8a8a8a", "Stopped"),
    "failed": ("#d13438", "NOT RUNNING"),
}


def _icon_image(color, alert=False, out=64):
    """Envelope glyph with a status dot, drawn large and downscaled for edges.
    out=256 gives the full-detail render the app icon build needs."""
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Envelope body
    box = (24, 56, 232, 192)
    d.rounded_rectangle(box, radius=16, fill="#f2f2f2", outline="#3c3c3c", width=10)
    # Flap
    d.line([(24, 64), (128, 148), (232, 64)], fill="#3c3c3c", width=12, joint="curve")

    # Status dot, bottom-right
    d.ellipse((150, 150, 246, 246), fill=color, outline="#ffffff", width=10)
    if alert:
        d.line([(198, 172), (198, 208)], fill="#ffffff", width=12)
        d.ellipse((190, 220, 206, 236), fill="#ffffff")

    return img if out == size else img.resize((out, out), Image.LANCZOS)


def _open_log():
    if not os.path.exists(LOG_PATH):
        return  # nothing logged yet (interactive run)
    if platform.system() == "Windows":
        os.startfile(LOG_PATH)  # noqa: S606 - user-initiated, fixed path
    elif platform.system() == "Darwin":
        subprocess.run(["open", LOG_PATH])
    else:
        subprocess.run(["xdg-open", LOG_PATH])


def _clip(text, limit):
    """Single-line, length-capped text for tooltips and menu labels."""
    text = " ".join(str(text).split())
    limit = max(limit, 12)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def run(run_watcher, show_failure):
    """Show the tray icon and run the watcher beneath it. Returns an exit code.

    show_failure(heading, message, details) re-opens the error dialog on demand,
    so the reason stays reachable after the first popup is dismissed.
    """
    result = {"code": 0}
    state = {"name": "starting", "detail": "", "failure": None}
    worker = {"thread": None}

    icon = pystray.Icon("otp_watcher", _icon_image(STATES["starting"][0]),
                        "OTP Watcher - Starting...")

    def apply_state(name, detail, failure):
        state["name"], state["detail"] = name, detail
        if failure or name != "failed":
            state["failure"] = failure  # a clean restart clears the old reason
        color, label = STATES.get(name, STATES["stopped"])
        icon.icon = _icon_image(color, alert=(name == "failed"))
        # Windows caps the tooltip near 128 chars, so trim the reason to fit.
        head = "OTP Watcher - " + label
        icon.title = head + (chr(10) + _clip(detail, 120 - len(head)) if detail else "")
        icon.update_menu()

    def on_main(fn, *args):
        if platform.system() == "Darwin":
            # AppKit kills the process if the menu bar is touched off the main
            # thread, and the watcher and update checks run on their own.
            from PyObjCTools import AppHelper
            AppHelper.callAfter(fn, *args)
        else:
            fn(*args)

    def set_state(name, detail="", failure=None):
        on_main(apply_state, name, detail, failure)

    update = {"release": None}

    def update_now(release):
        try:
            updater.apply(release)
        except Exception as e:
            log.error(f"Update failed: {e}")
            notify("OTP Watcher couldn't update",
                   f"{e}\n\nIt's still running version {updater.VERSION}.")
            return
        on_main(icon.stop)  # the new version starts once this one exits

    def offer_update(release, asked_from_menu=False):
        tag = release["tag_name"]
        if not updater.RELEASE:
            ask("OTP Watcher update",
                f"OTP Watcher {tag} is out (you have {updater.VERSION}). To update, "
                "pull the latest code and run install.py again.")
        elif asked_from_menu or ask(
                "OTP Watcher update",
                f"OTP Watcher {tag} is available (you have {updater.VERSION}). "
                "Update now? It restarts on its own afterwards.",
                yes="Update", no="Later"):
            update_now(release)

    def check_for_updates():
        while True:
            last_check = time.time()
            try:
                release = updater.newer_release()
            except Exception as e:
                log.info(f"Update check failed: {e}")
                release = None
            if release:
                update["release"] = release
                on_main(icon.update_menu)
                offer_update(release)
            while time.time() - last_check < UPDATE_CHECK_SECONDS:
                time.sleep(UPDATE_POLL_SECONDS)

    def update_text(_):
        release = update["release"]
        return f"Update available: {release['tag_name']}" if release else ""

    def on_update(_):
        release = update["release"]
        if release:
            threading.Thread(target=offer_update, args=(release, True), daemon=True).start()

    def on_start_at_login(_):
        autostart.turn_off() if autostart.is_on() else autostart.turn_on()
        icon.update_menu()

    def start_worker():
        def body():
            result["code"] = 0  # a restart clears the previous run's code
            result["code"] = run_watcher(set_state)
        worker["thread"] = threading.Thread(target=body, daemon=True)
        worker["thread"].start()

    def status_text(_):
        label = STATES.get(state["name"], STATES["stopped"])[1]
        line = "OTP Watcher: " + label
        return line + (" - " + _clip(state["detail"], 60) if state["detail"] else "")

    def can_restart(_):
        t = worker["thread"]
        return t is None or not t.is_alive()

    def has_failure(_):
        return state["failure"] is not None

    def on_show_error(_):
        # The dialog blocks its thread, so keep it off the tray's thread.
        failure = state["failure"]
        if failure:
            threading.Thread(target=lambda: show_failure(*failure),
                             daemon=True).start()

    def on_restart(_):
        if can_restart(None):
            set_state("starting", "Restarting...")
            start_worker()

    def on_quit(_):
        icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Why it stopped...", on_show_error, visible=has_failure),
        pystray.MenuItem("Open log", lambda _: _open_log()),
        pystray.MenuItem("Restart watcher", on_restart, visible=can_restart),
        pystray.MenuItem(update_text, on_update, visible=lambda _: update["release"] is not None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start at login", on_start_at_login,
                         checked=lambda _: autostart.is_on(),
                         visible=getattr(sys, "frozen", False)),
        pystray.MenuItem("Quit", on_quit),
    )

    updater.cleanup()
    autostart.sync()
    if getattr(sys, "frozen", False) and platform.system() == "Windows":
        try:
            from system import windows_uninstall
            windows_uninstall.register()
        except Exception as e:
            log.info(f"Couldn't add to Installed apps: {e}")
    def setup(icon):
        # Start the watcher only once the icon is showing: on Windows, a state
        # change that lands while it's still appearing is lost, and the dot
        # stays amber after the watcher is actually running.
        icon.visible = True
        start_worker()
        threading.Thread(target=check_for_updates, daemon=True).start()

    icon.run(setup)  # blocks on the main thread until Quit
    return result["code"]
