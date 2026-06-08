# Gmail OTP Watcher

A small background service that extracts one-time passcodes from incoming email in Gmail and copies them to your clipboard instantenously — with a chime so you know it's ready to paste.

Code extraction is pure regex. The only network calls are to the Gmail API so no email content leaves your machine.

## Platform

Tested on MacOS Tahoe and Windows 11.

## Features

- **Distinct chime** — a short bell arpeggio (`otp_chime.wav`) plays on copy.
  Falls back to a system sound if the WAV is missing.
- **Idle-aware** — only copies and chimes when you've actually used the
  keyboard/mouse recently (default: within 60s). When you've stepped away
  (e.g. doing the verification on your phone), new codes are skipped silently
  so your computer doesn't make noise in another room. Tunable via
  `IDLE_LIMIT_SECONDS`.
- **Self-capping logs** — output goes to a rotating `otp_watcher.log`
  (1 MB × 4 files max, ~4 MB hard ceiling) so it can never fill the disk, even
  in a failure loop.

## Requirements

- Python 3.x
- Packages: `pyperclip`, `google-api-python-client`, `google-auth`,
  `google-auth-oauthlib`

```console
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Google API setup

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a
   project and enable the **Gmail API**. You can find the instructions [here](https://developers.google.com/workspace/gmail/api/quickstart/python).

2. Publish the app to **Production** (rather than leaving it in Testing), which prevents
   refresh tokens from expiring every 7 days. The option should be under _APIs & Services > OAuth Consent Screen > Audience_. If you created a client before doing this, you will have to delete and regenerate it.

3. Create an **OAuth client ID** of type _Desktop app_ and download the
   credentials as `credentials.json` into the project folder.

## First run (Auth)

Run once interactively so the Google sign-in can complete and write
`token.json` to the project folder:

```console
python main.py
```

This opens a browser for consent. After it prints `Started Gmail agent,
monitoring`, you're authorized; stop it with Ctrl+C. Subsequent runs refresh
the token headlessly.

> If you change the scope or change the publishing status, delete `token.json` and re-run this step to re-authorize.

## Run at startup

### Windows (Task Scheduler)

> NOTE: Run it once manually to authenticate before setting this up.

Use **Create Task**:

- **General:** "Run only when user is logged on"
- **Triggers:** At log on. Optionally delay 30s so networking is up.
- **Actions:** Start a program
  - Program/script: `pythonw.exe` (the windowless interpreter, beside your
    `python.exe`)
  - Add arguments: `"C:\path\to\main.py" --background`
  - Start in: `C:\path\to\project`
- **Conditions:** uncheck "Start only on AC power" if on a laptop.
- **Settings:** restart on failure (every 1 min); **uncheck** "Stop the task if
  it runs longer than 3 days"; "If already running → Do not start a new
  instance."

### macOS (launchd)

> NOTE: Run it once manually to authenticate before setting this up.

Create `~/Library/LaunchAgents/com.<you>.gmailotpwatcher.plist` and modify `your_name` and the paths:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.your_name.gmailotpwatcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/.venv/bin/python3</string>
        <string>/path/to/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/project</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

```console
launchctl load   ~/Library/LaunchAgents/com.your_name.gmailotpwatcher.plist # start + enable at login
launchctl list | grep gmailotpwatcher                                       # check (PID = running)
launchctl unload ~/Library/LaunchAgents/com.your_name.gmailotpwatcher.plist # stop + disable
```

## Credit

This is mostly vibe-coded with Claude Code Opus 4.8
