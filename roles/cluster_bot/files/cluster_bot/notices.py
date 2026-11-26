"""The user docs' notices in the announcement channel: the docs site publishes each notice
(notices.json next to the site, written by the docs' scripts/notices.py export), and the
bot posts each one once, on its date, through the cluster's own Slack app. The docs
repository holds no Slack credential.

    notices.json  {"notices": [{"id", "date" (YYYY-MM-DD), "text" (the fallback),
                                "title", "markdown", "link": {"text", "url"}, "context"}]}

The notice is Markdown, rendered here by cluster_tools.rich, the one renderer of Slack
text: render(notice) is the message, a header (TITLE), the lead (MARKDOWN), a button to
the page (LINK) and a line of small print (CONTEXT, Markdown too). check(document) says
what of a notices.json the bot could not show as written; the docs run it on every
commit (scripts/deepops/notice_render.py, the notice-render hook). A notice published
before the docs sent Markdown carries its own "blocks", posted as they are.

The channel is kept like notices.json, every round (round_, plan): each notice due (its
date today or before) has one post, the newest pinned, and each post shows the notice
as today's code renders it. A notice reworded on the site, or rendered differently by a
newer rich, edits its post in place, once (the digest of the rendered post); a notice the
site dropped has its post deleted; a second post of one notice goes too. Each post
carries Slack message metadata (event_type cluster_notice, the notice's id), so the
round finds its posts exactly; SEEN_PATH records each one's ts and digest as it goes.

A post from before metadata (an older bot or its webhook) is found by its button to the
notice's page, or by its title. The bot's own rounds leave those as they are; the
owner's run changes them after reading what it would do:

    python -m cluster_bot.notices CONFIG --dry-run    say each update, delete, post, pin
    python -m cluster_bot.notices CONFIG              do it, then those posts carry metadata

The first round (no SEEN_PATH yet) posts nothing: what is already due counts as
announced, so a new deploy never repeats the notices people already saw. A notice whose
post someone deleted by hand is not posted again. A post of another app or a person is
never touched, only said.
"""

import argparse
import fcntl
import hashlib
import json
import os
import sys
import urllib.request
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from cluster_tools import rich  # the cluster's library (PYTHONPATH, roles/cluster_bot)
from cluster_tools.slack import (
    Error,
    Slack,
    channel_id,
    delete,
    history,
    is_own,
    pin_message,
    replies,
    unpin_message,
)
from cluster_tools.slack import own as own_ids

from . import state

FETCH_TIMEOUT = 30


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
        return json.load(resp)


HEADER_MAX = 150  # characters Slack shows in a header
MAX_BLOCKS = 50  # blocks in one message
EVENT = "cluster_notice"  # the metadata event_type of a notice post


def render(notice):
    """NOTICE as the message the bot posts: {"text", "blocks"}. A notice in Markdown is
    rendered here; one with only its own blocks (before the docs sent Markdown) keeps them."""
    if "markdown" not in notice:
        return {"text": notice["text"], "blocks": notice["blocks"]}
    title = notice.get("title") or notice["text"]
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}}]
    blocks += rich.blocks(notice["markdown"])
    link = notice.get("link") or {}
    if link.get("url"):
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": link.get("text") or link["url"]},
             "url": link["url"]}]})  # fmt: skip
    if notice.get("context"):
        blocks.append(rich.context(notice["context"]))
    return {"text": rich.plain(notice["text"]) or title, "blocks": blocks}


def check(document):
    """What of DOCUMENT (a notices.json) the bot could not post as written: ["what"], empty
    when every notice renders within Slack's limits and shows no marker left as typed."""
    notices = (document or {}).get("notices") if isinstance(document, dict) else None
    if not isinstance(notices, list):
        return ['notices.json: no "notices" list']
    out = []
    for i, n in enumerate(notices):
        where = f"notice {n.get('id', i) if isinstance(n, dict) else i}"
        if not isinstance(n, dict) or not _complete(n):
            out.append(f"{where}: needs id, date, text and markdown (or blocks)")
            continue
        msg = render(n)
        header = msg["blocks"][0]["text"]["text"] if msg["blocks"][0].get("type") == "header" else ""
        if len(header) > HEADER_MAX:
            out.append(f"{where}: the title is {len(header)} characters, Slack shows {HEADER_MAX}")
        if len(msg["blocks"]) > MAX_BLOCKS:
            out.append(f"{where}: {len(msg['blocks'])} blocks, Slack takes {MAX_BLOCKS}")
        out += rich.problems((msg["text"], msg["blocks"]), where)
    return out


