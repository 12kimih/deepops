"""The user docs' notices in the announcement channel: the docs site publishes each notice
already rendered as a Slack message (notices.json next to the site, written by the docs'
scripts/notices.py export), and the bot posts each one once, on its date, through the
cluster's own Slack app. The docs repository holds no Slack credential.

    notices.json  {"notices": [{"id", "date" (YYYY-MM-DD), "text", "blocks"}]}

round_(fetch, post, seen_path, today, pin=None) posts every notice due (its date today or
before) that SEEN_PATH does not list, oldest first, and records each one once Slack took
it; POST returns the message's ts and PIN(ts) keeps it as the channel's one pinned notice
(pin_latest: the newest the bot posted, the previous one unpinned). The
first round (no SEEN_PATH yet) posts nothing: what is already due counts as announced,
so a new deploy never repeats the notices people already saw; a notice dated later still
goes out on its date.
"""

import json
import urllib.request

from . import state
from .channels import channel_ids

FETCH_TIMEOUT = 30


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
        return json.load(resp)


def due(document, today):
    """The notices of DOCUMENT dated TODAY (YYYY-MM-DD) or before, oldest first; one
    without an id, date, text or blocks is left out."""
    notices = [
        n
        for n in (document or {}).get("notices", [])
        if isinstance(n, dict) and all(n.get(k) for k in ("id", "date", "text", "blocks"))
    ]
    return sorted((n for n in notices if n["date"] <= today), key=lambda n: (n["date"], n["id"]))


def round_(fetch, post, seen_path, today, pin=None):
    notices = due(fetch(), today)
    first = state.read(seen_path, None) is None
    if first:
        with state.locked(seen_path, {}) as seen:
            seen.update({n["id"]: n["date"] for n in notices})
        return
    for n in notices:  # each recorded as it goes: a later failure never reposts it
        with state.locked(seen_path, {}) as seen:
            if n["id"] in seen:
                continue
            ts = post(n["text"], n["blocks"])
            seen[n["id"]] = n["date"]
        if pin is not None:
            pin(ts)


def pin_latest(slack, channel, path, ts):
    """Pin the message TS in CHANNEL and unpin the one pinned before it (kept at PATH): the
    channel keeps one pinned notice, the newest. A previous pin someone already removed is
    no error. CHANNEL is a name (#...) or an ID; pins take the ID."""
    if channel.startswith("#"):
        channel = channel_ids(slack)[channel[1:]]
    with state.locked(path, {}) as pinned:
        slack.call("pins.add", channel=channel, timestamp=ts)
        old, pinned["ts"] = pinned.get("ts"), ts
        if old and old != ts:
            try:
                slack.call("pins.remove", channel=channel, timestamp=old)
            except Exception as e:
                if not any(code in str(e) for code in ("no_pin", "not_pinned", "message_not_found")):
                    raise
