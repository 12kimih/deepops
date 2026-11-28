"""The DM half of a fake cluster_tools.slack.Slack, for the tests of the tools that send
DMs: a test's fake defines post() (and update() where used) and inherits dm(), which is
post() to the member's DM channel (dm_channel(), the member itself unless overridden)
and records each DM's kind and login in self.dms."""


class FakeDM:
    def dm_channel(self, member):
        return member

    def dm(self, member, text, blocks=None, *, kind, login, log=True):
        channel = self.dm_channel(member)
        ts = self.post(channel, text, blocks)
        if not hasattr(self, "dms"):
            self.dms = []
        self.dms.append((kind, login, log))
        return channel, ts


def refuse_foreign(method, message, bot_id):
    """Slack's answer to chat.delete or chat.update (METHOD) of MESSAGE by the token of
    BOT_ID: they act on that bot's own messages alone, so another's (an incoming
    webhook's, another app's, a person's) raises cant_delete_message or
    cant_update_message. The one rule of every fake, so no test's Slack is kinder."""
    from cluster_tools.slack import Error

    if message.get("bot_id") != bot_id:
        raise Error(f"Slack {method} failed: cant_{method.split('.')[1]}_message")


def pins_call(pins, method, p):
    """pins.add and pins.remove on PINS (the set of pinned ts) as Slack answers them: a
    message pinned twice is already_pinned, one not pinned no_pin. The one pin board of
    every fake, so no test's Slack is kinder than Slack."""
    from cluster_tools.slack import Error

    ts = p["timestamp"]
    if method == "pins.add":
        if ts in pins:
            raise Error("Slack pins.add failed: already_pinned")
        pins.add(ts)
    elif method == "pins.remove":
        if ts not in pins:
            raise Error("Slack pins.remove failed: no_pin")
        pins.discard(ts)
    return {"ok": True}


