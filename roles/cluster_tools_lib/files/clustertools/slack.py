"""slack: the one client for Slack's Web API, for every tool that tells people something.

    slack = Slack(token)                    # a bot token; dry_run=True prints instead
    slack.post("#infra-status", text, blocks=None, thread_ts=None) -> the message's ts
    slack.update(channel, ts, text, blocks=None)                  -> edits a message
    slack.channel("U0123ABCD")              -> a member's DM channel (names, IDs as is)
    member(slack, user, user_map, email_domain, cache) -> a person's Slack member ID
    slack.call(method, **params)            -> any other method, parsed
    sections(text), table(header, rows)     -> message blocks within Slack's limits

Every failure is an Error naming the method and Slack's reason; Error is a RuntimeError.
Each call is bounded by TIMEOUT seconds. Interactive features (buttons, forms, Socket
Mode) are the cluster bot's (roles/cluster_bot); this module only sends and reads.
Python 3.9 or newer, standard library only.
"""

import json
import re
import time
import urllib.parse
import urllib.request

API = "https://slack.com/api/"
TIMEOUT = 30
# A Slack member ID (users, and workspace-wide W... IDs).
MEMBER = re.compile(r"[UW][A-Z0-9]+")
# How long a looked-up member ID is trusted (seconds).
MEMBER_TTL = 86400
MAX_BLOCKS = 45  # Slack allows 50 blocks per message
MAX_SECTION = 2900  # and 3000 characters per section text


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
        req = urllib.request.Request(
            API + method, data=data, headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.load(resp)
        except (OSError, ValueError) as e:
            raise Error(f"Slack {method} failed: {e}") from e
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


def table(header, rows):
    """A fixed-width table in a code block; Slack renders it monospaced."""
    widths = [max(len(str(c)) for c in col) for col in zip(header, *rows)]

    def fmt(r):
        return "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()

    return "```\n" + "\n".join([fmt(header)] + [fmt(r) for r in rows]) + "\n```"


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
