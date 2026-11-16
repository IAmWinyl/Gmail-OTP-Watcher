"""OTP Watcher: copies sign-in codes from Gmail to your clipboard.

Run with --tray for the menu bar / tray icon (the packaged app always is one),
and --debug to also log each email's sender and subject and the codes copied.
"""
import platform
import sys

from system.logs import setup_logging
from system.runtime import FROZEN, TRAY
from system.single_instance import ensure_single_instance
from watcher import run_watcher


def main():
    if FROZEN and platform.system() == "Windows" and "--uninstall" in sys.argv:
        # Before logging and the instance lock: both would hold files open
        # that the uninstall needs to delete.
        from system import windows_uninstall
        sys.exit(windows_uninstall.uninstall())
    setup_logging()
    ensure_single_instance()
    if TRAY:
        import setup_window
        if setup_window.needed() and not setup_window.run():
            sys.exit(0)  # closed before finishing; it opens again next launch
        import tray
        sys.exit(tray.run(run_watcher))
    sys.exit(run_watcher())


if __name__ == "__main__":
    main()
