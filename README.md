# Gmail OTP Watcher

A small menu bar / tray app for macOS and Windows that watches your Gmail and,
when a sign-in code arrives, copies it to your clipboard and plays a chime so
you can paste it right away. It only does this when you've used your computer
in the last minute, so it stays quiet when you're away. Code extraction is pure
regex and runs on your machine, so no email content leaves it.

## Setup

You need Python 3 and a Google Cloud project of your own (it's free, and it's
what lets the app read your Gmail).

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
   **Desktop app** and download it. Leave the downloaded
   `client_secret_<something>.json` in your Downloads folder or put it in this
   project folder.

### 2. Install

```console
git clone https://github.com/IAmWinyl/Gmail-OTP-Watcher.git
cd Gmail-OTP-Watcher
python3 install.py
```

(On Windows, `py install.py`.)

This builds the app, sets it to start at every login, and launches it. On the
first launch your browser opens so you can let it read your Gmail. Google warns
that the app isn't verified, since it's your own project: choose **Advanced**,
then **Go to ... (unsafe)**. After that the icon turns green.

## Update

```console
git pull
python3 install.py
```

## Uninstall

```console
python3 install.py --uninstall
```

## Credit

This is mostly vibe-coded with Claude Code Opus 4.8.

Got the idea from https://github.com/mhaowork/verigood but didn't want the AI part.
