"""The user docs' notices in the announcement channel: the docs site publishes each notice
(notices.json next to the site, written by the docs' scripts/notices.py export), and the
bot posts each one once, on its date, through the cluster's own Slack app. The docs
repository holds no Slack credential.

    notices.json  {"pins_max", "notices": [{"id", "date" (YYYY-MM-DD), "text" (the
                   fallback), "title", "markdown", "link": {"text", "url"}, "context",
                   "pin_until" (YYYY-MM-DD, absent: not pinned)}]}

The notice is Markdown, rendered here by cluster_tools.rich, the one renderer of Slack
text: render(notice) is the message, a header (TITLE), the lead (MARKDOWN), a button to
the page (LINK) and a line of small print (CONTEXT, Markdown too). check(document) says
what of a notices.json the bot could not show as written; the docs run it on every
commit (scripts/deepops/notice_render.py, the notice-render hook). A notice published
before the docs sent Markdown carries its own "blocks", posted as they are.

The channel is kept like notices.json, every round (round_, plan): each notice due (its
date today or before) has one post, and each post shows the notice as today's code
renders it. The pins are what people must know now: every notice whose pin_until has not
passed, the newest pins_max of them, pinned, and the bot's other notice posts unpinned;
the docs decide both (their notice format), the bot only applies them. A notice reworded on the site, or rendered differently by a
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
    blocks = [rich.header(title)]
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
    when every notice renders within Slack's limits and shows no marker left as typed, and
    its pins (pins_max, each pin_until) read as the bot reads them."""
    notices = (document or {}).get("notices") if isinstance(document, dict) else None
    if not isinstance(notices, list):
        return ['notices.json: no "notices" list']
    out = []
    if "pins_max" in document and not pins_max(document):
        out.append(f"notices.json: pins_max {document['pins_max']!r} is not a positive count")
    for i, n in enumerate(notices):
        where = f"notice {n.get('id', i) if isinstance(n, dict) else i}"
        if not isinstance(n, dict) or not _complete(n):
            out.append(f"{where}: needs id, date, text and markdown (or blocks)")
            continue
        if "pin_until" in n and not _day(n["pin_until"]):
            out.append(f"{where}: pin_until {n['pin_until']!r} is not a YYYY-MM-DD day")
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


def _day(value):
    """VALUE when it is a YYYY-MM-DD day, else None."""
    try:
        return date.fromisoformat(value).isoformat() if isinstance(value, str) else None
    except ValueError:
        return None


def notices_of(document):
    """{id: notice as the bot posts it} of DOCUMENT: {"id", "date", "text", "blocks",
    "digest", "pin_until" (a day, or None)}; one missing what it needs is left out."""
    out = {}
    for n in (document or {}).get("notices", []):
        if isinstance(n, dict) and _complete(n):
            msg = render(n)
            out[n["id"]] = {"id": n["id"], "date": n["date"], **msg, "digest": digest(n["id"], msg),
                            "pin_until": _day(n.get("pin_until"))}  # fmt: skip
    return out


def pins_max(document):
    """How many notices DOCUMENT lets stand pinned at once (its pins_max; none: 0)."""
    n = (document or {}).get("pins_max")
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def pinned_notices(notes, shown, today, most):
    """The notices of SHOWN (ids with a post) to stand pinned TODAY: pin_until not passed,
    the newest MOST of them (NOTES, notices_of)."""
    live = [
        nid
        for nid in shown
        if notes[nid]["pin_until"] and notes[nid]["date"] <= today <= notes[nid]["pin_until"]
    ]
    return sorted(live, key=lambda nid: (notes[nid]["date"], nid), reverse=True)[:most]


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


