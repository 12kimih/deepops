"""slack: the one client for Slack's Web API, for every tool that tells people something.

    slack = Slack(token, dm_log=DmLog("#alerts", state))  # dry_run=True prints instead
    slack.post("#infra-status", text, blocks=None, thread_ts=None) -> the message's ts
    slack.dm("U0123ABCD", text, kind="disk", login="kim")         -> (DM channel, ts)
    slack.dm_channel("U0123ABCD")           -> a member's DM channel, for a preview
    slack.update(channel, ts, text, blocks=None)                  -> edits a message
    member(slack, user, user_map, email_domain, cache) -> a person's Slack member ID
    slack.call(method, **params)            -> any other method, parsed
    sections(text), table(header, rows)     -> message blocks within Slack's limits
                                               (cluster_tools.rich: the Markdown every
                                               text is written in, sent as rich text)
    mentioning(who, text, blocks)           -> (text, blocks) that notify WHO first
    bar(percent)                            -> a text gauge for a code span or table
    pages(title, blocks)                    -> thread replies [(text, blocks)] holding
                                               every block, each within Slack's limits
    keep(slack, channel, key, card, thread, record) -> one message kept up to date, with
                                               its replies (below); the record to keep
    find(slack, channel, key, legacy) -> the bot's own messages marked KEY, newest first

A message the bot keeps up to date (a list edited in place, a day's log) is marked with
Slack's message metadata (event_type KEPT, payload {"key", "part"}) and found in the
channel's history, never only through a ts the caller kept: a record lost, unreadable or
from before a restart posted a second copy beside the first (the ban list, 2026-11-26).
keep() reads the history before it posts and on its first round in a process, adopts
one copy (the recorded one, else the newest), deletes the others with their replies,
and keeps the full text in the thread, one reply per page, edited in place and the
extra ones deleted. Without the history scopes (the app manifest not imported yet) it
works from the record alone and says so.

Every message to a person goes through dm(), never post(), which refuses a member ID: so
each DM is in the journal (one "dm KIND LOGIN" line) and, unless sent with log=False,
logged for the administrators (DmLog: one reply per DM in a daily thread of their
channel, no mention, so the channel's alerts stay on top): they see what people were
told and can help.

Every failure is an Error naming the method and Slack's reason; Error is a RuntimeError.
Each call is bounded by TIMEOUT seconds. A call Slack rate-limits (HTTP 429) is sent
again once, after the Retry-After it names (at most RETRY_MAX seconds). Interactive features (buttons, forms, Socket
Mode) are the cluster bot's (roles/cluster_bot); this module only sends and reads.
Python 3.9 or newer, standard library only.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import rich
from .files import write_atomic
from .render import width

API = "https://slack.com/api/"
TIMEOUT = 30
# The longest Retry-After a rate-limited call waits before its one retry (seconds).
RETRY_MAX = 60
# A Slack member ID (users, and workspace-wide W... IDs).
MEMBER = re.compile(r"[UW][A-Z0-9]+")
# How long a looked-up member ID is trusted (seconds).
MEMBER_TTL = 86400
MAX_BLOCKS = 45  # Slack allows 50 blocks per message
MAX_SECTION = 2900  # and 3000 characters per section text
# The characters of shown text one thread reply holds: Slack refuses a message far
# longer (msg_too_long), and a reply this size is still read at a glance.
MAX_PAGE = 12000
# The metadata event_type of a message kept up to date (keep()).
KEPT = "cluster_kept"
# What Slack says of a message that is not there any more, or that it will not edit.
GONE = ("message_not_found", "thread_not_found", "cant_update_message")
BAR_WIDTH = 10
# How much of a logged DM's first line the log line keeps.
DM_LOG_FIRST = 200


class Error(RuntimeError):
    """Slack refused or could not be reached; str() says which method and why."""


class DmLog:
    """The administrators' record of the DMs: in CHANNEL, one message a day (on the first
    DM of the day, its count kept up to date) and under it one reply per DM. STATE_FILE
    holds today's message across runs and the processes of one tool (locked)."""

    def __init__(self, channel, state_file):
        self.channel, self.state_file = channel, state_file

    @contextlib.contextmanager
    def _today(self, now):
        os.makedirs(os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True)
        with open(self.state_file + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
            except (OSError, ValueError):
                state = {}
            day = time.strftime("%Y-%m-%d", time.localtime(now))
            if state.get("day") != day:
                state = {"day": day, "ts": "", "channel": "", "count": 0}
            yield state
            write_atomic(self.state_file, json.dumps(state), mode=0o600)

    def record(self, slack, line, now=None):
        """LINE under today's message (made by this call if there is none yet). The
        message's channel is kept as Slack gave it, an ID: chat.update takes no name."""
        with self._today(time.time() if now is None else now) as state:
            if not state["ts"]:
                state["channel"], state["ts"] = self._day_message(slack, state["day"])
            slack.post(state["channel"], line, thread_ts=state["ts"])
            state["count"] += 1
            try:
                slack.update(state["channel"], state["ts"], dm_log_title(state["day"], state["count"]))
            except Error as e:  # the count is cosmetic: the line is in, and so is the state
                print(f"slack: the day's DM count was not updated: {e}", file=sys.stderr)

    def _day_message(self, slack, day):
        """(channel ID, ts) of DAY's message: the one in the channel (marked, find()) when
        the state lost it, else a new one, so a day never has two."""
        key = f"dm-log {day}"
        try:
            channel = channel_id(slack, self.channel)
            found = find(slack, channel, key)
        except Error as e:  # a token without the read scopes: posted as before
            if not _no_history(e):
                raise
            channel, found = self.channel, []
        if found:
            return channel, found[0]
        made = slack.call(
            "chat.postMessage", channel=channel, text=dm_log_title(day, 0), metadata=json.dumps(marked(key))
        )
        return made["channel"], made["ts"]


class Slack:
    def __init__(self, token, dry_run=False, timeout=TIMEOUT, dm_log=None):
        self.token, self.dry_run, self.timeout, self.dm_log = token, dry_run, timeout, dm_log

    def call(self, method, **params):
        if self.dry_run and method != "users.lookupByEmail":
            self._print(method, params)
            return {"ok": True, "ts": "dry-run", "channel": {"id": "dry-run"}}
        data = urllib.parse.urlencode(params).encode()
        for attempt in (1, 2):
            req = urllib.request.Request(
                API + method, data=data, headers={"Authorization": f"Bearer {self.token}"}
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                with e:
                    wait = _retry_after(e) if e.code == 429 and attempt == 1 else None
                    if wait is None:
                        raise Error(f"Slack {method} failed: {e}") from None
                time.sleep(wait)
            except (OSError, ValueError) as e:
                raise Error(f"Slack {method} failed: {e}") from e
        if not isinstance(body, dict):
            raise Error(f"Slack {method} failed: the answer is not a JSON object")
        if not body.get("ok") and body.get("error") != "users_not_found":
            raise Error(f"Slack {method} failed: {body.get('error')} {body.get('response_metadata', '')}")
        return body

    @staticmethod
    def _print(method, params):
        print(f"--- {method} {params.get('channel', '')} thread={params.get('thread_ts', '')}")
        for b in json.loads(params.get("blocks", "[]")):
            print(rich.read([b]) or "-" * 40)
        if "blocks" not in params:
            print(params.get("text", ""))
        print()

    def post(self, channel, text, blocks=None, thread_ts=None, metadata=None):
        """The message's ts. METADATA ({"event_type", "event_payload"}, Slack's message
        metadata) marks what the message is, so the bot finds it again exactly."""
        if MEMBER.fullmatch(channel):
            raise Error(f"Slack chat.postMessage: {channel} is a person; a message to a person is a dm()")
        msg = rich.message(text, blocks or None)
        params = {"channel": channel, "text": msg["text"], "blocks": json.dumps(msg["blocks"]),
                  "unfurl_links": "false", "unfurl_media": "false"}  # fmt: skip
        if thread_ts:
            params["thread_ts"] = thread_ts
        if metadata:
            params["metadata"] = json.dumps(metadata)
        return self.call("chat.postMessage", **params)["ts"]

    def dm_channel(self, member):
        """MEMBER's DM channel: where dm() sends, and where an operator's own preview of
        a run goes (cluster_tools.usage_report --to), which tells no one anything."""
        return self.call("conversations.open", users=member)["channel"]["id"]

    def dm(self, member, text, blocks=None, *, kind, login, log=True):
        """A direct message to MEMBER (a member ID) about KIND for LOGIN; (channel, ts).
        With log and a dm_log (DmLog), the log gets one line: the kind, the login, the
        message's first line and a link.
        That line failing is said on stderr, never undoes or repeats the sent DM."""
        logged = log and self.dm_log is not None
        channel = self.dm_channel(member)
        ts = self.post(channel, text, blocks)
        print(f"dm {kind} {login} {channel} {ts}")
        if logged and not self.dry_run:
            try:
                link = self.call("chat.getPermalink", channel=channel, message_ts=ts).get("permalink", "")
                self.dm_log.record(self, dm_log_line(kind, login, text, link))
            except (Error, OSError) as e:
                print(f"slack: the DM to {login} ({kind}) was sent; logging it failed: {e}", file=sys.stderr)
        return channel, ts

    def update(self, channel, ts, text, blocks=None, metadata=None):
        msg = rich.message(text, blocks or None)
        params = {"channel": channel, "ts": ts, "text": msg["text"], "blocks": json.dumps(msg["blocks"])}
        if metadata:
            params["metadata"] = json.dumps(metadata)
        return self.call("chat.update", **params)


def dm_log_title(day, count):
    """The day's message the DM lines are threaded under."""
    return f":envelope: Notices sent to people by DM on {day}: {count} (each in the thread)"


def dm_log_line(kind, login, text, link=""):
    """The administrators' line for one DM: what kind, to whom, its first line, a link."""
    shown = rich.plain(text).strip()
    first = shown.splitlines()[0] if shown else ""
    if len(first) > DM_LOG_FIRST:
        first = first[: DM_LOG_FIRST - 3] + "..."
    return f"`{escape(kind)}` to **{escape(login)}**: {escape(first)}" + (f"  <{link}|open>" if link else "")


def _retry_after(error):
    """The seconds a 429 asks to wait, within RETRY_MAX; None when it names none."""
    try:
        wait = int(error.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return None
    return wait if 0 <= wait <= RETRY_MAX else None


def escape(text):
    """TEXT shown as itself in a message (cluster_tools.rich): &, < and > as Slack's
    entities (a job name with <!channel> would mention everyone), and the Markdown's own
    characters escaped, so a value is never read as a style or a link."""
    text = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\*_`\[\]:])", r"\\\1", text)


def member(slack, user, user_map, email_domain, cache):
    """A cluster user's Slack member ID: from user_map (an ID, or an email to look up),
    else <user>@<email_domain>; "" when unknown. CACHE (a dict the caller keeps)
    holds each answer for MEMBER_TTL seconds."""
    hit = cache.get(user)
    if hit and hit["checked"] > time.time() - MEMBER_TTL:
        return hit["id"]
    target = user_map.get(user) or (f"{user}@{email_domain}" if email_domain else "")
    found = ""
    if MEMBER.fullmatch(target):
        found = target
    elif "@" in target and slack.token:
        found = slack.call("users.lookupByEmail", email=target).get("user", {}).get("id", "")
    cache[user] = {"id": found, "checked": time.time()}
    return found


# --- Message blocks -----------------------------------------------------------


def bar(pct, width=BAR_WIDTH):
    """PCT (0 to 100, None unknown) as a gauge of WIDTH blocks and the percent; the same
    width either way, so a table's columns line up."""
    if pct is None:
        return "-".ljust(width + 5)
    filled = round(max(0.0, min(pct, 100.0)) / 100 * width)
    return "█" * filled + "░" * (width - filled) + f" {pct:3.0f}%"


def table(header, rows):
    """A fixed-width table in a code block; Slack renders it monospaced. Cells may hold
    what users named (folders, jobs): each is escaped after padding, so the columns line
    up as Slack shows them; a wide character (Korean) takes two columns (render.width)."""
    widths = [max(width(str(c)) for c in col) for col in zip(header, *rows)]

    def fmt(r):
        return "  ".join(escape(str(c) + " " * (w - width(str(c)))) for c, w in zip(r, widths)).rstrip()

    return "```\n" + "\n".join([fmt(header)] + [fmt(r) for r in rows]) + "\n```"


def mentioning(who, text, blocks=None):
    """TEXT and BLOCKS with WHO first (a mention: <!subteam^S0123ABCD>, <@U0123ABCD>,
    <!channel>), so Slack notifies them: a message with blocks notifies whom its blocks
    mention, its text is only the fallback. An empty WHO leaves both as they are."""
    if not who:
        return text, blocks
    return f"{who} {text}", ([*rich.blocks(who), *blocks] if blocks else blocks)


def sections(text):
    """Rich text blocks holding all of TEXT (cluster_tools.rich): a longer text is split at
    lines into several within Slack's limits, a code block closed at the end of one and
    reopened in the next. Nothing is cut: a line left out could be the mention that tells
    an owner about a job leaving its GPUs idle."""
    return rich.blocks(text)


def pages(title, blocks):
    """BLOCKS as thread replies [(text, blocks)], in order, each at most MAX_BLOCKS
    blocks and MAX_PAGE characters of shown text; TEXT is TITLE, numbered when there are
    several. Nothing is cut: a block longer than a page is a page of its own."""
    out, cur, size = [], [], 0
    for block in blocks:
        n = len(rich.read([block]))
        if cur and (len(cur) >= MAX_BLOCKS or size + n > MAX_PAGE):
            out.append(cur)
            cur, size = [], 0
        cur.append(block)
        size += n
    if cur:
        out.append(cur)
    if len(out) == 1:
        return [(title, out[0])]
    return [(f"{title} ({i}/{len(out)})", page) for i, page in enumerate(out, 1)]


# --- Messages kept up to date -------------------------------------------------


def channel_id(slack, channel):
    """CHANNEL as an ID: a name (#...) is looked up; history, edits and pins take an ID."""
    if not channel.startswith("#"):
        return channel
    cursor = ""
    while True:
        params = {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        body = slack.call("conversations.list", **params)
        for c in body.get("channels", []):
            if c.get("name") == channel[1:]:
                return c["id"]
        cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            raise Error(f"Slack conversations.list: no channel {channel} the bot can see (invite it)")


def own(slack):
    """(bot_id, app_id) of the token: what the bot's own messages carry. Asked once per
    client."""
    ids = getattr(slack, "_own", None)
    if ids is None:
        me = slack.call("auth.test")
        bot_id = me.get("bot_id") or ""
        slack._own_user = me.get("user_id") or ""  # the bot's user (own_user())
        app_id = (slack.call("bots.info", bot=bot_id).get("bot") or {}).get("app_id", "") if bot_id else ""
        ids = slack._own = (bot_id, app_id)
    return ids


def own_user(slack):
    """The member ID of the token's bot user: what Slack names as having changed what the
    bot did (a bookmark's last_updated_by_user_id)."""
    own(slack)
    return slack._own_user


def is_own(message, ids):
    """MESSAGE was posted by the bot of IDS (own())."""
    bot_id, app_id = ids
    return bool(bot_id and message.get("bot_id") == bot_id) or bool(
        app_id and message.get("app_id") == app_id
    )


def history(slack, channel):
    """Every message in CHANNEL (an ID), newest first, with its metadata."""
    out, cursor = [], ""
    while True:
        params = {"channel": channel, "limit": 200, "include_all_metadata": "true"}
        if cursor:
            params["cursor"] = cursor
        body = slack.call("conversations.history", **params)
        out += body.get("messages", [])
        cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            return out


def replies(slack, channel, ts):
    """The replies under TS in CHANNEL (an ID), oldest first, with their metadata."""
    out, cursor = [], ""
    while True:
        params = {"channel": channel, "ts": ts, "limit": 200, "include_all_metadata": "true"}
        if cursor:
            params["cursor"] = cursor
        body = slack.call("conversations.replies", **params)
        out += [m for m in body.get("messages", []) if m.get("ts") != ts]
        cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            return out


def gone(error):
    """ERROR (an Error) says the message is not there, or not to be edited."""
    return any(code in str(error) for code in GONE)


def delete(slack, channel, ts, ids=None):
    """Delete the message TS and, given IDS (own()), the bot's own replies under it first
    (Slack keeps a thread whose parent is deleted, showing it as deleted); one already
    gone is no error."""
    try:
        if ids is not None:
            for r in replies(slack, channel, ts):
                if is_own(r, ids):
                    delete(slack, channel, r["ts"])
        slack.call("chat.delete", channel=channel, ts=ts)
    except Error as e:
        if not gone(e):
            raise


# Slack's answers that mean the pin already stands as asked: no error to whoever pins.
PINNED = ("already_pinned",)
UNPINNED = ("no_pin", "not_pinned", "message_not_found")


def pin_message(slack, channel, ts):
    """Pin the message TS in CHANNEL (an ID); one already pinned is no error."""
    try:
        slack.call("pins.add", channel=channel, timestamp=ts)
    except Error as e:
        if not any(code in str(e) for code in PINNED):
            raise


def unpin_message(slack, channel, ts):
    """Take the pin off TS in CHANNEL (an ID); one already off, or a message gone, is no error."""
    try:
        slack.call("pins.remove", channel=channel, timestamp=ts)
    except Error as e:
        if not any(code in str(e) for code in UNPINNED):
            raise


def marked(key, part=0):
    """The metadata of the message kept as KEY (PART: its reply's number, 0 the message)."""
    return {"event_type": KEPT, "event_payload": {"key": key, "part": part}}


def _key(message):
    meta = message.get("metadata") or {}
    if meta.get("event_type") != KEPT:
        return None, None
    payload = meta.get("event_payload") or {}
    return payload.get("key"), payload.get("part", 0)


def find(slack, channel, key, legacy=None):
    """The ts of the bot's own messages in CHANNEL (an ID) kept as KEY, newest first: marked
    so, or (LEGACY(message) true, for those posted before the mark) unmarked. Raises
    Error (missing_scope) without the history scopes."""
    ids = own(slack)
    out = []
    for m in history(slack, channel):
        k, part = _key(m)
        if is_own(m, ids) and ((k == key and not part) or (k is None and legacy is not None and legacy(m))):
            out.append(m["ts"])
    return out


def _digest(message):
    return hashlib.sha256(json.dumps(message, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _no_history(error):
    return "missing_scope" in str(error) or "not_in_channel" in str(error)


def _adopt(slack, cid, key, legacy, record, ids, log):
    """The record after reading the channel: of the copies of KEY found (find()) the
    recorded one, else the newest, kept, the others deleted with their replies. None
    when the history is not readable."""
    try:
        found = find(slack, cid, key, legacy)
    except Error as e:
        if not _no_history(e):
            raise
        log(f"{key}: the channel's history is not readable ({e}); kept by its record alone. "
            "Import the app manifest and reinstall the app")  # fmt: skip
        return None
    kept = record.get("ts") if record.get("ts") in found else (found[0] if found else "")
    for ts in found:
        if ts != kept:
            delete(slack, cid, ts, ids)
            log(f"{key}: deleted a second copy ({ts}), kept {kept}")
    if kept == record.get("ts"):
        return record
    if kept:
        log(f"{key}: took over the message {kept} found in the channel")
        return {"ts": kept, "taken": True}
    return {}


def keep(slack, channel, key, card, thread=(), record=None, *, legacy=None, pin=False, log=None):
    """Keep one message in CHANNEL as KEY: CARD (text, blocks) the message, THREAD
    [(text, blocks)] its replies (pages()), in order. RECORD (what the last call
    returned, or None) saves edits that change nothing; the returned record is the
    caller's to keep. The history is read on a first call in this client and whenever the
    record has no message: of the copies found (find(); LEGACY matches the unmarked ones
    from before) the recorded one, else the newest, is kept and the others deleted with
    their replies, so no second copy ever stands. A message deleted by hand is posted
    again. PIN: the message is pinned when posted or taken over. LOG(line) says what was
    done beyond an edit."""
    log = log or (lambda line: print(f"slack: {line}", file=sys.stderr))
    record = dict(record or {})
    cid = record.get("channel") or channel_id(slack, channel)
    ids = own(slack)
    scanned = slack.__dict__.setdefault("_kept_scanned", set())
    readable = True
    if key not in scanned or not record.get("ts"):
        adopted = _adopt(slack, cid, key, legacy, record, ids, log)
        readable = adopted is not None
        if readable:
            scanned.add(key)
            record = adopted
    sums = {} if record.get("taken") else dict(record.get("sums") or {})
    shown = _digest(list(card))

    def post_card(why=""):
        if why:
            log(f"{key}: the message {record.get('ts')} {why}; posting it again")
        ts = slack.post(cid, card[0], card[1], metadata=marked(key))
        _pin(ts)
        return {"ts": ts}

    def _pin(ts):
        if not pin:
            return
        try:
            pin_message(slack, cid, ts)
        except Error as e:  # the message stands unpinned (the bot is not in the channel)
            log(f"{key}: not pinned: {e}")

    fresh = not record.get("ts")
    if fresh:
        record = post_card()
    else:
        if record.get("taken"):
            _pin(record["ts"])
        if sums.get("card") != shown:
            try:
                slack.update(cid, record["ts"], card[0], card[1], metadata=marked(key))
            except Error as e:
                if not gone(e):
                    raise
                delete(slack, cid, record["ts"], ids)
                record, fresh = post_card(f"could not be edited ({e})"), True
    have = [] if fresh else list(record.get("replies") or [])
    if readable and not fresh:
        try:
            have = [r["ts"] for r in replies(slack, cid, record["ts"]) if is_own(r, ids)]
        except Error as e:
            if not gone(e):
                if not _no_history(e):
                    raise
            else:  # deleted by hand
                record, fresh, have = post_card("is gone"), True, []
    known = (sums.get("replies") or []) if not fresh and have == (record.get("replies") or []) else []
    done, sums = [], {"card": shown, "replies": []}
    for i, (text, blocks) in enumerate(thread):
        page = _digest([text, blocks])
        ts = have[i] if i < len(have) else ""
        if ts and (i >= len(known) or known[i] != page):
            try:
                slack.update(cid, ts, text, blocks, metadata=marked(key, i + 1))
            except Error as e:
                if not gone(e):
                    raise
                ts = ""
        if not ts:
            ts = slack.post(cid, text, blocks, thread_ts=record["ts"], metadata=marked(key, i + 1))
        done.append(ts)
        sums["replies"].append(page)
    for ts in have[len(done) :]:
        delete(slack, cid, ts)
    return {"channel": cid, "ts": record["ts"], "replies": done, "sums": sums}
