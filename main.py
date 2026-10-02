import base64
import os.path
import sys
import time
import re
import json
import traceback
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

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# History/labels we never want to act on (your own outgoing/test mail).
# Only act on mail that actually landed in the inbox. This correctly handles
# self-sent test emails (which carry SENT *and* INBOX) and ignores the
# draft/trash intermediates Gmail creates while composing/forwarding.
REQUIRE_LABEL = "INBOX"

# Only copy/beep when you've used the keyboard/mouse within this many seconds.
# When you've been away longer (e.g. doing the verification on your phone), new
# codes are silently skipped. Raise this if it ever skips a code you wanted.
IDLE_LIMIT_SECONDS = 60

BACKGROUND = "--background" in sys.argv
# Tray mode puts a status icon in the notification area so a dead
# watcher is visible at a glance rather than silently absent.
TRAY = "--tray" in sys.argv

# Exit codes. On macOS, launchd's KeepAlive {SuccessfulExit: false} restarts
# only on a nonzero exit, so 0 means "stopped on purpose, needs a human".
EXIT_NEEDS_HUMAN = 0
EXIT_CRASH = 1


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
        # 32-bit unsigned subtraction handles the ~49.7-day GetTickCount
        # wraparound, which matters since this machine stays on continuously.
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


class _TASKDIALOGCONFIG(ctypes.Structure):
    """TASKDIALOGCONFIG from commctrl.h (byte-packed via pshpack1.h)."""
    _pack_ = 1
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwndParent", ctypes.c_void_p),
        ("hInstance", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
        ("dwCommonButtons", ctypes.c_uint),
        ("pszWindowTitle", ctypes.c_wchar_p),
        ("pszMainIcon", ctypes.c_void_p),
        ("pszMainInstruction", ctypes.c_wchar_p),
        ("pszContent", ctypes.c_wchar_p),
        ("cButtons", ctypes.c_uint),
        ("pButtons", ctypes.c_void_p),
        ("nDefaultButton", ctypes.c_int),
        ("cRadioButtons", ctypes.c_uint),
        ("pRadioButtons", ctypes.c_void_p),
        ("nDefaultRadioButton", ctypes.c_int),
        ("pszVerificationText", ctypes.c_wchar_p),
        ("pszExpandedInformation", ctypes.c_wchar_p),
        ("pszExpandedControlText", ctypes.c_wchar_p),
        ("pszCollapsedControlText", ctypes.c_wchar_p),
        ("pszFooterIcon", ctypes.c_void_p),
        ("pszFooter", ctypes.c_wchar_p),
        ("pfCallback", ctypes.c_void_p),
        ("lpCallbackData", ctypes.c_ssize_t),
        ("cxWidth", ctypes.c_uint),
    ]


_TD_CALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
    ctypes.c_size_t, ctypes.c_ssize_t, ctypes.c_ssize_t)


def _td_on_created(hwnd, msg, wparam, lparam, refdata):
    """TDN_CREATED: force the dialog topmost so it can't open behind windows."""
    if msg == 0:
        HWND_TOPMOST, SWP_NOSIZE_NOMOVE = -1, 0x0003
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE_NOMOVE)
    return 0


_td_callback = _TD_CALLBACK(_td_on_created)


def _task_dialog(heading, message, details):
    """Windows task dialog: big heading, explanation, collapsible details.
    Returns False if unavailable so the caller can fall back."""
    TDF_ALLOW_DIALOG_CANCELLATION = 0x0008
    TDF_EXPAND_FOOTER_AREA = 0x0040
    TDF_SIZE_TO_CONTENT = 0x01000000
    TDCBF_OK_BUTTON = 0x0001
    TD_ERROR_ICON = 65534  # MAKEINTRESOURCE(-2)

    cfg = _TASKDIALOGCONFIG()
    cfg.cbSize = ctypes.sizeof(cfg)
    cfg.dwFlags = (TDF_ALLOW_DIALOG_CANCELLATION | TDF_SIZE_TO_CONTENT
                   | (TDF_EXPAND_FOOTER_AREA if details else 0))
    cfg.dwCommonButtons = TDCBF_OK_BUTTON
    cfg.pszWindowTitle = "OTP Watcher"
    cfg.pszMainIcon = TD_ERROR_ICON
    cfg.pszMainInstruction = heading
    cfg.pszContent = message
    if details:
        cfg.pszExpandedInformation = details
        cfg.pszCollapsedControlText = "Show technical details"
        cfg.pszExpandedControlText = "Hide technical details"
    cfg.pfCallback = ctypes.cast(_td_callback, ctypes.c_void_p)

    pressed = ctypes.c_int()
    hr = ctypes.windll.comctl32.TaskDialogIndirect(
        ctypes.byref(cfg), ctypes.byref(pressed), None, None)
    return hr == 0