def plan(messages, notes, seen, mine, today, adopt, most=0, pinned=()):
    """What makes the channel's notice posts what NOTES (notices_of) says, in order:
    [{"do", "id", "ts", "how", ...}]. MESSAGES are the channel's (history), SEEN the record,
    MINE the bot's own (bot_id, app_id): its posts are the ones it may edit or delete. A post
    found only by its button or title (from before metadata) is changed with ADOPT alone,
    the owner's run after reading the dry run; otherwise it is left and counts as posted.
    MOST: the pins at once (pins_max); PINNED: the ts the record says the bot pinned, what
    it unpins when the history cannot be read.

      update   a post whose notice is in NOTES: its text, blocks and metadata today's
      delete   a post whose notice NOTES no longer has, and a second post of one notice
      post     a notice due (TODAY or before) with no post and none recorded
      missing  a notice recorded as posted whose post is gone (deleted by hand): not again
      foreign  a notice post of another app or a person: left, said
      pin      a post of a notice to stand pinned (pinned_notices)
      unpin    another post of the bot's that stands pinned (in the history, or recorded)
    """
    recorded = {e["ts"]: nid for nid, e in seen.items() if isinstance(e, dict) and e.get("ts")}
    sites = {nid[: nid.index("/news/") + len("/news/")] for nid in notes if "/news/" in nid}
    found, out = {}, []
    for m in messages:
        if m.get("thread_ts", m.get("ts")) != m.get("ts"):
            continue  # a reply, not a post
        nid, how = identify(m, notes, recorded, sites)
        if nid is None:
            continue
        own = is_own(m, mine)
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
    if not messages:  # the history unread: the record says which posts stand
        kept = {
            nid: e["ts"] for nid, e in seen.items() if nid in notes and isinstance(e, dict) and e.get("ts")
        }
    shown = set(kept) | {a["id"] for a in out if a["do"] == "post"}
    want = pinned_notices(notes, shown, today, most)
    stays = {kept[nid] for nid in want if nid in kept}
    deleted = {a["ts"] for a in out if a["do"] == "delete"}
    for nid, posts in sorted(found.items()):
        for p in posts:
            if p["pinned"] and p["ts"] not in stays and p["ts"] not in deleted:
                if adopt or p["how"] in EXACT:
                    out.append({"do": "unpin", "id": nid, "ts": p["ts"], "title": p["title"]})
    if not messages:  # the history unread: the record says what the bot pinned
        for ts in sorted(set(pinned) - stays):
            nid = recorded.get(ts, "")
            out.append(
                {"do": "unpin", "id": nid, "ts": ts, "title": _header((notes.get(nid) or {}).get("blocks"))}
            )
    for nid in sorted(want, key=lambda nid: (notes[nid]["date"], nid)):
        pin = {"do": "pin", "id": nid, "title": _header(notes[nid]["blocks"])}
        target = next((p for p in found.get(nid, []) if p["ts"] == kept.get(nid)), None)
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
                ts = slack.post(channel, n["text"], n["blocks"], metadata=metadata(nid), cls="kept")
                log(describe(a))
                with state.locked(seen_path, {}) as seen:
                    seen[nid] = {"date": n["date"], "ts": ts, "digest": n["digest"]}
            elif a["do"] == "pin":
                ts = (state.read(seen_path, {}).get(nid) or {}).get("ts")
                if ts and (ts not in pins(pinned_path) or a.get("pinned") is False):
                    pin_message(slack, channel, ts)
                    _record_pin(pinned_path, ts, True)
                    log(describe(a, f" ({ts})"))
            elif a["do"] == "unpin":
                unpin_message(slack, channel, a["ts"])
                _record_pin(pinned_path, a["ts"], False)
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
    document = fetch()
    notes = notices_of(document)
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
        actions = plan(messages, notes, seen, ids, today, adopt, pins_max(document), pins(pinned_path))
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
    ts = _old_pin(state.read(pinned_path, {})) if pinned_path and old else None
    if ts:
        seen[old[-1][1]]["ts"] = ts


def _old_pin(record):
    """The one pin of a record from before several ({"ts": ts}), else None."""
    ts = (record or {}).get("ts") if isinstance(record, dict) else None
    return ts if isinstance(ts, str) else None


def pins(path):
    """The ts of the notice posts the bot pinned (PATH: {"pins": [ts, ...]}; a record from
    before several pins, {"ts": ts}, is its one)."""
    record = state.read(path, {}) if path else {}
    if isinstance(record, dict) and isinstance(record.get("pins"), list):
        return set(record["pins"])
    return {ts} if (ts := _old_pin(record)) else set()


def _record_pin(path, ts, on):
    with state.locked(path, {}) as record:
        now = (
            set(record.get("pins", []))
            if isinstance(record.get("pins"), list)
            else ({_old_pin(record)} - {None})
        )
        now = now | {ts} if on else now - {ts}
        record.clear()
        record["pins"] = sorted(now)


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
    slack = Slack(Path(cfg["bot_token_file"]).read_text().strip(), places={docs["channel"]: "announce"})
    said = []

    def say(line):
        said.append(line)
        print(f"notices: {line}")

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
