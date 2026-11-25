"""The user docs' notices in the announcement channel: the docs site publishes each notice
already rendered as a Slack message (notices.json next to the site, written by the docs'
scripts/notices.py export), and the bot posts each one once, on its date, through the
cluster's own Slack app. The docs repository holds no Slack credential.

    notices.json  {"notices": [{"id", "date" (YYYY-MM-DD), "text", "blocks"}]}

round_(fetch, post, seen_path, today, pin=None, update=None, pinned_path=None) posts every
notice due (its date today or before) that SEEN_PATH does not list, oldest first, and
records each one once Slack took it: its date, the message's ts (POST returns it) and a
digest of its text and blocks. PIN(ts) keeps it as the channel's one pinned notice
(pin_latest: the newest the bot posted, the previous one unpinned). A notice the site
rewords after it went out is edited in place: UPDATE(ts, text, blocks) once its digest
differs, so the post never keeps the old text. The first round (no SEEN_PATH yet) posts
nothing: what is already due counts as announced, so a new deploy never repeats the
notices people already saw; a notice dated later still goes out on its date.

A notice recorded before the ts was kept (its entry only a date) is not edited, but for the
newest one: its ts is the pinned notice's (PINNED_PATH), and it is brought up to date once.
"""

import hashlib
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


def digest(notice):
    """What a posted notice said: its text and blocks, hashed."""
    said = json.dumps([notice["text"], notice["blocks"]], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(said.encode()).hexdigest()


def _entries(seen, pinned_path):
    """SEEN with every entry a dict; an entry from before the ts was kept is {"date"}, and the
    newest of them takes the pinned notice's ts (the bot pinned each notice it posted), with
    no digest, so the round brings that post up to date once."""
    old = sorted((day, nid) for nid, day in seen.items() if isinstance(day, str))
    for day, nid in old:
        seen[nid] = {"date": day}
    ts = (state.read(pinned_path, {}) or {}).get("ts") if pinned_path and old else None
    if ts:
        seen[old[-1][1]]["ts"] = ts


def round_(fetch, post, seen_path, today, pin=None, update=None, pinned_path=None):
    notices = due(fetch(), today)
    first = state.read(seen_path, None) is None
    if first:
        with state.locked(seen_path, {}) as seen:
            seen.update({n["id"]: {"date": n["date"], "digest": digest(n)} for n in notices})
        return
    with state.locked(seen_path, {}) as seen:
        _entries(seen, pinned_path)
    for n in notices:  # each recorded as it goes: a later failure never reposts it
        posted = None
        with state.locked(seen_path, {}) as seen:
            entry = seen.get(n["id"])
            if entry is None:
                posted = post(n["text"], n["blocks"])
                seen[n["id"]] = {"date": n["date"], "ts": posted, "digest": digest(n)}
            elif update is not None and entry.get("ts") and entry.get("digest") != digest(n):
                update(entry["ts"], n["text"], n["blocks"])
                entry["digest"] = digest(n)
        if posted is not None and pin is not None:
            pin(posted)


def channel_id(slack, channel):
    """CHANNEL as an ID: a name (#...) is looked up; pins and edits take the ID."""
    return channel_ids(slack)[channel[1:]] if channel.startswith("#") else channel


def pin_latest(slack, channel, path, ts):
    """Pin the message TS in CHANNEL and unpin the one pinned before it (kept at PATH): the
    channel keeps one pinned notice, the newest. A previous pin someone already removed is
    no error. CHANNEL is a name (#...) or an ID."""
    channel = channel_id(slack, channel)
    with state.locked(path, {}) as pinned:
        slack.call("pins.add", channel=channel, timestamp=ts)
        old, pinned["ts"] = pinned.get("ts"), ts
        if old and old != ts:
            try:
                slack.call("pins.remove", channel=channel, timestamp=old)
            except Exception as e:
                if not any(code in str(e) for code in ("no_pin", "not_pinned", "message_not_found")):
                    raise