def _complete(n):
    return all(n.get(k) for k in ("id", "date", "text")) and bool(n.get("markdown") or n.get("blocks"))


def notices_of(document):
    """{id: notice as the bot posts it} of DOCUMENT: {"id", "date", "text", "blocks",
    "digest"}; one missing what it needs is left out."""
    out = {}
    for n in (document or {}).get("notices", []):
        if isinstance(n, dict) and _complete(n):
            msg = render(n)
            out[n["id"]] = {"id": n["id"], "date": n["date"], **msg, "digest": digest(n["id"], msg)}
    return out


def digest(nid, msg):
    """The post as today's code renders notice NID (MSG, render()), hashed: a reworded
    notice and a renderer that shows it differently both edit the post, once."""
    said = [nid, msg["text"], msg["blocks"]]
    return hashlib.sha256(json.dumps(said, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def metadata(nid):
    """Slack message metadata on every notice post: which notice it is, read back exactly."""
    return {"event_type": EVENT, "event_payload": {"id": nid}}


def _header(blocks):
    return next((b["text"].get("text", "") for b in blocks or [] if b.get("type") == "header"), "")


def _button(blocks):
    urls = (e.get("url") for b in blocks or [] if b.get("type") == "actions" for e in b.get("elements", []))
    return next((u for u in urls if u), "")


def identify(message, notes, recorded, sites):
    """(notice id, how) of MESSAGE, or (None, None) when it is no notice post: its metadata
    (exact), the ts SEEN recorded for a notice (exact), its button to a notice page under
    one of SITES (the id is the page: a notice since dropped is found too), or a header that
    is a notice's title. The last two find the posts from before metadata was set."""
    payload = (message.get("metadata") or {}).get("event_payload") or {}
    if (message.get("metadata") or {}).get("event_type") == EVENT and payload.get("id"):
        return payload["id"], "metadata"
    if message.get("ts") in recorded:
        return recorded[message["ts"]], "record"
    url = _button(message.get("blocks"))
    if url and (url in notes or any(url.startswith(s) for s in sites)):
        return url, "link"
    title = _header(message.get("blocks"))
    nid = next((i for i, n in notes.items() if title and _header(n["blocks"]) == title), None)
    return (nid, "title") if nid else (None, None)


EXACT = ("metadata", "record")
# What a round sees and leaves: another app's or a person's post, one deleted by hand.
LEFT = ("foreign", "missing")


def plan(messages, notes, seen, mine, today, adopt):
    """What makes the channel's notice posts what NOTES (notices_of) says, in order:
    [{"do", "id", "ts", "how", ...}]. MESSAGES are the channel's (history), SEEN the record,
    MINE the bot's own (bot_id, app_id): its posts are the ones it may edit or delete. A post
    found only by its button or title (from before metadata) is changed with ADOPT alone,
    the owner's run after reading the dry run; otherwise it is left and counts as posted.

      update   a post whose notice is in NOTES: its text, blocks and metadata today's
      delete   a post whose notice NOTES no longer has, and a second post of one notice
      post     a notice due (TODAY or before) with no post and none recorded
      missing  a notice recorded as posted whose post is gone (deleted by hand): not again
      foreign  a notice post of another app or a person: left, said
      pin      the newest notice's post, the channel's one pinned notice
      unpin    another post of the bot's pinned (a pin whose record was lost): taken off
    """
    bot_id, app_id = mine
    recorded = {e["ts"]: nid for nid, e in seen.items() if isinstance(e, dict) and e.get("ts")}
    sites = {nid[: nid.index("/news/") + len("/news/")] for nid in notes if "/news/" in nid}
    found, out = {}, []
    for m in messages:
        if m.get("thread_ts", m.get("ts")) != m.get("ts"):
            continue  # a reply, not a post
        nid, how = identify(m, notes, recorded, sites)
        if nid is None:
            continue
        own = bool(bot_id and m.get("bot_id") == bot_id) or bool(app_id and m.get("app_id") == app_id)
        title = _header(m.get("blocks")) or (m.get("text") or "")[:80]
        if not own:
            out.append({"do": "foreign", "id": nid, "ts": m["ts"], "how": how, "title": title,
                        "by": m.get("bot_id") or m.get("user") or "?"})  # fmt: skip
            continue
        found.setdefault(nid, []).append({"ts": m["ts"], "how": how, "title": title,
                                          "replies": m.get("reply_count", 0),
                                          "pinned": bool(m.get("pinned_to"))})  # fmt: skip
    kept = {}
    for nid, posts in sorted(found.items()):
        n = notes.get(nid)
        entry = seen.get(nid) if isinstance(seen.get(nid), dict) else {}
        keep = None
        if n is not None:
            keep = next((p for p in posts if p["ts"] == entry.get("ts")), None)
            keep = keep or min(posts, key=lambda p: float(p["ts"]))
            kept[nid] = keep["ts"]
        for p in posts:
            if not adopt and p["how"] not in EXACT:
                continue
            if p is not keep:
                out.append({"do": "delete", "id": nid, **p, "why": "a second post" if n else "dropped"})
            elif entry.get("ts") != p["ts"] or entry.get("digest") != n["digest"]:
                out.append({"do": "update", "id": nid, **p})
    for n in sorted(notes.values(), key=lambda n: (n["date"], n["id"])):
        if n["id"] in kept or n["date"] > today:
            continue
        do = "missing" if n["id"] in seen else "post"
        out.append({"do": do, "id": n["id"], "title": _header(n["blocks"])})
    shown = set(kept) | {a["id"] for a in out if a["do"] == "post"}
    newest = max(shown, key=lambda nid: (notes[nid]["date"], nid)) if shown else None
    deleted = {a["ts"] for a in out if a["do"] == "delete"}
    for nid, posts in sorted(found.items()):
        for p in posts:
            if p["pinned"] and p["ts"] != kept.get(newest) and p["ts"] not in deleted:
                if adopt or p["how"] in EXACT:
                    out.append({"do": "unpin", "id": nid, "ts": p["ts"], "title": p["title"]})
    if newest is not None:
        pin = {"do": "pin", "id": newest, "title": _header(notes[newest]["blocks"])}
        target = next((p for p in found.get(newest, []) if p["ts"] == kept.get(newest)), None)
        if target is not None and messages:
            pin["pinned"] = target["pinned"]
        out.append(pin)
    return out


def own_replies(slack, channel, ts, ids):
    """(the ts of the bot's own replies under TS, how many replies are someone else's)."""
    found = replies(slack, channel, ts)
    own = [r["ts"] for r in found if is_own(r, ids)]
    return own, len(found) - len(own)


def describe(action, extra=""):
    a = action
    where = f" {a['ts']}" if a.get("ts") else ""
    how = f", found by {a['how']}" if a.get("how") else ""
    by = f", posted by {a['by']}" if a.get("by") else ""
    why = f", {a['why']}" if a.get("why") else ""
    return f"{a['do']}{where} {a['id']} {a.get('title', '')!r}{how}{by}{why}{extra}"


def apply(actions, slack, channel, notes, seen_path, pinned_path, ids, log):
    """Do ACTIONS (plan) in CHANNEL, recording each in SEEN_PATH as it goes: a failure
    later in the round never repeats what went out. A delete takes the bot's own replies
    under the post first; someone else's reply stays, under Slack's "deleted" mark. A post
    the bot may not change (cant_update_message, cant_delete_message) is said and left."""
    for a in actions:
        nid, n = a["id"], notes.get(a["id"])
        try:
            if a["do"] == "delete":
                theirs = 0
                if a.get("replies"):
                    own, theirs = own_replies(slack, channel, a["ts"], ids)
                    for ts in own:
                        delete(slack, channel, ts)
                delete(slack, channel, a["ts"])
                log(describe(a, f", {theirs} replies of others left" if theirs else ""))
                with state.locked(seen_path, {}) as seen:
                    if n is None and (seen.get(nid) or {}).get("ts") in (a["ts"], None):
                        seen.pop(nid, None)
            elif a["do"] == "update":
                slack.update(channel, a["ts"], n["text"], n["blocks"], metadata=metadata(nid))
                log(describe(a))
                with state.locked(seen_path, {}) as seen:
                    seen[nid] = {"date": n["date"], "ts": a["ts"], "digest": n["digest"]}
            elif a["do"] == "post":
                ts = slack.post(channel, n["text"], n["blocks"], metadata=metadata(nid))
                log(describe(a))
                with state.locked(seen_path, {}) as seen:
                    seen[nid] = {"date": n["date"], "ts": ts, "digest": n["digest"]}
            elif a["do"] == "pin":
                ts = (state.read(seen_path, {}).get(nid) or {}).get("ts")
                if ts and (ts != (state.read(pinned_path, {}) or {}).get("ts") or a.get("pinned") is False):
                    pin_latest(slack, channel, pinned_path, ts)
                    log(describe(a, f" ({ts})"))
            elif a["do"] == "unpin":
                unpin_message(slack, channel, a["ts"])
                log(describe(a))
            elif a["do"] in LEFT:
                pass  # said by the dry run; nothing to do
        except Exception as e:
            if not any(code in str(e) for code in ("cant_update_message", "cant_delete_message")):
                raise
            log(describe(a, f": not allowed ({e})"))


def round_(fetch, slack, channel, seen_path, pinned_path, today, log, dry_run=False, adopt=False):
    """One round: the channel made what notices.json says (plan, apply). The first round
    (no SEEN_PATH yet) counts every notice already due as announced, so a new deploy posts
    none again. DRY_RUN says each action (LOG) and changes nothing, the record neither. A
    notices.json with no notice changes nothing: an empty export is a broken one, never
    "delete every post"."""
    notes = notices_of(fetch())
    if not notes:
        log("notices.json lists no notice: nothing changed")
        return []
    channel = channel_id(slack, channel)
    ids = own_ids(slack)
    with _one_round(seen_path, dry_run):
        first = state.read(seen_path, None) is None
        seed = {n["id"]: {"date": n["date"]} for n in notes.values() if n["date"] <= today} if first else {}
        if dry_run:
            seen = seed or state.read(seen_path, {})
            _entries(seen, pinned_path)
        else:
            with state.locked(seen_path, {}) as recorded:
                recorded.update(seed)
                _entries(recorded, pinned_path)
                seen = dict(recorded)
        try:
            messages = history(slack, channel)
        except Error as e:  # the app's manifest not yet imported: new notices still go out
            if "missing_scope" not in str(e):
                raise
            log(f"the channel's history is not readable ({e}): import the app manifest; posting only")
            messages = []
        actions = plan(messages, notes, seen, ids, today, adopt)
        if dry_run:
            for a in actions:
                theirs = ""
                if a["do"] == "delete" and a.get("replies"):
                    own, others = own_replies(slack, channel, a["ts"], ids)
                    theirs = f", with {len(own)} bot replies; {others} replies of others left"
                log(describe(a, theirs))
            return actions
        apply(actions, slack, channel, notes, seen_path, pinned_path, ids, log)
        return actions


@contextmanager
def _one_round(seen_path, dry_run):
    """One round at a time, the bot's and an owner's run alike (a flock beside SEEN_PATH)."""
    if dry_run:
        yield
        return
    os.makedirs(os.path.dirname(seen_path) or ".", exist_ok=True)
    with open(f"{seen_path}.round", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


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


def pin_latest(slack, channel, path, ts):
    """Pin the message TS in CHANNEL and unpin the one pinned before it (kept at PATH): the
    channel keeps one pinned notice, the newest. A previous pin someone already removed is
    no error, and so is a pin Slack already has (its record lost). CHANNEL is a name (#...)
    or an ID."""
    channel = channel_id(slack, channel)
    with state.locked(path, {}) as pinned:
        pin_message(slack, channel, ts)
        old, pinned["ts"] = pinned.get("ts"), ts
        if old and old != ts:
            unpin_message(slack, channel, old)


def main(argv=None):
    """One round now, as the bot's runs it: python -m cluster_bot.notices CONFIG [--dry-run]."""
    parser = argparse.ArgumentParser(prog="python -m cluster_bot.notices", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    parser.add_argument("config", help="the bot's config.json")
    parser.add_argument("--dry-run", action="store_true", help="say what the round would do; change nothing")
    args = parser.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text())
    docs = cfg.get("notices") or {}
    if not (docs.get("url") and docs.get("channel")):
        parser.error(f"{args.config}: no notices url or channel")
    slack = Slack(Path(cfg["bot_token_file"]).read_text().strip())
    said = []

    def say(line):
        said.append(line)
        print(f"notices: {line}", flush=True)

    actions = round_(lambda: fetch_json(docs["url"]), slack, docs["channel"], docs["seen"], docs["pinned"],
                     date.today().isoformat(), say, dry_run=args.dry_run, adopt=True)  # fmt: skip
    if not args.dry_run:  # the owner's run also says what it left, as the dry run does
        changed = bool(said)
        for a in actions:
            if a["do"] in LEFT:
                say(describe(a))
        if not changed:
            say("the channel already shows notices.json: nothing changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
