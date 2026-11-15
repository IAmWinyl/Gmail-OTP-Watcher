"""Native dialogs: failure alerts, yes/no questions and the file picker."""
import ctypes
import json
import platform
import re
import subprocess
import traceback

from system.runtime import NO_WINDOW


if platform.system() == "Windows":
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
                 f"display alert {_as_str(title)} message {_as_str(body)}"],
                timeout=3600)
    except Exception as e:
        print(f"(notify failed: {e})")


def _as_str(text):
    """An AppleScript string literal. json.dumps escapes quotes the same way,
    but AppleScript can't read its \\uXXXX escapes, so keep non-ASCII as is."""
    return json.dumps(text, ensure_ascii=False)



def ask(title, message, yes="OK", no=None):
    """Blocking question dialog. True if the yes button was pressed. With no
    second button it's just an acknowledgement. (Windows message boxes can't
    relabel their buttons, so there it's Yes/No or OK.)"""
    try:
        if platform.system() == "Windows":
            MB_OK, MB_YESNO, MB_ICONINFO, MB_SYSTEMMODAL, IDOK, IDYES = 0, 4, 0x40, 0x1000, 1, 6
            style = (MB_YESNO if no else MB_OK) | MB_ICONINFO | MB_SYSTEMMODAL
            return ctypes.windll.user32.MessageBoxW(None, message, title, style) in (IDOK, IDYES)
        if platform.system() == "Darwin":
            buttons = [no, yes] if no else [yes]
            script = (f"display dialog {_as_str(message)} with title {_as_str(title)} "
                      f"buttons {{{', '.join(_as_str(b) for b in buttons)}}} "
                      f"default button {_as_str(yes)}")
            out = subprocess.run(["osascript", "-e", script],
                                 capture_output=True, text=True, timeout=3600)
            return out.returncode == 0 and f"button returned:{yes}" in out.stdout
    except Exception as e:
        print(f"(dialog failed: {e})")
    return False


def choose_file(prompt):
    """Native open-file dialog for a .json file. The chosen path, or None."""
    try:
        if platform.system() == "Darwin":
            script = f"POSIX path of (choose file with prompt {_as_str(prompt)} of type {{\"public.json\"}})"
            out = subprocess.run(["osascript", "-e", script],
                                 capture_output=True, text=True, timeout=3600)
        elif platform.system() == "Windows":
            ps = ("Add-Type -AssemblyName System.Windows.Forms; "
                  "$d = New-Object System.Windows.Forms.OpenFileDialog; "
                  f"$d.Title = '{prompt.replace(chr(39), chr(39) * 2)}'; "
                  "$d.Filter = 'Google client file (*.json)|*.json'; "
                  "if ($d.ShowDialog() -eq 'OK') { $d.FileName }")
            out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                                 capture_output=True, text=True, timeout=3600,
                                 creationflags=NO_WINDOW)
        else:
            return None
        return out.stdout.strip() or None
    except Exception as e:
        print(f"(file picker failed: {e})")
        return None


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
