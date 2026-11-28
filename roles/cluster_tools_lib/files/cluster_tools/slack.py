"""slack: the one client for Slack's Web API, for every tool that tells people something.

    slack = Slack(token, dm_log=DmLog("#alerts"))  # dry_run=True prints instead
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
    unmentioning(text)                      -> (who, text): its inverse, on a text read
    bar(percent)                            -> a text gauge for a code span or table
    pages(title, blocks)                    -> thread replies [(text, blocks)] holding
                                               every block, each within Slack's limits
    keep(slack, channel, key, card, thread, record) -> one message kept up to date, with
                                               its replies (below); the record to keep
    find(slack, channel, key, legacy) -> the bot's own messages marked KEY, newest first
    DailyLog(channel, key, title, logged).record(slack, entry) -> ENTRY's replies under
                                               the day's one message (DmLog: the DMs')

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
logged for the administrators (DmLog: in a daily thread of their channel, no mention,
so the channel's alerts stay on top): they see what people were told, word for word,
and can help. The log entry is built from the very message dm() sent (dm_log_entry), so
the two never differ. The day's message, its count and its replies are Slack's alone,
read from the channel, so every sender on every host (the bot, the usage reports) shares
one day message and one count.

Every failure is an Error naming the method and Slack's reason; Error is a RuntimeError.
Each call is bounded by TIMEOUT seconds. A call Slack rate-limits (HTTP 429) is sent
again once, after the Retry-After it names (at most RETRY_MAX seconds). Interactive features (buttons, forms, Socket
Mode) are the cluster bot's (roles/cluster_bot); this module only sends and reads.
Python 3.9 or newer, standard library only.
"""

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import rich
from .files import count_in_textfile
from .prom import key
from .render import width

API = "https://slack.com/api/"
TIMEOUT = 30
# The longest Retry-After a rate-limited call waits before its one retry (seconds).
RETRY_MAX = 60
# A Slack member ID (users, and workspace-wide W... IDs).
MEMBER = re.compile(r"[UW][A-Z0-9]+")
# A mention mentioning() puts first: a member, a user group or a special one (<!here>).
MENTIONED = re.compile(r"(<(?:@[UW][A-Z0-9]+|!subteam\^[A-Z0-9]+|![a-z]+)(?:\|[^>]*)?>)(?: |$)")
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
# The metadata event_type of a logged DM's replies (payload: kind, login, part), which
# the day message's count is read from.
DM_LOGGED = "cluster_dm_logged"
# How often the count is read again after an edit, while another sender's edit races it.
DM_LOG_RECOUNT = 3


class Error(RuntimeError):
    """Slack refused or could not be reached; str() says which method and why."""


class DailyLog:
    """A record the administrators open only when they want it: in CHANNEL, one message
    a day titled TITLE(day, count), its count kept up to date, and under it each entry
    (a list of replies). Slack holds it all: the day's message is the bot's one marked
    "KEY DAY" (find()), the count is the replies marked LOGGED (event_type), so any number
    of senders, in any process on any host, share them. Two senders that start a day at
    once each post a day message, see both, and keep the older; a count one of them wrote
    over is read again and set right (DM_LOG_RECOUNT). No entry mentions anyone, so the
    channel's alerts stay on top. Needs the history scopes (cluster_bot.scopes reports
    them). The DMs' log is one (DmLog), the bot's deletes another (cluster_bot.remove)."""

    def __init__(self, channel, key, title, logged):
        self.channel, self.key, self.title, self.logged = channel, key, title, logged
        self._cid = None

    def record(self, slack, entry, now=None):
        """ENTRY ([(text, blocks)]) under today's message, in order."""
        now = time.time() if now is None else now
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._cid is None:
            self._cid = channel_id(slack, self.channel)
        cid, start = self._cid, _day_start(now)
        ts = self._day_message(slack, cid, day, start)
        for part, (text, blocks) in enumerate(entry):
            meta = {"event_type": self.logged, "event_payload": {"part": part}}
            slack.post(cid, text, blocks, thread_ts=ts, metadata=meta)
        self._count(slack, cid, ts, day)

    def _day_message(self, slack, cid, day, start):
        """The ts of DAY's message: the bot's oldest marked for it, posted by this call
        when there is none. A second one posted meanwhile by another sender gives way to
        the older, deleted before any reply is under it."""
        key = f"{self.key} {day}"
        found = find(slack, cid, key, oldest=start)
        if found:
            return min(found, key=float)
        mine = slack.call("chat.postMessage", channel=cid, text=self.title(day, 0),
                          metadata=json.dumps(marked(key)))["ts"]  # fmt: skip
        oldest = min(find(slack, cid, key, oldest=start) + [mine], key=float)
        if oldest != mine:
            delete(slack, cid, mine)
        return oldest

    def _count(self, slack, cid, ts, day):
        """Set the day message's count to the entries logged under it, read from Slack,
        and read it again after: a sender that counted earlier and wrote later is outdone."""
        written = None
        for _ in range(DM_LOG_RECOUNT):
            n = sum(1 for r in replies(slack, cid, ts) if self._part(r) == 0)
            if n == written:
                return
            slack.update(cid, ts, self.title(day, n))
            written = n

    def _part(self, message):
        """The part of a logged entry MESSAGE is (0 its first reply), else None."""
        meta = message.get("metadata") or {}
        if meta.get("event_type") != self.logged:
            return None
        return (meta.get("event_payload") or {}).get("part", 0)


