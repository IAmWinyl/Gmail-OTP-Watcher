"""Making sure only one copy runs, and offering to take over from another."""
import ctypes
import os
import platform
import re
import subprocess
import sys
import time

from system.dialogs import ask, notify
from system.runtime import NO_WINDOW, TRAY, app_dir, user_data_dir


_mutex_handle = None  # Windows: kept alive for the process lifetime
_lock_file = None     # macOS/POSIX: kept open to hold the flock


def _pid_file():
    """Where the copy holding the lock records its process ID. Always the
    per-user data folder, so a source run and the app still find each other."""
    os.makedirs(user_data_dir(), exist_ok=True)
    return os.path.join(user_data_dir(), "running.pid")


def _running_copy():
    """PID of the other running OTP Watcher, if it recorded one and that
    process really is an OTP Watcher (a stale file could name a reused PID)."""
    try:
        with open(_pid_file()) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    if pid == os.getpid():
        return None
    if platform.system() == "Windows":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    else:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True).stdout
    return pid if re.search(r"otp[ _-]?watcher|main\.py", out, re.IGNORECASE) else None


def _stop_copy(pid):
    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, creationflags=NO_WINDOW)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _already_running(try_lock):
    """Another copy holds the lock. Offer to close it and take over, and
    otherwise say so out loud: a tray build has no console, so exiting quietly
    looks identical to never starting."""
    print("Another instance is already running.")
    if not TRAY:
        sys.exit(0)
    pid = _running_copy()
    if pid and ask("OTP Watcher is already running",
                   "Another copy of OTP Watcher is already running. Close it "
                   "and use this one instead?", yes="Use this one", no="Cancel"):
        _stop_copy(pid)
        for _ in range(50):  # up to ~10s for it to exit and free the lock
            time.sleep(0.2)
            if try_lock():
                return
    elif not pid:
        notify("OTP Watcher is already running",
               "Another copy already has the lock, so this one closed and no "
               "second tray icon appeared." + chr(10) + chr(10) +
               "If you cannot see its icon, the copy holding the lock was "
               "probably started some other way, like an old manual setup or "
               "a terminal. Stop that one, then start this app again.")
    sys.exit(0)


def ensure_single_instance():
    """Make sure only one copy of the watcher runs, so codes don't get copied
    and chimed twice, then record this copy's PID for the next one to find."""
    global _lock_file
    if platform.system() == "Windows":
        ERROR_ALREADY_EXISTS = 183
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]

        def try_lock():
            global _mutex_handle
            if _mutex_handle:
                # Holding a handle keeps the mutex alive, so let go before
                # checking whether the other copy's is gone.
                k32.CloseHandle(ctypes.c_void_p(_mutex_handle))
            _mutex_handle = k32.CreateMutexW(None, False, "OTP_Watcher_single_instance_v1")
            return k32.GetLastError() != ERROR_ALREADY_EXISTS
    else:
        # POSIX: non-blocking flock, auto-released by the OS on exit (no stale
        # lock). Keep the file object alive in a global.
        import fcntl
        _lock_file = open(os.path.join(app_dir(), "otp_watcher.lock"), "w")

        def try_lock():
            try:
                fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False

    if not try_lock():
        _already_running(try_lock)
    try:
        with open(_pid_file(), "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
