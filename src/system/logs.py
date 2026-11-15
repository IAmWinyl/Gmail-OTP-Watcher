"""Logging: to the console when there is one, otherwise to a size-capped
rotating file in the app's data folder."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from system.runtime import app_dir


# Debug logging records each email's sender and subject and the codes copied.
# Off by default so the log never becomes a record of your mail.
DEBUG = "--debug" in sys.argv
log = logging.getLogger("otp_watcher")


class _StreamToLogger:
    """Minimal writable-stream shim so existing print()/tracebacks flow into a
    rotating log. Buffers partial writes and emits one log record per line."""
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self._buf = ""

    def write(self, msg):
        self._buf += msg
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self.logger.log(self.level, line)

    def flush(self):
        if self._buf:
            self.logger.log(self.level, self._buf)
            self._buf = ""

    def isatty(self):
        return False


def setup_logging():
    """Without a console (the packaged app, or started at login), route output
    to a size-capped rotating logfile in app_dir() so it can never fill the
    disk (~4 MB max total). Console runs log to the console instead."""
    try:
        interactive = sys.stdout is not None and sys.stdout.isatty()
    except Exception:
        interactive = False
    log.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    if interactive:
        if not log.handlers:
            log.addHandler(logging.StreamHandler(sys.stdout))
        return
    log_path = os.path.join(app_dir(), "otp_watcher.log")

    logger = log
    if not logger.handlers:  # avoid duplicate handlers if called twice
        # 1 MB per file, 3 old files kept -> at most ~4 MB on disk, ever.
        handler = RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)

    sys.stdout = _StreamToLogger(logger, logging.INFO)
    sys.stderr = _StreamToLogger(logger, logging.ERROR)
    print("--- watcher started ---")