class DmLog(DailyLog):
    """The administrators' record of the DMs: each DM as the person got it
    (dm_log_entry), under the day's message "dm-log DAY"."""

    def __init__(self, channel):
        super().__init__(channel, "dm-log", dm_log_title, DM_LOGGED)


def _day_start(now):
    """The Unix time of the local midnight that starts NOW's day."""
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


class DmCounts:
    """What a sender's DMs came to, for Prometheus: the textfile PATH (every sender's in
    node_exporter_unprivileged_dir, root's and the service accounts' alike) holds
    slack_dm_messages_total{sender, kind, result} (result: sent, failed, log_failed) and,
    for a sender that runs on a timer, slack_dm_last_success_timestamp_seconds{sender}: its last
    run, so a sender that stops shows (SlackDmSenderStale). A count that cannot be
    written is said on stderr and never stops the DM."""

    RESULTS = ("sent", "failed", "log_failed")

    def __init__(self, path, sender):
        self.path, self.sender = path, sender

    def add(self, kind, result):
        self._write(add={key("slack_dm_messages_total", sender=self.sender, kind=kind, result=result): 1})

    def ran(self, now=None):
        self._write(
            put={
                key("slack_dm_last_success_timestamp_seconds", sender=self.sender): int(
                    time.time() if now is None else now
                )
            }
        )

    def _write(self, **changes):
        try:
            count_in_textfile(self.path, **changes)
        except OSError as e:
            print(f"slack: the DM counts were not written to {self.path}: {e}", file=sys.stderr)


