"""How long since you last used the keyboard or mouse."""
import ctypes
import platform
import re
import subprocess


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
