"""slack: the one client for Slack's Web API, for every tool that tells people something.

    slack = Slack(token)                    # a bot token; dry_run=True prints instead
    slack.post("#infra-status", text, blocks=None, thread_ts=None) -> the message's ts
    slack.update(channel, ts, text, blocks=None)                  -> edits a message
    slack.channel("U0123ABCD")              -> a member's DM channel (names, IDs as is)
    member(slack, user, user_map, email_domain, cache) -> a person's Slack member ID
    slack.call(method, **params)            -> any other method, parsed
    sections(text), table(header, rows)     -> message blocks within Slack's limits
    mentioning(who, text, blocks)           -> (text, blocks) that notify WHO first
    bar(percent)                            -> a text gauge for a code span or table

Every failure is an Error naming the method and Slack's reason; Error is a RuntimeError.
Each call is bounded by TIMEOUT seconds. A call Slack rate-limits (HTTP 429) is sent
again once, after the Retry-After it names (at most RETRY_MAX seconds). Interactive features (buttons, forms, Socket
Mode) are the cluster bot's (roles/cluster_bot); this module only sends and reads.
Python 3.9 or newer, standard library only.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

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


class Error(RuntimeError):
    """Slack refused or could not be reached; str() says which method and why."""


class Slack:
    def __init__(self, token, dry_run=False, timeout=TIMEOUT):
        self.token, self.dry_run, self.timeout = token, dry_run, timeout

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
            print(
                b.get("text", {}).get("text")
                or " | ".join(f["text"] for f in b.get("fields", []))
                or " ".join(e["text"] for e in b.get("elements", []))
                or "-" * 40
            )
        if "blocks" not in params:
            print(params.get("text", ""))
        print()

    def channel(self, target):
        """A channel name or ID as is; a member ID (U...) becomes their DM channel."""
        if MEMBER.fullmatch(target):
            return self.call("conversations.open", users=target)["channel"]["id"]
        return target

    def post(self, channel, text, blocks=None, thread_ts=None):
        params = {"channel": channel, "text": text, "unfurl_links": "false", "unfurl_media": "false"}
        if blocks:
            params["blocks"] = json.dumps(blocks)
        if thread_ts:
            params["thread_ts"] = thread_ts
        return self.call("chat.postMessage", **params)["ts"]

    def update(self, channel, ts, text, blocks=None):
        params = {"channel": channel, "ts": ts, "text": text}
        if blocks:
            params["blocks"] = json.dumps(blocks)
        return self.call("chat.update", **params)


def _retry_after(error):
    """The seconds a 429 asks to wait, within RETRY_MAX; None when it names none."""
    try:
        wait = int(error.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return None
    return wait if 0 <= wait <= RETRY_MAX else None


def escape(text):
    """TEXT shown as itself in a Slack message: &, < and > are Slack's control characters
    (a job name with <!channel> would mention everyone). api.slack.com/reference/surfaces/formatting"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
        return "n/a".ljust(width + 5)
    filled = round(max(0.0, min(pct, 100.0)) / 100 * width)
    return "█" * filled + "░" * (width - filled) + f" {pct:3.0f}%"


def table(header, rows):
    """A fixed-width table in a code block; Slack renders it monospaced. Cells may hold
    what users named (folders, jobs): each is escaped after padding, so the columns line
    up as Slack shows them."""
    widths = [max(len(str(c)) for c in col) for col in zip(header, *rows)]

    def fmt(r):
        return "  ".join(escape(str(c).ljust(w)) for c, w in zip(r, widths)).rstrip()

    return "```\n" + "\n".join([fmt(header)] + [fmt(r) for r in rows]) + "\n```"


def mentioning(who, text, blocks=None):
    """TEXT and BLOCKS with WHO first (a mention: <!subteam^S0123ABCD>, <@U0123ABCD>,
    <!channel>), so Slack notifies them: a message with blocks notifies whom its blocks
    mention, its text is only the fallback. An empty WHO leaves both as they are."""
    if not who:
        return text, blocks
    return f"{who} {text}", (
        [{"type": "section", "text": {"type": "mrkdwn", "text": who}}, *blocks] if blocks else blocks
    )


def sections(text):
    """Slack sections holding all of TEXT. Slack rejects a section over 3000 characters,
    failing the whole post, so a longer text is split at lines into several, a code block
    closed at the end of one and reopened in the next. Nothing is cut: a line left out
    could be the mention that tells an owner about their idle job."""
    chunks, cur, fenced = [], [], False
    width = MAX_SECTION - 8  # room for a code fence closed and reopened around a piece
    pieces = [ln[i : i + width] for ln in text.split("\n") for i in range(0, max(len(ln), 1), width)]
    for line in pieces:
        if cur and len("\n".join(cur + [line])) + 4 > MAX_SECTION:
            chunks.append("\n".join(cur + (["```"] if fenced else [])))
            cur = ["```"] if fenced else []
        cur.append(line)
        if line.startswith("```"):
            fenced = not fenced
    chunks.append("\n".join(cur))
    return [{"type": "section", "text": {"type": "mrkdwn", "text": c}} for c in chunks if c.strip("`\n")]
