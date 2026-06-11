import base64
import os.path
import sys
import time
import re
import unicodedata
import html as html_lib
import datetime
import ctypes
import subprocess
import logging
from logging.handlers import RotatingFileHandler
import pyperclip
import platform
import email
from email.header import decode_header, make_header

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Only copy/beep when you've used the keyboard/mouse within this many seconds.
# When you've been away longer (e.g. doing the verification on your phone), new
# codes are silently skipped. Raise this if it ever skips a code you wanted.
IDLE_LIMIT_SECONDS = 60


def idle_seconds():
    """Seconds since the last keyboard/mouse input. Supports Windows and macOS;
    returns 0 on other systems (so they always count as 'active')."""
    system = platform.system()
    if system == "Windows":
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        # 32-bit unsigned subtraction handles the ~49.7-day GetTickCount wraparound
        millis = (tick - info.dwTime) & 0xFFFFFFFF
        return millis / 1000.0

    if system == "Darwin":
        # HIDIdleTime = nanoseconds since the last HID (keyboard/mouse) event.
        try:
            out = subprocess.run(
                ["ioreg", "-c", "IOHIDSystem", "-d", "4"],
                capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
            if m:
                return int(m.group(1)) / 1_000_000_000.0  # ns -> s
        except Exception as e:
            print(f"(idle check failed: {e})")
        return 0.0
    return 0.0


def decode_hdr(value):
    """Decode an RFC2047-encoded header (e.g. =?UTF-8?B?...?=) to plain text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def to_readable_text(s):
    """Reduce a string to the plain text a human would read, so rendering
    artifacts (URLs, CRLF/tab padding, &nbsp;, full-width digits, ...) can't
    inflate the keyword-to-code distance the matcher scores on."""
    if not s:
        return ""

    # Drop URLs
    s = re.sub(r"https?://\S+", " ", s)

    # Normalize to NFKC
    s = unicodedata.normalize("NFKC", s)

    # Drop zero-width and other format/control chars
    s = "".join(
        ch for ch in s
        if ch in "\t\n\r" or not unicodedata.category(ch).startswith("C")
    )

    # Collapse whitespace
    return re.sub(r"\s+", " ", s).strip()


def extract_code(subject, body):
    """Pull a verification code or magic link out of an email using regex.

    Strategy: find every standalone 4-8 digit run, score each by how close it
    sits to a verification keyword on EITHER side, and return the closest one.
    This avoids grabbing reference numbers/dates/amounts, and handles subjects
    where the number comes before the word 'code'. Falls back to a magic link.
    """
    text = (subject or "") + "\n" + (body or "")

    # Require a verification signal near the numbers
    KW = re.compile(
        r"verification|verify|passcode|one[\s-]?time|\botp\b|authenticat|2fa"
        r"|(?:your|this|the|enter|following|security|login|access|confirmation|sign[\s-]?in)\s+(?:code|pin)"
        r"|(?:code|pin)\s*[:=]"
        r"|(?:code|pin)\s+(?:is|are|was|below)\b",
        re.IGNORECASE,
    )
    WINDOW = 50  # chars between keyword and code

    # Canonicalize so distance reflects words, not markup
    clean_text = to_readable_text(text)

    best = None
    for m in re.finditer(r"(?<!\d)(\d{4,8})(?!\d)", clean_text):
        s, e = m.start(), m.end()
        before = clean_text[max(0, s - WINDOW):s]
        after = clean_text[e:e + WINDOW]
        # Distance to the nearest keyword on either side
        dist = None
        for km in KW.finditer(before):
            d = len(before) - km.end()
            dist = d if dist is None else min(dist, d)
        for km in KW.finditer(after):
            d = km.start()
            dist = d if dist is None else min(dist, d)
        if dist is None:
            continue

        # Closest keyword wins; tie-break toward 6-digit codes, then position
        digits = m.group(1)
        score = (dist, 0 if len(digits) == 6 else 1, s)
        if best is None or score < best[0]:
            best = (score, digits)
    if best:
        return best[1]

    # Magic-link fallback: a genuine sign-in link, not an unsubscribe/preferences
    # URL (those contain "login"/"email" and caused false positives)
    if KW.search(text):
        for lm in re.finditer(r"https?://\S+", text):
            u = lm.group()
            if re.search(r"verify|magic|token|otp|confirm|sign[-_]?in", u, re.IGNORECASE) \
               and not re.search(r"unsubscrib|preferenc|revoke|optout|opt-out|manage|email",
                                 u, re.IGNORECASE):
                return u.rstrip(").,>\"']}")

    return None


# Custom "OTP copied" chime, expected next to this script.
SOUND_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "otp_chime.wav")


def beep():
    """Play the distinct OTP chime. Falls back to a system sound if missing."""
    system = platform.system()
    have_file = os.path.exists(SOUND_FILE)
    try:
        if system == "Windows":
            import winsound
            if have_file:
                winsound.PlaySound(SOUND_FILE,
                                   winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.MessageBeep()
        elif system == "Darwin": # MacOS
            sound = SOUND_FILE if have_file else "/System/Library/Sounds/Glass.aiff"
            subprocess.run(["afplay", sound])
        else:  # Linux/other
            if have_file:
                # Try ALSA, fall back to PulseAudio
                if subprocess.run(["aplay", "-q", SOUND_FILE],
                                  stderr=subprocess.DEVNULL).returncode != 0:
                    subprocess.run(["paplay", SOUND_FILE], stderr=subprocess.DEVNULL)
            else:
                print("\a", end="", flush=True)  # terminal bell
    except Exception as e:
        print(f"(beep failed: {e})")


def process_email(subject, body):
    """Copy the code and beep. Returns True if a code was found/copied."""
    code = extract_code(subject, body)
    if not code:
        print("No validation code or link found in the email")
        return False
    pyperclip.copy(code)
    print("Copied to clipboard:", code)
    beep()
    return True


def get_body(mime_msg):
    """Return the best-effort plain-text body of an email.

    Prefers text/plain. Falls back to text/html with tags and style/script
    blocks stripped and HTML entities unescaped, so the regex sees real text.
    """
    plain_parts = []
    html_parts = []

    if mime_msg.is_multipart():
        for part in mime_msg.walk():
            ctype = part.get_content_type()
            if ctype not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)  # decode transfer-encoding
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            txt = payload.decode(charset, errors="replace")
            (plain_parts if ctype == "text/plain" else html_parts).append(txt)
    else:
        payload = mime_msg.get_payload(decode=True)
        if payload is not None:
            charset = mime_msg.get_content_charset() or "utf-8"
            txt = payload.decode(charset, errors="replace")
            if mime_msg.get_content_type() == "text/html":
                html_parts.append(txt)
            else:
                plain_parts.append(txt)

    if plain_parts:
        return "\n".join(plain_parts)
    if html_parts:
        raw = "\n".join(html_parts)
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw,
                     flags=re.DOTALL | re.IGNORECASE)
        raw = re.sub(r"<[^>]+>", " ", raw)
        return html_lib.unescape(raw)
    return ""


def fetch_email(email_id, creds):
    service = build("gmail", "v1", credentials=creds)
    msg = service.users().messages().get(
        userId="me", id=email_id, format="raw").execute()
    try:
        mime_msg = email.message_from_bytes(base64.urlsafe_b64decode(msg["raw"]))
        subject = decode_hdr(mime_msg["subject"])
        from_name = decode_hdr(mime_msg["from"])
        body = get_body(mime_msg)
        print(f"From: {from_name}\nSubject: {subject}\nBody length: {len(body)}")
    except Exception as e:
        print(f"An error occurred parsing email: {e}")
        return

    process_email(subject, body)


def poll_for_new_emails(creds):
    service = build("gmail", "v1", credentials=creds)
    user_id = "me"
    start_history_id = service.users().getProfile(
        userId=user_id).execute()["historyId"]

    while True:
        try:
            changes = []
            page_token = None
            while True:
                resp = service.users().history().list(
                    userId=user_id,
                    startHistoryId=start_history_id,
                    pageToken=page_token,
                ).execute()
                changes.extend(resp.get("history", []))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            start_history_id = resp["historyId"]

            seen = set()
            for change in changes:
                for added in change.get("messagesAdded", []):
                    msg = added["message"]
                    mid = msg["id"]
                    if mid in seen:
                        continue
                    seen.add(mid)
                    labels = set(msg.get("labelIds", []))
                    print(f"detected {mid} labels={sorted(labels)}")
                    if "INBOX" not in labels:
                        continue  # not in inbox (draft/trash/etc.)
                    idle = idle_seconds()
                    if idle > IDLE_LIMIT_SECONDS:
                        # Skip silently; history still advances so it won't replay
                        print(f"skipping {mid} (idle {int(idle)}s)")
                        continue
                    fetch_email(mid, creds)

            time.sleep(0.8)

        except HttpError as error:
            if error.resp.status == 404:
                # startHistoryId expired/too old: resync to current.
                start_history_id = service.users().getProfile(
                    userId=user_id).execute()["historyId"]
                print("History expired; resynced.")
            else:
                print(f"HTTP error, retrying: {error}")
                time.sleep(2)
        except Exception as error:
            print(f"An error occurred, retrying: {error}")
            time.sleep(2)


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
    """When launched in the background, route output to a size-capped rotating
    logfile next to the script so it can never fill the disk."""
    force = "--background" in sys.argv
    try:
        interactive = sys.stdout is not None and sys.stdout.isatty()
    except Exception:
        interactive = False
    if interactive and not force:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(here, "otp_watcher.log")

    logger = logging.getLogger("otp_watcher")
    logger.setLevel(logging.INFO)
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


_mutex_handle = None  # Windows: kept alive for the process lifetime
_lock_file = None     # macOS/POSIX: kept open to hold the flock


def ensure_single_instance():
    """Exit immediately if another copy of the watcher is already running.
    Prevents duplicate beepers no matter how many times it gets launched."""
    global _mutex_handle, _lock_file
    system = platform.system()
    if system == "Windows":
        ERROR_ALREADY_EXISTS = 183
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        _mutex_handle = k32.CreateMutexW(None, False, "OTP_Watcher_single_instance_v1")
        if k32.GetLastError() == ERROR_ALREADY_EXISTS:
            print("Another instance is already running; exiting.")
            sys.exit(0)
    else:
        # POSIX: non-blocking flock, auto-released by the OS on exit (no stale
        # lock). Keep the file object alive in a global.
        import fcntl
        here = os.path.dirname(os.path.abspath(__file__))
        _lock_file = open(os.path.join(here, "otp_watcher.lock"), "w")
        try:
            fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("Another instance is already running; exiting.")
            sys.exit(0)


def main():
    setup_logging()
    ensure_single_instance()
    creds = None
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    # Resolve paths next to the script so it works when launched from
    # Task Scheduler/launchd (CWD = System32 etc.)
    here = os.path.dirname(os.path.abspath(__file__))
    token_path = os.path.join(here, "token.json")
    creds_path = os.path.join(here, "credentials.json")

    if os.path.exists(token_path):
        # Delete token.json if you change SCOPES
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                creds_path, SCOPES)
            creds = flow.run_local_server(port=54461, open_browser=False)
        with open(token_path, "w") as token:
            token.write(creds.to_json())

    print("Started Gmail agent, monitoring")
    try:
        poll_for_new_emails(creds)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()