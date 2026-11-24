"""The Slack channels' guides, kept by the bot: each channel's purpose, topic, canvas (the
channel's README tab) and bookmarks, from the user docs site (channels.json next to the
site, written by the docs' build), so the text people read in Slack has one source.

    channels.json  {"channels": {ROLE: {"purpose", "topic", "canvas": {"title", "markdown"},
                                        "bookmarks": [{"title", "url", "emoji"}]}}}

ROLE is a channel's purpose (slack_channels: announce, help, status, admin); the names
are the site's (config), never the docs'. "[[#ROLE]]" in the text is that channel: a
mention in the canvas, its name in a purpose or topic.

round_(fetch, slack, names, state_path, today, log) converges each channel the site
describes and the config names:
- the purpose and the topic, set only when they differ;
- the canvas: created once, its ID kept; replaced in full when its text changed and once
  a day (a hand edit lasts a day at most), and kept read-only for the channel's members
  (canvases.access.set; a refusal is logged, never fatal);
- the bookmarks the bot made (their IDs kept): added, edited or removed to match; a
  bookmark a person added is never touched.
A site that cannot be read changes nothing; one channel's failure is logged and the
others still converge.
"""

import hashlib
import json
import re

from . import state

MENTION = re.compile(r"\[\[#([a-z]+)\]\]")


def channel_ids(slack):
    """{channel name without '#': ID} of the channels the bot can see."""
    out, cursor = {}, ""
    while True:
        params = {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        body = slack.call("conversations.list", **params)
        out.update({c["name"]: c["id"] for c in body.get("channels", [])})
        cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            return out


def _text(text, names, ids=None):
    """TEXT with each [[#ROLE]] as that channel: a canvas mention (IDS given) or its name."""

    def repl(m):
        name = names[m.group(1)]
        return f"![](#{ids[name.lstrip('#')]})" if ids is not None else name

    return MENTION.sub(repl, text)


def _failed(error, *codes):
    return any(code in str(error) for code in codes)


def _canvas(slack, cid, info, spec, names, ids, path, role, today, log):
    title, markdown = spec["title"], _text(spec["markdown"], names, ids)
    digest = hashlib.sha256(f"{title}\0{markdown}".encode()).hexdigest()
    content = json.dumps({"type": "markdown", "markdown": markdown})
    with state.locked(path, {}) as st:
        held = st.setdefault("canvases", {}).get(role)
        if held is None:
            try:
                canvas_id = slack.call(
                    "conversations.canvases.create", channel_id=cid, title=title, document_content=content
                )["canvas_id"]
                held = {"id": canvas_id, "hash": digest, "day": today}
                log(f"{role}: canvas {canvas_id} created")
            except Exception as e:
                if not _failed(e, "channel_canvas_already_exists"):
                    raise
                canvas_id = ((info.get("properties") or {}).get("canvas") or {}).get("file_id")
                if not canvas_id:
                    raise RuntimeError(
                        f"{role}: the channel has a canvas the bot did not record, and "
                        f"conversations.info names none: {info.get('properties')}"
                    ) from None
                held = {"id": canvas_id, "hash": "", "day": ""}  # replaced below
                log(f"{role}: canvas {canvas_id} found in the channel, taken over")
            _lock(slack, held["id"], cid, role, log)
            st["canvases"][role] = held
        if (held["hash"], held["day"]) != (digest, today):
            changes = [{"operation": "replace", "document_content": json.loads(content)}]
            slack.call("canvases.edit", canvas_id=held["id"], changes=json.dumps(changes))
            if held["hash"] != digest:  # the title, too, when the text changed
                rename = [{"operation": "rename", "title_content": {"type": "markdown", "markdown": title}}]
                slack.call("canvases.edit", canvas_id=held["id"], changes=json.dumps(rename))
            _lock(slack, held["id"], cid, role, log)
            held.update(hash=digest, day=today)


def _lock(slack, canvas_id, cid, role, log):
    try:
        slack.call(
            "canvases.access.set", canvas_id=canvas_id, access_level="read", channel_ids=json.dumps([cid])
        )
    except Exception as e:  # noqa: BLE001 - the guide stays; the daily replace undoes edits
        log(f"{role}: canvas {canvas_id} could not be made read-only: {e}")


def _bookmarks(slack, cid, wanted, path, role):
    existing = {b["id"]: b for b in slack.call("bookmarks.list", channel_id=cid).get("bookmarks", [])}
    want = {b["title"]: b for b in wanted}
    with state.locked(path, {}) as st:
        mine = st.setdefault("bookmarks", {}).setdefault(role, {})
        for title, bid in list(mine.items()):
            if bid not in existing:  # removed by hand: added again below if still wanted
                del mine[title]
            elif title not in want:
                slack.call("bookmarks.remove", channel_id=cid, bookmark_id=bid)
                del mine[title]
        for title, b in want.items():
            bid = mine.get(title)
            if bid is None:
                added = slack.call(
                    "bookmarks.add", channel_id=cid, title=title, type="link", link=b["url"], emoji=b["emoji"]
                )
                mine[title] = added["bookmark"]["id"]
            elif (existing[bid].get("link"), existing[bid].get("emoji") or "") != (b["url"], b["emoji"]):
                slack.call("bookmarks.edit", channel_id=cid, bookmark_id=bid, link=b["url"], emoji=b["emoji"])


def round_(fetch, slack, names, state_path, today, log):
    guides = (fetch() or {}).get("channels") or {}
    ids = channel_ids(slack)
    for role, spec in sorted(guides.items()):
        name = names.get(role, "")
        if not name:
            continue
        try:
            cid = ids.get(name.lstrip("#"))
            if cid is None:
                raise RuntimeError(f"{name}: no such channel the bot can see (invite it)")
            info = slack.call("conversations.info", channel=cid)["channel"]
            for field, method in (
                ("purpose", "conversations.setPurpose"),
                ("topic", "conversations.setTopic"),
            ):
                value = _text(spec[field], names)
                if (info.get(field) or {}).get("value", "") != value:
                    slack.call(method, channel=cid, **{field: value})
            _canvas(slack, cid, info, spec["canvas"], names, ids, state_path, role, today, log)
            _bookmarks(slack, cid, spec.get("bookmarks") or [], state_path, role)
        except Exception as e:  # noqa: BLE001 - one channel's failure leaves the others
            log(f"{role}: {e}")