def notify(title, message, details=""):
    """Visible alert for fatal problems, so a dead watcher is never silent.
    Blocking dialog rather than a toast: toasts get swallowed by Focus/DND."""
    print(f"[NOTIFY] {title}: {message}")
    if details:
        print(f"[NOTIFY] details:\n{details}")
    try:
        system = platform.system()
        if system == "Windows":
            if _task_dialog(title, message, details):
                return
            # Pre-Vista comctl32 or a malformed dialog: plain message box.
            body = message + (f"\n\n{details}" if details else "")
            MB_ICONWARNING, MB_SYSTEMMODAL = 0x30, 0x1000
            ctypes.windll.user32.MessageBoxW(
                None, body, title, MB_ICONWARNING | MB_SYSTEMMODAL)
        elif system == "Darwin":
            body = message + (f"\n\n{details}" if details else "")
            subprocess.run(
                ["osascript", "-e",
                 f"display alert {json.dumps(title)} message {json.dumps(body)}"],
                timeout=3600)
    except Exception as e:
        print(f"(notify failed: {e})")


def describe_failure(exc):
    """Turn an exception into (one-line reason, full detail text) for display."""
    reason = f"{type(exc).__name__}: {exc}".strip()
    reason = re.sub(r"\s+", " ", reason)
    if len(reason) > 300:
        reason = reason[:297] + "..."
    details = traceback.format_exc()
    if len(details) > 4000:  # keep the dialog a sane size; log has the rest
        details = "...\n" + details[-4000:]
    return reason, details


def decode_hdr(value):
    """Decode an RFC2047-encoded header (e.g. =?UTF-8?B?...?=) to plain text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def to_readable_text(s, keep_urls=False):
    """Reduce a string to the plain text a human would read, so rendering
    artifacts (URLs, CRLF/tab padding, &nbsp;, full-width digits, ...) can't
    inflate the keyword-to-code distance the matcher scores on.

    keep_urls leaves links in place for the magic-link scan, which needs to
    measure distance to the URL itself."""
    if not s:
        return ""
    # Drop URLs: tracking links are full of arbitrary numbers (e.g. ?at=1000lwu3)
    # sitting next to words like 'verify' that would otherwise beat the real code.
    if not keep_urls:
        s = re.sub(r"https?://\S+", " ", s)
    # Normalize to NFKC
    s = unicodedata.normalize("NFKC", s)
    # Drop zero-width and other format/control chars
    s = "".join(
        ch for ch in s
        if ch in "\t\n\r" or not unicodedata.category(ch).startswith("C")
    )
    # Collapse whitespace runs. HTML tag-stripping leaves table/layout markup
    # as long stretches of newlines and &nbsp;, which can push a code 40+ chars
    # from its keyword even when they're visually adjacent.
    return re.sub(r"\s+", " ", s).strip()


# A verification signal has to sit near the digits for them to count as a code.
# Bare words like "code"/"pin" are NOT enough on their own (they match "Internal
# Revenue Code", "promo code", "zip code"). They only count in verification
# phrasings: "verification code", "your code", "code:", "code is", "PIN is".
KW = re.compile(
    r"verification|verify|passcode|one[\s-]?time|\botp\b|authenticat|2fa"
    r"|(?:your|this|the|enter|following|security|login|access|confirmation|sign[\s-]?in)\s+(?:code|pin)"
    r"|(?:code|pin)\s*[:=]"
    r"|(?:code|pin)\s+(?:is|are|was|below)\b",
    re.IGNORECASE,
)
WINDOW = 50  # chars between keyword and code

_MONTHS = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"

# Dates and clock times are full of 4-digit runs, and security emails print
# them right beside words like "authenticated" ("Authenticated August 24, 2026
# at 2:02 PM"). Digits inside one of these are never the code.
DATE_TIME = re.compile(
    r"\b(?:" + _MONTHS + r")[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*(?:\d{4})?"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:" + _MONTHS + r")[a-z]*\.?\s*,?\s*(?:\d{4})?"
    r"|\b\d{1,2}[:.]\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?"
    r"|\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b",
    re.IGNORECASE,
)


def keyword_distance(text, start, end, window=WINDOW):
    """Chars from [start:end) to the nearest verification keyword on either
    side, or None if there isn't one within the window."""
    before = text[max(0, start - window):start]
    after = text[end:end + window]
    dist = None
    for km in KW.finditer(before):
        d = len(before) - km.end()
        dist = d if dist is None else min(dist, d)
    for km in KW.finditer(after):
        d = km.start()
        dist = d if dist is None else min(dist, d)
    return dist


