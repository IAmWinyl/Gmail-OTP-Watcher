"""First-launch setup: one window that walks you through choosing your Google
client file and signing in to Gmail, before the tray icon starts.

Runs on the main thread and is fully closed before the tray starts, since on
macOS both Tk and the tray need the main thread.
"""
import json
import os
import platform
import queue
import shutil
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk

import watcher
from system import updater

WRAP = 400
GREY, GREEN, RED = "#6b6b6b", "#1e7e34", "#c42b1c"
TRAY_PLACE = "menu bar" if platform.system() == "Darwin" else "notification area"


def needed():
    token_path, _ = watcher.paths()
    return not os.path.exists(token_path)


def _check_client_file(path):
    """None if path is a Desktop app OAuth client file, else what's wrong."""
    try:
        with open(path) as f:
            client = json.load(f)
    except (OSError, ValueError):
        return "That file isn't a Google client file. Pick the .json you downloaded."
    if "installed" not in client:
        return ("That's not a Desktop app client. Create an OAuth client of type "
                "Desktop app in your Google Cloud project and download that one.")
    return None


def run():
    """Show the setup window until you finish or close it. True if finished."""
    token_path, creds_path = watcher.paths()
    finished = {"ok": False}
    results = queue.Queue()  # sign-in thread -> Tk thread

    root = tk.Tk()
    root.title("OTP Watcher Setup")
    root.resizable(False, False)
    base = ttk.Style().lookup("TLabel", "font") or "TkDefaultFont"
    bold = tkfont.Font(font=base)
    bold.configure(weight="bold")
    title_font = tkfont.Font(font=base)
    title_font.configure(weight="bold", size=title_font.cget("size") + 6)

    frame = ttk.Frame(root, padding=24)
    frame.grid()

    def label(text="", font=None, color=None, pady=(0, 0)):
        w = ttk.Label(frame, text=text, wraplength=WRAP, font=font, foreground=color)
        w.grid(sticky="w", pady=pady)
        return w

    label("Set up OTP Watcher", title_font)
    label("Two quick steps, then it copies sign-in codes from your Gmail as they "
          "arrive.", color=GREY, pady=(4, 18))

    # Step 1: the OAuth client file from your own Google Cloud project.
    label("1. Choose your Google client file", bold)
    step1_note = label("The client_secret_....json you downloaded from your Google "
                       "Cloud project.", color=GREY, pady=(2, 6))
    step1_row = ttk.Frame(frame)
    step1_row.grid(sticky="w", pady=(0, 18))
    choose = ttk.Button(step1_row, text="Choose file...")
    choose.grid(row=0, column=0)
    if updater.REPO:
        help_link = ttk.Label(step1_row, text="How do I get this?", foreground="#0a64d6",
                              cursor="hand2")
        help_link.grid(row=0, column=1, padx=(12, 0))
        help_link.bind("<Button-1>", lambda _: webbrowser.open(
            f"https://github.com/{updater.REPO}#1-google-api-setup"))

    # Step 2: Google's consent screen in the browser.
    label("2. Sign in to Gmail", bold)
    step2_note = label("Your browser opens so you can let OTP Watcher read your Gmail. "
                       "Google warns the app isn't verified, since it's your own "
                       "project: choose Advanced, then Go to ... (unsafe).",
                       color=GREY, pady=(2, 6))
    sign_in = ttk.Button(frame, text="Sign in...", state="disabled")
    sign_in.grid(sticky="w", pady=(0, 18))

    all_set = label(pady=(0, 12))
    done = ttk.Button(frame, text="Done", state="disabled")
    done.grid(sticky="e")

    def step1_done(name):
        step1_note.configure(text=f"✓ Using {name}", foreground=GREEN)
        choose.configure(text="Choose a different file...")
        sign_in.configure(state="normal")

    def on_choose():
        path = filedialog.askopenfilename(
            parent=root, title="Choose your Google client file",
            filetypes=[("Google client file", "*.json")])
        if not path:
            return
        problem = _check_client_file(path)
        if problem:
            step1_note.configure(text=problem, foreground=RED)
            return
        shutil.copy2(path, creds_path)
        step1_done(os.path.basename(path))

    def on_sign_in():
        sign_in.configure(state="disabled")
        choose.configure(state="disabled")
        step2_note.configure(text="Waiting for you to approve in your browser...",
                             foreground=GREY)

        def work():
            try:
                watcher.sign_in(creds_path, token_path, open_browser=True, success_message=(
                    "OTP Watcher is signed in. You can close this tab and go back "
                    "to the setup window."))
                results.put(None)
            except Exception as e:
                results.put(e)

        threading.Thread(target=work, daemon=True).start()
        root.after(200, poll_sign_in)

    def poll_sign_in():
        try:
            error = results.get_nowait()
        except queue.Empty:
            root.after(200, poll_sign_in)
            return
        choose.configure(state="normal")
        if error:
            step2_note.configure(text=f"Sign-in didn't finish: {error}", foreground=RED)
            sign_in.configure(state="normal")
            return
        step2_note.configure(text="✓ Signed in", foreground=GREEN)
        sign_in.configure(text="Signed in")
        done.configure(state="normal")
        all_set.configure(text=f"All set. OTP Watcher lives in your {TRAY_PLACE} from "
                          "now on, and starts on its own when you log in.")
        root.lift()

    def on_done():
        finished["ok"] = True
        root.destroy()

    choose.configure(command=on_choose)
    sign_in.configure(command=on_sign_in)
    done.configure(command=on_done)
    if os.path.exists(creds_path) and not _check_client_file(creds_path):
        step1_done("the client file you set up before")

    # Centre it and bring it to the front: a menu bar app's first window
    # otherwise opens behind whatever you were using.
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f"+{x}+{y}")
    root.lift()
    root.attributes("-topmost", True)
    root.after(500, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.mainloop()
    return finished["ok"]
