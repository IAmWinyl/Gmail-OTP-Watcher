"""Watching Gmail: sign in, follow new inbox mail, and copy each sign-in code
to the clipboard with a chime while you're at the computer."""
import base64
import email
import os
import time
import traceback

import pyperclip
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from extract import decode_hdr, extract_code, get_bodies
from system.dialogs import describe_failure, notify
from system.idle import idle_seconds
from system.logs import log
from system.runtime import FROZEN, app_dir
from system.sound import beep


# History/labels we never want to act on (your own outgoing/test mail).
# Only act on mail that actually landed in the inbox. This correctly handles
# self-sent test emails (which carry SENT *and* INBOX) and ignores the
# draft/trash intermediates Gmail creates while composing/forwarding.
REQUIRE_LABEL = "INBOX"

# Only copy/beep when you've used the keyboard/mouse within this many seconds.
# When you've been away longer (e.g. doing the verification on your phone), new
# codes are silently skipped. Raise this if it ever skips a code you wanted.
IDLE_LIMIT_SECONDS = 60


# Exit codes: 0 when the watcher stopped on purpose or needs you, 1 on a crash.
EXIT_NEEDS_HUMAN = 0
EXIT_CRASH = 1


def process_email(subject, bodies):
    """Copy the code and beep. Returns True if a code was found/copied."""
    code = extract_code(subject, bodies)
    if not code:
        log.debug("No validation code found in the email")
        return False
    pyperclip.copy(code)
    log.info("Copied a code to the clipboard")
    log.debug(f"Copied {code}")
    beep()
    return True


def fetch_email(email_id, creds):
    service = build("gmail", "v1", credentials=creds)
    msg = service.users().messages().get(
        userId="me", id=email_id, format="raw").execute()
    try:
        mime_msg = email.message_from_bytes(base64.urlsafe_b64decode(msg["raw"]))
        subject = decode_hdr(mime_msg["subject"])
        from_name = decode_hdr(mime_msg["from"])
        bodies = get_bodies(mime_msg)
        log.debug(f"From: {from_name} | Subject: {subject} | "
                  f"Body lengths: {[len(b) for b in bodies]}")
    except Exception as e:
        print(f"An error occurred parsing email: {e}")
        return
    process_email(subject, bodies)


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
                    log.debug(f"detected {mid} labels={sorted(labels)}")
                    if REQUIRE_LABEL not in labels:
                        continue  # not in inbox (draft/trash/etc.)
                    idle = idle_seconds()
                    if idle > IDLE_LIMIT_SECONDS:
                        # Away from the computer: skip silently. History still
                        # advances below, so we won't replay this on return.
                        log.debug(f"skipping {mid} (idle {int(idle)}s)")
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


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def paths():
    """(token.json, credentials.json) in app_dir(), not the CWD, which is
    System32 or / when launched at login."""
    here = app_dir()
    return os.path.join(here, "token.json"), os.path.join(here, "credentials.json")


def sign_in(creds_path, token_path, open_browser=False, success_message=None):
    """Run Google's browser consent flow and save the resulting token. Without
    open_browser it prints the link instead (a terminal run)."""
    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
    kwargs = {"success_message": success_message} if success_message else {}
    creds = flow.run_local_server(port=54461, open_browser=open_browser, **kwargs)
    with open(token_path, "w") as token:
        token.write(creds.to_json())
    return creds


def get_credentials(token_path, creds_path):
    """Load/refresh credentials, or sign in from the terminal on a source run
    with no token yet. Waits out transient network failures (e.g. DNS not up
    yet right after boot) instead of crashing. Permanent failures
    (invalid_scope / invalid_grant) raise RefreshError."""
    creds = None
    if os.path.exists(token_path):
        # NOTE: delete token.json and regenerate it if you change SCOPES
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        with_network_retry(lambda: creds.refresh(Request()), "Token refresh")
        with open(token_path, "w") as token:
            token.write(creds.to_json())
        return creds
    return sign_in(creds_path, token_path)


def run_watcher(on_state=None):
    """Run the watcher to completion. Reports lifecycle changes through
    on_state(state, detail) so a UI can show them. Returns an exit code."""
    def state(name, detail="", failure=None):
        if on_state:
            on_state(name, detail, failure)

    token_path, creds_path = paths()
    if FROZEN:
        # The setup window handles signing in, and it opens on the next launch
        # whenever there's no saved sign-in.
        sign_in_again = "Quit OTP Watcher from its menu and open it again to sign back in."
    else:
        sign_in_again = "Run src/main.py again to sign back in."

    try:
        state("starting", "Authorizing with Gmail...")
        if not os.path.exists(token_path) and (FROZEN or not os.path.exists(creds_path)):
            if FROZEN:
                failure = ("OTP Watcher isn't signed in to Gmail",
                           "Quit OTP Watcher from its menu and open it again to "
                           "set it up.", "")
            else:
                failure = ("OTP Watcher can't sign in: credentials.json is missing",
                           "Gmail access needs the OAuth client file from your own "
                           "Google Cloud project, and it isn't there.\n\n"
                           f"Put it at:\n{creds_path}\n\n"
                           "The README's Google API setup section says how to get it. "
                           "Then restart the watcher.", "")
            state("failed", "not signed in", failure)
            notify(*failure)
            return EXIT_NEEDS_HUMAN
        creds = get_credentials(token_path, creds_path)
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
        # The saved sign-in is permanently rejected, so drop it: the next
        # launch then signs in fresh instead of failing the same way.
        try:
            os.remove(token_path)
        except OSError:
            pass
        reason, details = describe_failure(e)
        failure = ("OTP Watcher stopped: Gmail sign-in expired",
                   "Gmail stopped accepting the saved sign-in, so OTP codes are NOT "
                   f"being copied.\n\nReason: {reason}\n\n{sign_in_again}",
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
