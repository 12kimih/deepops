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
                made = slack.call(
                    "chat.postMessage", channel=self.channel, text=dm_log_title(state["day"], 0)
                )
                state["ts"], state["channel"] = made["ts"], made["channel"]
            slack.post(state["channel"], line, thread_ts=state["ts"])
            state["count"] += 1
            try:
                slack.update(state["channel"], state["ts"], dm_log_title(state["day"], state["count"]))
            except Error as e:  # the count is cosmetic: the line is in, and so is the state
                print(f"slack: the day's DM count was not updated: {e}", file=sys.stderr)


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
    an owner about their idle job."""
    return rich.blocks(text)