class Slack:
    def __init__(self, token, dry_run=False, timeout=TIMEOUT, dm_log=None, counts=None):
        self.token, self.dry_run, self.timeout, self.dm_log = token, dry_run, timeout, dm_log
        self.counts = counts  # DmCounts, or None

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

    def granted_scopes(self):
        """The scopes Slack granted this token: auth.test's x-oauth-scopes header. The app's
        settings, not its manifest file, decide them, so a scope a feature needs can be
        missing until the owner imports the manifest again (cluster_bot.scopes)."""
        req = urllib.request.Request(
            API + "auth.test", data=b"", headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                header, body = resp.headers.get("x-oauth-scopes"), json.load(resp)
        except urllib.error.HTTPError as e:
            with e:
                raise Error(f"Slack auth.test failed: {e}") from None
        except (OSError, ValueError) as e:
            raise Error(f"Slack auth.test failed: {e}") from e
        if not isinstance(body, dict) or not body.get("ok"):
            raise Error(f"Slack auth.test failed: {body.get('error') if isinstance(body, dict) else body}")
        if header is None:
            raise Error("Slack auth.test answered without the token's scopes (x-oauth-scopes)")
        return {scope.strip() for scope in header.split(",") if scope.strip()}

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
        return self._send(channel, rich.message(text, blocks or None), thread_ts, metadata)

    def _send(self, channel, msg, thread_ts=None, metadata=None):
        """Post MSG (rich.message: {"text", "blocks"}) as it is; its ts."""
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
        With log and a dm_log (DmLog), the log gets the message as sent, under a line
        naming the kind, the login and a link (dm_log_entry). The log failing is said on
        stderr, never undoes or repeats the sent DM. With counts (DmCounts), each DM counts
        as sent or failed, and a failed log as log_failed."""
        logged = log and self.dm_log is not None
        msg = rich.message(text, blocks or None)
        try:
            channel = self.dm_channel(member)
            ts = self._send(channel, msg)
        except Error:
            self._count(kind, "failed")
            raise
        self._count(kind, "sent")
        print(f"dm {kind} {login} {channel} {ts}")
        if logged and not self.dry_run:
            try:
                link = self.call("chat.getPermalink", channel=channel, message_ts=ts).get("permalink", "")
                self.dm_log.record(self, dm_log_entry(kind, login, msg, link))
            except (Error, OSError) as e:
                self._count(kind, "log_failed")
                print(f"slack: the DM to {login} ({kind}) was sent; logging it failed: {e}", file=sys.stderr)
        return channel, ts

    def _count(self, kind, result):
        if self.counts is not None and not self.dry_run:
            self.counts.add(kind, result)

    def update(self, channel, ts, text, blocks=None, metadata=None):
        msg = rich.message(text, blocks or None)
        params = {"channel": channel, "ts": ts, "text": msg["text"], "blocks": json.dumps(msg["blocks"])}
        if metadata:
            params["metadata"] = json.dumps(metadata)
        return self.call("chat.update", **params)


def dm_log_title(day, count):
    """The day's message the DM lines are threaded under."""
    return f"DM log {day}: {count} sent to people (each in the thread)"


def dm_log_line(kind, login, link=""):
    """The line heading one logged DM: what kind, to whom, a link to it."""
    return f"`{escape(kind)}` to **{escape(login)}**" + (f"  <{link}|open>" if link else "")


def dm_log_entry(kind, login, msg, link=""):
    """The log's replies [(text, blocks)] for the DM MSG (rich.message, as dm() sent it):
    dm_log_line first, then every block of the DM unchanged, split only where Slack's
    limits force it (pages())."""
    head = dm_log_line(kind, login, link)
    return pages(rich.plain(head), rich.blocks(head) + list(msg["blocks"]))


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


def unmentioning(text):
    """(who, the text) of a TEXT mentioning() wrote: the mention it put first, "" when there
    is none. A message read back (tidy, a match on what it says) compares what it says."""
    match = MENTIONED.match(text or "")
    return (match[1], text[match.end() :]) if match else ("", text or "")


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
    """MESSAGE was posted by the bot of IDS (own()): the only messages the token may edit
    or delete. Another of the app's integrations (an incoming webhook) posts with the
    app's app_id but a bot_id of its own, and Slack refuses those (cant_delete_message,
    cant_update_message: chat.delete and chat.update act on the token's bot's messages
    alone); only an admin in the Slack client removes them."""
    bot_id, _app_id = ids
    return bool(bot_id and message.get("bot_id") == bot_id)


def history(slack, channel, oldest=None):
    """Every message in CHANNEL (an ID), newest first, with its metadata; with OLDEST (a
    Unix time) only those since."""
    out, cursor = [], ""
    while True:
        params = {"channel": channel, "limit": 200, "include_all_metadata": "true"}
        if oldest is not None:
            params["oldest"] = f"{oldest:.6f}"
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


def find(slack, channel, key, legacy=None, oldest=None):
    """The ts of the bot's own messages in CHANNEL (an ID) kept as KEY, newest first: marked
    so, or (LEGACY(message) true, for those posted before the mark) unmarked; with OLDEST
    only among those since. Raises Error (missing_scope) without the history scopes."""
    ids = own(slack)
    out = []
    for m in history(slack, channel, oldest):
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