class FakeChannel:
    """A fake cluster_tools.slack.Slack over channels of messages, for what the bot keeps
    up to date (cluster_tools.slack.keep): posts, replies, edits, deletes and pins, with
    message metadata and the bot's IDs as Slack reports them. A name (#x) is channel "C" +
    x upper-cased. NO_HISTORY: the history scopes are missing (the manifest not imported).
    The bot's posts carry its user (USER), as Slack's do; another poster's carry the USER
    given (none: an incoming webhook's) and what else Slack shows (EXTRA: attachments,
    subtype, username). A post with replies shows reply_count in the history.
    chat.delete and chat.update act on the bot's own messages alone, as Slack's do: any
    other (an incoming webhook's, another app's, a person's) is cant_delete_message or
    cant_update_message. REFUSE: more ts chat.delete refuses (cant_delete_message)."""

    BOT = ("B1", "A1")
    USER = "UBOT"

    def __init__(self, no_history=False):
        self.messages, self.pins, self.log, self.no_history, self.n = [], set(), [], no_history, 0
        self.refuse = set()
        self.usergroups = {}  # usergroup ID -> its members, as usergroups.users.list answers

    @staticmethod
    def cid(channel):
        return "C" + channel[1:].upper() if channel.startswith("#") else channel

    def add(
        self, text, channel="CADMIN", bot=BOT, metadata=None, thread_ts=None, blocks=None, user=None, **extra
    ):
        self.n += 1
        ts = f"{1000 + self.n}.0"
        user = user or (self.USER if bot == self.BOT else None)
        self.messages.append({"ts": ts, "channel": self.cid(channel), "text": text, "blocks": blocks or [],
                              "bot_id": bot[0], "app_id": bot[1], "thread_ts": thread_ts or ts,
                              **({"user": user} if user else {}), **extra,
                              **({"metadata": metadata} if metadata else {})})  # fmt: skip
        return ts

    def at(self, ts):
        return next(m for m in self.messages if m["ts"] == ts)

    def posts(self, channel="CADMIN"):
        """The channel's messages (not replies), oldest first."""
        return [m for m in self.messages if m["channel"] == channel and m["thread_ts"] == m["ts"]]

    def thread(self, ts):
        return [m for m in self.messages if m["thread_ts"] == ts and m["ts"] != ts]

    def did(self, *kinds):
        return [e for e in self.log if e[0] in kinds]

    def _scope(self):
        from cluster_tools.slack import Error

        if self.no_history:
            raise Error("Slack conversations.history failed: missing_scope")

    def call(self, method, **p):
        from cluster_tools.slack import Error

        if method == "chat.getPermalink":
            return {
                "ok": True,
                "permalink": f"https://slack.example/archives/{p['channel']}/p{p['message_ts']}",
            }
        if method == "usergroups.users.list":
            if p["usergroup"] not in self.usergroups:
                raise Error("Slack usergroups.users.list failed: no_such_subteam")
            return {"ok": True, "users": list(self.usergroups[p["usergroup"]])}
        if method == "auth.test":
            return {"ok": True, "bot_id": self.BOT[0], "user_id": self.USER}
        if method == "bots.info":
            return {"ok": True, "bot": {"app_id": self.BOT[1]}}
        if method == "conversations.list":
            names = {m["channel"] for m in self.messages} | {"CADMIN"}
            return {"ok": True, "channels": [{"name": c[1:].lower(), "id": c} for c in names]}
        if method == "conversations.history":
            self._scope()
            shown = sorted(self.posts(p["channel"]), key=lambda m: -float(m["ts"]))
            return {"ok": True, "messages": [{**m, **({"pinned_to": [m["channel"]]} if m["ts"] in self.pins else {}),
                                              **({"reply_count": n} if (n := len(self.thread(m["ts"]))) else {})}
                                             for m in shown]}  # fmt: skip
        if method == "conversations.replies":
            self._scope()
            if not any(m["ts"] == p["ts"] for m in self.messages):
                raise Error("Slack conversations.replies failed: thread_not_found")
            return {"ok": True, "messages": [self.at(p["ts"]), *self.thread(p["ts"])]}
        self.log.append((method, p.get("ts") or p.get("timestamp")))
        if method == "chat.delete":
            if not any(m["ts"] == p["ts"] for m in self.messages):
                raise Error("Slack chat.delete failed: message_not_found")
            if p["ts"] in self.refuse:
                raise Error("Slack chat.delete failed: cant_delete_message")
            refuse_foreign(method, self.at(p["ts"]), self.BOT[0])
            self.messages = [m for m in self.messages if m["ts"] != p["ts"]]
        elif method == "chat.update":
            if not any(m["ts"] == p["ts"] for m in self.messages):
                raise Error("Slack chat.update failed: message_not_found")
            refuse_foreign(method, self.at(p["ts"]), self.BOT[0])
            self.at(p["ts"]).update(text=p.get("text", ""))
        elif method in ("pins.add", "pins.remove"):
            return pins_call(self.pins, method, p)
        elif method == "chat.postMessage":  # the DM log's day message
            import json

            ts = self.add(
                p["text"], p["channel"], metadata=json.loads(p["metadata"]) if p.get("metadata") else None
            )
            return {"ok": True, "ts": ts, "channel": self.cid(p["channel"])}
        return {"ok": True}

    def post(self, channel, text, blocks=None, thread_ts=None, metadata=None, *, cls=None):
        from cluster_tools.slack import CLASSES, Error

        if not thread_ts and cls not in CLASSES:  # as cluster_tools.slack.Slack.post refuses
            raise Error(f"Slack chat.postMessage to {channel}: a top-level post says what it is")
        return self.wire(channel, text, blocks, thread_ts, metadata)

    def wire(self, channel, text, blocks=None, thread_ts=None, metadata=None):
        """chat.postMessage itself, below Slack.post's rule: what Slack._send sends."""
        ts = self.add(text, channel, metadata=metadata, thread_ts=thread_ts, blocks=blocks)
        self.log.append(("post", text))
        return ts

    def update(self, channel, ts, text, blocks=None, metadata=None):
        from cluster_tools.slack import Error

        if not any(m["ts"] == ts for m in self.messages):
            raise Error("Slack chat.update failed: message_not_found")
        refuse_foreign("chat.update", self.at(ts), self.BOT[0])
        self.at(ts).update(text=text, blocks=blocks or [], **({"metadata": metadata} if metadata else {}))
        self.log.append(("update", text))
