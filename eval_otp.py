"""Run the extractor over recent real mail and report what it pulled out.

Usage: python eval_otp.py [--days 30]

Covers trash + archive from the last N days plus everything under the OTP
labels. Raw emails are cached in .eval_cache/ (your actual mail, gitignored) so
reruns only download new messages. Each run is compared against the previous
one so the effect of an extractor change is visible at a glance.
"""
import argparse
import base64
import email
import json
import os
import shutil
import time

from googleapiclient.discovery import build

import main

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".eval_cache")
RAW = os.path.join(CACHE, "raw")
RESULTS = os.path.join(CACHE, "results.json")
PREV = os.path.join(CACHE, "results.prev.json")
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
LABELS = {"OTP": "otp", "OTP/False Positives": "false_positive"}


def list_ids(svc, **kwargs):
    ids, token = [], None
    while True:
        resp = svc.users().messages().list(
            userId="me", includeSpamTrash=True, maxResults=500,
            pageToken=token, **kwargs).execute(num_retries=7)
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token:
            return ids


def raw_email(svc, mid):
    path = os.path.join(RAW, mid + ".eml")
    if not os.path.exists(path):
        # The per-minute quota is shared with the live watcher, so pace the
        # first download and let the client back off on rate-limit errors.
        time.sleep(0.25)
        msg = svc.users().messages().get(
            userId="me", id=mid, format="raw").execute(num_retries=7)
        with open(path, "wb") as f:
            f.write(base64.urlsafe_b64decode(msg["raw"]))
    with open(path, "rb") as f:
        return f.read()


def context(subject, body, found):
    text = main.to_readable_text((subject or "") + "\n" + (body or ""), keep_urls=True)
    i = text.find(found)
    return text[max(0, i - 80):i + len(found) + 40] if i >= 0 else ""


def evaluate(days):
    os.makedirs(RAW, exist_ok=True)
    creds = main.get_credentials(os.path.join(HERE, "token.json"),
                                 os.path.join(HERE, "credentials.json"), SCOPES)
    svc = build("gmail", "v1", credentials=creds)

    tagged = {}
    labels = svc.users().labels().list(userId="me").execute(num_retries=7)["labels"]
    for lab in labels:
        if lab["name"] in LABELS:
            for mid in list_ids(svc, labelIds=[lab["id"]]):
                tagged.setdefault(mid, set()).add(LABELS[lab["name"]])
    window = f"newer_than:{days}d"
    ids = list_ids(svc, q=f"in:trash {window}")
    ids += list_ids(svc, q=f"-in:inbox -in:trash -in:spam -in:sent -in:drafts -in:chats {window}")
    ids = list(dict.fromkeys(ids + list(tagged)))

    results = []
    for n, mid in enumerate(ids, 1):
        if n % 50 == 0:
            print(f"  {n}/{len(ids)}")
        msg = email.message_from_bytes(raw_email(svc, mid))
        subject = main.decode_hdr(msg["subject"])
        body = main.get_body(msg)
        found = main.extract_code(subject, body)
        results.append({
            "id": mid,
            "date": msg["date"],
            "from": main.decode_hdr(msg["from"]),
            "subject": subject,
            "tags": sorted(tagged.get(mid, ())),
            "found": found,
            "context": context(subject, body, found) if found else "",
        })
    return results


def show(r):
    tags = f" [{', '.join(r['tags'])}]" if r["tags"] else ""
    print(f"- {r['from']} | {r['subject']}{tags}\n    found: {r['found']!r}")
    if r["context"]:
        print(f"    near:  ...{r['context']}...")


def report(results, prev):
    hits = [r for r in results if r["found"]]
    print(f"\n{len(results)} emails, {len(hits)} with something extracted\n")

    print("== Extracted")
    for r in hits:
        show(r)
    print("\n== Labeled OTP but nothing extracted")
    for r in results:
        if "otp" in r["tags"] and "false_positive" not in r["tags"] and not r["found"]:
            show(r)
    print("\n== Labeled false positive but something extracted")
    for r in results:
        if "false_positive" in r["tags"] and r["found"]:
            show(r)

    if prev is not None:
        before = {r["id"]: r["found"] for r in prev}
        print("\n== Changed since last run")
        for r in results:
            if r["id"] in before and before[r["id"]] != r["found"]:
                print(f"- {r['from']} | {r['subject']}\n    was: {before[r['id']]!r}  now: {r['found']!r}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    prev = None
    if os.path.exists(RESULTS):
        shutil.copy(RESULTS, PREV)
        with open(PREV) as f:
            prev = json.load(f)
    results = evaluate(args.days)
    with open(RESULTS, "w") as f:
        json.dump(results, f, indent=1)
    report(results, prev)