def extract_code(subject, body):
    """Pull a verification code or magic link out of an email using regex.

    Strategy: find every standalone 4-8 digit run, score each by how close it
    sits to a verification keyword on EITHER side, and return the closest one.
    This avoids grabbing reference numbers/dates/amounts, and handles subjects
    where the number comes before the word 'code'. Falls back to a magic link.
    """
    text = (subject or "") + "\n" + (body or "")

    # Canonicalize so distance reflects words, not markup
    clean_text = to_readable_text(text)

    # Spans to ignore: digits that are part of a date or a clock time
    date_spans = [(m.start(), m.end()) for m in DATE_TIME.finditer(clean_text)]

    best = None
    for m in re.finditer(r"(?<!\d)(\d{4,8})(?!\d)", clean_text):
        s, e = m.start(), m.end()
        if any(ds < e and s < de for ds, de in date_spans):
            continue  # inside a date/time
        dist = keyword_distance(clean_text, s, e)
        if dist is None:
            continue

        # Closest keyword wins; tie-break toward 6-digit codes, then position
        digits = m.group(1)
        score = (dist, 0 if len(digits) == 6 else 1, s)
        if best is None or score < best[0]:
            best = (score, digits)
    if best:
        return best[1]

    # Alphanumeric codes (Steam sends "Login Code 6PVGY"). Runs only after the
    # digit scan comes up empty, and only for tokens mixing letters AND digits,
    # so shouty words like STEAM or LOGIN can never qualify.
    best = None
    for m in re.finditer(r"(?<![A-Za-z0-9])([A-Z0-9]{4,8})(?![A-Za-z0-9])", clean_text):
        token = m.group(1)
        if not (re.search(r"[A-Z]", token) and re.search(r"\d", token)):
            continue
        s, e = m.start(), m.end()
        if any(ds < e and s < de for ds, de in date_spans):
            continue
        dist = keyword_distance(clean_text, s, e)
        if dist is None:
            continue
        score = (dist, s)
        if best is None or score < best[0]:
            best = (score, token)
    if best:
        return best[1]

    # Magic-link fallback: a genuine sign-in link, not an unsubscribe/preferences
    # URL (those contain "login"/"email" and caused false positives). The keyword
    # has to be next to the link too: Amazon's "...RefundConfirmation..." order
    # links sit paragraphs away from an incidental "verification of the item(s)".
    link_text = to_readable_text(text, keep_urls=True)
    for lm in re.finditer(r"https?://\S+", link_text):
        u = lm.group()
        if not re.search(r"verify|magic|token|otp|confirm|sign[-_]?in", u, re.IGNORECASE):
            continue
        if re.search(r"unsubscrib|preferenc|revoke|optout|opt-out|manage|email|disavow|wasnt|not[-_]?you|report",
                     u, re.IGNORECASE):
            continue
        if keyword_distance(link_text, lm.start(), lm.end()) is None:
            continue
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
            import winsound  # stdlib, Windows only
            if have_file:
                winsound.PlaySound(SOUND_FILE,
                                   winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.MessageBeep()
        elif system == "Darwin":  # MacOS
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

    Prefers text/plain, but only if it actually has content -- senders
    sometimes include an EMPTY text/plain part alongside the real HTML, and
    naively preferring it yields an empty body. Falls back to text/html with
    tags and style/script blocks stripped and entities unescaped.
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
            if not txt.strip():
                continue  # blank part: ignore so it can't shadow a real one
            (plain_parts if ctype == "text/plain" else html_parts).append(txt)
    else:
        payload = mime_msg.get_payload(decode=True)
        if payload is not None:
            charset = mime_msg.get_content_charset() or "utf-8"
            txt = payload.decode(charset, errors="replace")
            if txt.strip():
                if mime_msg.get_content_type() == "text/html":
                    html_parts.append(txt)
                else:
                    plain_parts.append(txt)

    plain = "\n".join(plain_parts).strip()
    if plain:
        return plain
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


def with_network_retry(fn, what):
    """Run fn(), retrying transient network failures with capped backoff.
    Permanent auth failures (RefreshError) propagate immediately."""
    delay = 5
    while True:
        try:
            return fn()
        except RefreshError:
            raise
        except (TransportError, OSError, HttpError) as e:
            if isinstance(e, HttpError) and e.resp.status < 500 and e.resp.status != 429:
                raise  # 4xx other than rate limit: not transient
            print(f"{what} failed (network?), retrying in {delay}s: {e}")
            time.sleep(delay)
            delay = min(delay * 2, 60)


def poll_for_new_emails(creds):
    service = build("gmail", "v1", credentials=creds)
    user_id = "me"
    start_history_id = with_network_retry(
        lambda: service.users().getProfile(userId=user_id).execute()["historyId"],
        "Initial profile fetch")

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
            # resp is the last page; its historyId is the newest checkpoint.
            start_history_id = resp["historyId"]

            seen = set()  # avoid double-processing within one batch
            for change in changes:
                for added in change.get("messagesAdded", []):
                    msg = added["message"]
                    mid = msg["id"]
                    if mid in seen:
                        continue
                    seen.add(mid)
                    labels = set(msg.get("labelIds", []))
                    print(f"detected {mid} labels={sorted(labels)}")
                    if REQUIRE_LABEL not in labels:
                        continue  # not in inbox (draft/trash/etc.)
                    idle = idle_seconds()
                    if idle > IDLE_LIMIT_SECONDS:
                        # Away from the computer: skip silently. History still
                        # advances below, so we won't replay this on return.
                        print(f"skipping {mid} (idle {int(idle)}s)")
                        continue
                    fetch_email(mid, creds)

            time.sleep(0.8)

        except RefreshError:
            raise  # permanent auth failure: let main() notify and stop
        except HttpError as error:
            if error.resp.status == 404:
                # startHistoryId expired/too old: resync to current.
                start_history_id = with_network_retry(
                    lambda: service.users().getProfile(userId=user_id).execute()["historyId"],
                    "History resync")
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
    logfile next to the script so it can never fill the disk (~4 MB max total).
    The launcher passes --background; we also fall back to detecting a missing
    console. Manual console runs are left untouched so you see live output."""
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


def get_credentials(token_path, creds_path, scopes):
    """Load/refresh credentials. Waits out transient network failures (e.g.
    DNS not up yet right after boot) instead of crashing. Permanent failures
    (invalid_scope / invalid_grant) raise RefreshError."""
    creds = None
    if os.path.exists(token_path):
        # NOTE: delete token.json and regenerate it if you change SCOPES
        creds = Credentials.from_authorized_user_file(token_path, scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        with_network_retry(lambda: creds.refresh(Request()), "Token refresh")
    else:
        if BACKGROUND:
            # Can't open a browser from a background launch: fail loudly
            # instead of hanging forever on the consent flow.
            raise RefreshError("No usable token.json.")
        flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
        creds = flow.run_local_server(port=54461, open_browser=False)
    with open(token_path, "w") as token:
        token.write(creds.to_json())
    return creds



def run_watcher(on_state=None):
    """Run the watcher to completion. Reports lifecycle changes through
    on_state(state, detail) so a UI can show them. Returns an exit code."""
    def state(name, detail="", failure=None):
        if on_state:
            on_state(name, detail, failure)

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    # Resolve paths next to the script so it works when launched from
    # Task Scheduler/launchd (CWD = System32 etc.)
    here = os.path.dirname(os.path.abspath(__file__))
    token_path = os.path.join(here, "token.json")
    creds_path = os.path.join(here, "credentials.json")

    try:
        state("starting", "Authorizing with Gmail...")
        creds = get_credentials(token_path, creds_path, SCOPES)
        print("Started Gmail agent, monitoring")
        state("running", "Watching for OTP emails.")
        poll_for_new_emails(creds)
        return EXIT_NEEDS_HUMAN
    except KeyboardInterrupt:
        print("\nStopped.")
        state("stopped", "Stopped by hand.")
        return EXIT_NEEDS_HUMAN
    except RefreshError as e:
        print(f"Auth failure: {e}")
        reason, details = describe_failure(e)
        failure = ("OTP Watcher stopped: Gmail sign-in expired",
                   "Gmail authorization failed, so OTP codes are NOT being copied.\n\n"
                   f"Reason: {reason}\n\n"
                   "Fix: delete token.json, run main.py once manually to sign in, "
                   "then start the watcher again.",
                   details)
        state("failed", reason, failure)
        notify(*failure)
        return EXIT_NEEDS_HUMAN
    except Exception as e:
        traceback.print_exc()
        reason, details = describe_failure(e)
        failure = ("OTP Watcher crashed",
                   "The watcher stopped unexpectedly, so OTP codes are NOT being "
                   f"copied.\n\nReason: {reason}\n\n"
                   "It will not restart on its own. Use the Restart watcher item in "
                   "the tray menu once the cause is dealt with.",
                   details)
        state("failed", reason, failure)
        notify(*failure)
        return EXIT_CRASH


def main():
    setup_logging()
    ensure_single_instance()
    if TRAY:
        import tray
        sys.exit(tray.run(run_watcher, notify))
    sys.exit(run_watcher())


if __name__ == "__main__":
    main()
