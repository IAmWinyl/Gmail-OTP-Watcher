"""System-tray front end for the OTP watcher.

Keeps a status icon in the notification area for as long as the process lives,
so "no icon" means not running and a red icon means running-but-broken. Without
this, a dead watcher looks exactly like a quiet one.
"""
import os
import sys
import threading
import subprocess
import platform

import pystray
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "otp_watcher.log")

# state -> (dot color, human label)
STATES = {
    "starting": ("#d4a017", "Starting..."),
    "running": ("#2e9e4f", "Watching"),
    "stopped": ("#8a8a8a", "Stopped"),
    "failed": ("#d13438", "NOT RUNNING"),
}


def _icon_image(color, alert=False):
    """Envelope glyph with a status dot, drawn large and downscaled for edges."""
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

    return img.resize((64, 64), Image.LANCZOS)


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

    def set_state(name, detail="", failure=None):
        state["name"], state["detail"] = name, detail
        if failure or name != "failed":
            state["failure"] = failure  # a clean restart clears the old reason
        color, label = STATES.get(name, STATES["stopped"])
        icon.icon = _icon_image(color, alert=(name == "failed"))
        # Windows caps the tooltip near 128 chars, so trim the reason to fit.
        head = "OTP Watcher - " + label
        icon.title = head + (chr(10) + _clip(detail, 120 - len(head)) if detail else "")
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
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    start_worker()
    icon.run()  # blocks on the main thread until Quit
    return result["code"]
