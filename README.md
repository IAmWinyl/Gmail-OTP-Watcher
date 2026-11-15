# Gmail OTP Watcher

A small menu bar / tray app for macOS and Windows that watches your Gmail and,
when a sign-in code arrives, copies it to your clipboard and plays a chime so
you can paste it right away. It only does this when you've used your computer
in the last minute, so it stays quiet when you're away. Code extraction is pure
regex and runs on your machine, so no email content leaves it.

## Setup

You need a Google Cloud project of your own (it's free, and it's what lets the
app read your Gmail).

### 1. Google API setup

Google's [Gmail API Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python)
walks through these steps with screenshots.

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a
   project and enable the **Gmail API**.
2. Set up the OAuth consent screen (_APIs & Services > OAuth consent screen_)
   with audience **External**, then **Publish** it to Production. Left in
   Testing, Google expires your sign-in every 7 days. If you created a client
   before publishing, delete it and make a new one.
3. Under _APIs & Services > Credentials_, create an **OAuth client ID** of type
   **Desktop app** and download it (`client_secret_<something>.json`).

### 2. Install

**Download it** from the [latest release](https://github.com/IAmWinyl/Gmail-OTP-Watcher/releases/latest):

- macOS: `OTP-Watcher-macos-arm64.zip` (Apple Silicon) or
  `OTP-Watcher-macos-x86_64.zip` (Intel). Unzip it, move `OTP Watcher.app` to
  Applications and open it. It isn't signed, so the first time macOS blocks it:
  go to _System Settings > Privacy & Security_ and choose **Open Anyway**.
- Windows: `OTP-Watcher-windows.exe`. Put it in a folder of its own (it keeps
  its sign-in next to itself) and run it. If SmartScreen stops it, choose
  **More info > Run anyway**.

**Or build it yourself** (needs Python 3). Put the `client_secret_*.json` in
the project folder first and it'll skip asking for it:

```console
git clone https://github.com/IAmWinyl/Gmail-OTP-Watcher.git
cd Gmail-OTP-Watcher
python3 install.py
```

(On Windows, `py install.py`.)

On first launch it asks you to choose the `client_secret_*.json` you
downloaded, then your browser opens so you can let it read your Gmail. Google
warns that the app isn't verified, since it's your own project: choose
**Advanced**, then **Go to ... (unsafe)**. After that the icon turns green, and
it starts at every login.

## Update

When a new version is out, the app asks if you want to update, and the menu
shows **Update available**. If you built it yourself, update with:

```console
git pull
python3 install.py
```

## Uninstall

If you built it yourself:

```console
python3 install.py --uninstall
```

## Credit

This is vibe-coded with Claude Code.
Got the idea from https://github.com/mhaowork/verigood but didn't want the AI part.
