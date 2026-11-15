"""Playing the "code copied" chime."""
import os
import platform
import subprocess

from system.runtime import bundled_path


# Custom "OTP copied" chime, shipped with the app.
SOUND_FILE = bundled_path("otp_chime.wav")


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
