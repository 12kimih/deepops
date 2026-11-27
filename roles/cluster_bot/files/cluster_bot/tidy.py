"""The owner's tidy of the channels the bot posts in: the messages today's code no longer
manages, deleted after a dry run. A survey comes first, so it is plain which integration
posted what:

    python -m cluster_bot.tidy CONFIG --dry-run   the survey, then each delete and why
    python -m cluster_bot.tidy CONFIG             the same, and the deletes done

The survey groups each channel's messages by who posted them (bot_id, app_id, user,
name, subtype): how many, from when to when, with how many replies, and whose they are,
an integration the config knows by its name (tidy.known: bot_id -> what it is). What
goes is what RULES name, one row each: the channel (by its role in slack_channels), what
the message is, a matcher and why it no longer belongs.

What happens to a matched message is decided before anything is tried, in the dry run
as in the real one, by Slack's rule: chat.delete acts on the token's bot's own messages
alone (cluster_tools.slack is_own). The bot's are deleted, with its replies after them
(someone else's reply stays, under Slack's "deleted" mark). An integration's (an
incoming webhook of the app, another app) is "by hand": listed once per channel with a
link to each, for an admin to delete in the Slack client, and never attempted. A
person's is left. A delete Slack still refuses (cant_delete_message) is said, to delete
by hand. The deletes are paced to Slack's rate for chat.delete; a call it still limits
is sent again (cluster_tools.slack).

A notice an integration posted (a retired notice webhook's copy) is recorded as
announced in the notices round's record (cluster_bot.notices SEEN_PATH, under its lock)
when the round has none, with no ts: the round counts it as done ("missing" once the
copy is deleted by hand) and never posts or pins it again. The dry run records nothing.
"""

import argparse
import json
import re
import sys
import time
from collections import namedtuple
from pathlib import Path

from cluster_tools import rich  # the cluster's library (PYTHONPATH, roles/cluster_bot)
from cluster_tools.slack import (  # the cluster's library (PYTHONPATH, roles/cluster_bot)
    Error,
    Slack,
    channel_id,
    delete,
    history,
    is_own,
    own,
    own_user,
    replies,
    unmentioning,
)

from . import notices, state

DELETE, BY_HAND, LEFT = "delete", "by hand", "left"

# Slack's chat.delete is a Tier 3 method (about 50 a minute): one call this often.
PAUSE = 1.2
REFUSED = ("cant_delete_message",)

# Alertmanager's own title for Slack ([FIRING:2] ...), before the footer was set.
ALERT_TITLE = re.compile(r"^\[(FIRING|RESOLVED)(:\d+)?\]")
# A ban notice of the old format: its list cut at LIST_MAX bans, "외 N개" (N more).
CUT_LIST = re.compile(r"외 \d+개")


def _alert_by_webhook(m, ctx):
    """An Alertmanager alert (its footer, or its default title) that the bot user did not
    post: posted through an incoming webhook, which Alertmanager never edits. With the bot
    user unknown, none (Alertmanager's own messages would look the same)."""
    alert = any(
        a.get("footer") == "Alertmanager" or ALERT_TITLE.match(a.get("title") or a.get("fallback") or "")
        for a in m.get("attachments") or []
    )
    return alert and bool(ctx.user) and m.get("user") != ctx.user


def _old_ban_notice(m, ctx):
    """A new-bans notice whose list was cut in the message ("외 N개"), with no thread; its
    text after the admin mention (cluster_tools.slack.mentioning) names it."""
    text = unmentioning(m.get("text"))[1]
    shown = json.dumps(m.get("blocks") or [], ensure_ascii=False)
    return text.startswith("새로 차단된 주소") and bool(CUT_LIST.search(shown)) and not m.get("metadata")


def _integration_notice(m, ctx):
    """A notice post (cluster_bot.notices.identify) that the bot did not post: the round
    leaves those as another's ("foreign"), and keeps its own."""
    return ctx.notice(m) is not None and not is_own(m, ctx.ids)


Rule = namedtuple("Rule", "channel what match why")
RULES = (
    Rule("admin", "an alert", _alert_by_webhook,
         "posted through an incoming webhook, never edited when it resolved; "
         "Alertmanager now posts with the bot and edits its message in place"),
    Rule("admin", "a ban notice", _old_ban_notice,
         "the old format, its list cut; the kept ban list and its thread hold every ban"),
    Rule("announce", "a notice", _integration_notice,
         "posted by an integration, not the bot; the notices round keeps the bot's own posts"),
)  # fmt: skip


class Context:
    """What the matchers read: the bot's IDS (own()) and USER (own_user()), the KNOWN
    integrations ({bot_id: what it is}), and the notices (notices.notices_of) with the
    round's record SEEN, kept at SEEN_PATH."""

    def __init__(self, ids, user, known=None, notes=None, seen=None, seen_path=None):
        self.ids, self.user, self.known = ids, user, dict(known or {})
        self.notes, self.seen, self.seen_path = notes or {}, seen or {}, seen_path
        self._recorded = {e["ts"]: nid for nid, e in self.seen.items() if isinstance(e, dict) and e.get("ts")}
        self._sites = {n[: n.index("/news/") + len("/news/")] for n in self.notes if "/news/" in n}

    def ours(self, m):
        """M is the bot's own: the only messages the token may delete."""
        return is_own(m, self.ids)

    def fate(self, m):
        """What becomes of M when a rule matches it: DELETE (the bot's), BY_HAND (an
        integration's: Slack lets only an admin in its client delete it), LEFT (a
        person's)."""
        if self.ours(m):
            return DELETE
        return BY_HAND if m.get("bot_id") else LEFT

    def whose(self, m):
        """Who M is, in words: the bot, a known integration by its name, the app's other
        integration (an incoming webhook), another app, a person."""
        if self.ours(m):
            return "the bot's"
        if m.get("bot_id") in self.known:
            return self.known[m["bot_id"]]
        if m.get("bot_id"):
            return (
                "the app's, not its bot"
                if self.ids[1] and m.get("app_id") == self.ids[1]
                else "another app's"
            )
        return "a person's"

    def notice(self, m):
        return notices.identify(m, self.notes, self._recorded, self._sites)[0]


def poster(m):
    """Who posted M, as the survey groups it: (bot_id, app_id, user, name, subtype)."""
    name = m.get("username") or (m.get("bot_profile") or {}).get("name") or ""
    return tuple(m.get(k) or "" for k in ("bot_id", "app_id", "user")) + (name, m.get("subtype") or "")


def title(m):
    """What M says first, to name it: the first line of its blocks, else of an attachment's
    title, fallback or text (an incoming webhook's, Alertmanager's), else of its text; a
    leading mention left out. "" only for a message that shows nothing."""
    attachments = m.get("attachments") or []
    for said in (rich.read(m.get("blocks")), *(a.get(k) for k in ("title", "fallback", "text") for a in attachments),
                 m.get("text")):  # fmt: skip
        for line in (said or "").splitlines():
            line = unmentioning(line.strip())[1].strip()
            if line:
                return line
    return ""


def day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(float(ts)))


def survey(messages, ctx):
    """The channel's MESSAGES by poster(): [line], the most first, each ending with its
    newest message's title(), so an integration nobody knows can be told by what it says."""
    groups = {}
    for m in messages:
        g = groups.setdefault(poster(m), {"n": 0, "ts": [], "replies": 0, "whose": ctx.whose(m), "newest": m})
        g["n"] += 1
        g["ts"].append(float(m["ts"]))
        g["replies"] += m.get("reply_count") or 0
        if float(m["ts"]) > float(g["newest"]["ts"]):
            g["newest"] = m
    out = []
    for key, g in sorted(groups.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        who = " ".join(f"{k}={v}" for k, v in zip(("bot_id", "app_id", "user", "name", "subtype"), key) if v)
        out.append(f"{who or 'unknown'}: {g['n']} messages, {day(min(g['ts']))} to {day(max(g['ts']))}, "
                   f"{g['replies']} replies, {g['whose']}, newest: {title(g['newest'])[:80]!r}")  # fmt: skip
    return out


def plan(role, messages, ctx):
    """What RULES make of the posts in the channel of ROLE: [{"do", "ts", "what", "why",
    "by", "whose", "replies", "day", "text"}], oldest first, do as Context.fate says. A
    notice of an integration the round has no record of carries "record": its id, to
    record as announced (record_notices)."""
    out = []
    for m in sorted(messages, key=lambda m: float(m["ts"])):
        if m.get("thread_ts", m["ts"]) != m["ts"]:
            continue  # a reply goes with its parent
        rule = next((r for r in RULES if r.channel == role and r.match(m, ctx)), None)
        if rule is None:
            continue
        a = {"do": ctx.fate(m), "ts": m["ts"], "what": rule.what, "why": rule.why, "by": poster(m),
             "whose": ctx.whose(m), "replies": m.get("reply_count") or 0, "day": day(m["ts"]),
             "text": title(m)[:80]}  # fmt: skip
        if a["do"] == BY_HAND:
            a["why"] += "; Slack lets only an admin delete it, in the Slack client"
        elif a["do"] == LEFT:
            a["why"] = "a person's: never touched"
        if rule.match is _integration_notice and ctx.notice(m) not in ctx.seen:
            a["record"] = ctx.notice(m)
        out.append(a)
    return out


def describe(a, extra=""):
    by = "/".join(v for v in a["by"][:3] if v) or "?"
    return (f"{a['do']} {a['ts']} ({a['day']}, by {by}, {a['whose']}) {a['what']} {a['text']!r}: "
            f"{a['why']}{extra}")  # fmt: skip


def permalink(slack, channel, ts):
    """The link an admin opens to delete TS by hand; "" when Slack gives none."""
    try:
        return slack.call("chat.getPermalink", channel=channel, message_ts=ts).get("permalink", "")
    except Error:
        return ""


def record_notices(ctx, actions, log, dry_run):
    """Record each notice ACTIONS name (plan's "record") as announced in the round's
    record, under its lock, with no ts: the round never posts or pins it again."""
    todo = {}
    for a in actions:
        if a.get("record"):
            todo.setdefault(a["record"], a)  # the oldest copy names it
    for nid, a in sorted(todo.items()):
        n = ctx.notes.get(nid) or {}
        if dry_run or not ctx.seen_path:
            log(f"  would record {nid} as announced: an integration's copy stands ({a['ts']})")
            continue
        with state.locked(ctx.seen_path, {}) as seen:
            if nid not in seen:
                seen[nid] = {"date": n.get("date", a["day"]), "digest": n.get("digest", ""),
                             "copy": "an integration's copy, to delete by hand"}  # fmt: skip
                log(f"  recorded {nid} as announced: the notices round will not post it again")


def thread_of(slack, channel, a, ctx):
    """(the app's replies under A to delete, how many replies are someone else's)."""
    if not a["replies"]:
        return [], 0
    found = replies(slack, channel, a["ts"])
    mine = [r["ts"] for r in found if ctx.ours(r)]
    return mine, len(found) - len(mine)


def remove(slack, channel, ts, log, pause, a):
    """Delete TS; a refusal is said and False."""
    try:
        delete(slack, channel, ts)
    except Error as e:
        if not any(code in str(e) for code in REFUSED):
            raise
        log(describe(a, f": Slack refused ({e}); delete it by hand"))
        return False
    finally:
        pause(PAUSE)
    return True


def tidy(slack, channels, ctx, log, dry_run=True, pause=time.sleep):
    """Survey and tidy CHANNELS ({role: name or ID}), each line to LOG; DRY_RUN changes
    nothing. Returns the actions, "done" set on each delete carried out."""
    out, roles_of = [], {}
    for role, name in sorted(channels.items()):
        if name:  # two roles may name one channel: it is read once, with the rules of both
            roles_of.setdefault(channel_id(slack, name), []).append(role)
    for cid, roles in roles_of.items():
        name = channels[roles[0]]
        try:
            messages = history(slack, cid)
        except Error as e:
            if "missing_scope" not in str(e):
                raise
            log(f"{name}: the history is not readable ({e}): import the app manifest")
            continue
        log(f"{name} ({', '.join(roles)}): {len(messages)} messages")
        for line in survey(messages, ctx):
            log(f"  {line}")
        actions = [a for r in roles for a in plan(r, messages, ctx)]
        for a in actions:
            a["channel"] = name
            if a["do"] == BY_HAND:
                continue
            mine, theirs = thread_of(slack, cid, a, ctx) if a["do"] == DELETE else ([], 0)
            extra = f", with {len(mine)} replies of the bot" if mine else ""
            extra += f"; {theirs} replies of others stay" if theirs else ""
            if dry_run or a["do"] != DELETE:
                log(f"  {describe(a, extra)}")
                continue
            if remove(slack, cid, a["ts"], log, pause, a):
                for ts in mine:
                    remove(slack, cid, ts, log, pause, {**a, "ts": ts, "what": "its reply"})
                a["done"] = True
                log(f"  {describe(a, extra).replace('delete', 'deleted', 1)}")
        by_hand = [a for a in actions if a["do"] == BY_HAND]
        if by_hand:
            log(f"  by hand in the Slack client (an admin), {len(by_hand)} messages:")
            for a in by_hand:
                a["link"] = permalink(slack, cid, a["ts"])
                log(f"    {a['link'] or a['ts']}  {describe(a)}")
        record_notices(ctx, actions, log, dry_run)
        out += actions
    return out


def context(slack, cfg, log, fetch=notices.fetch_json):
    """The Context of the bot's config CFG: its IDs, the known integrations, the notices."""
    docs = cfg.get("notices") or {}
    notes = {}
    if docs.get("url"):
        try:
            notes = notices.notices_of(fetch(docs["url"]))
        except (OSError, ValueError) as e:
            log(f"notices.json not read ({e}): no notice post is deleted")
    seen = state.read(docs["seen"], {}) if docs.get("seen") else {}
    known = (cfg.get("tidy") or {}).get("known") or {}
    return Context(own(slack), own_user(slack), known, notes, seen, docs.get("seen"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m cluster_bot.tidy", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    parser.add_argument("config", help="the bot's config.json")
    parser.add_argument("--dry-run", action="store_true", help="the survey and each delete; change nothing")
    args = parser.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text())
    channels = (cfg.get("tidy") or {}).get("channels") or {}
    if not channels:
        parser.error(f"{args.config}: no tidy channels")
    slack = Slack(Path(cfg["bot_token_file"]).read_text().strip())
    log = lambda line: print(f"tidy: {line}")  # noqa: E731
    actions = tidy(slack, channels, context(slack, cfg, log), log, dry_run=args.dry_run)
    log(summary(actions, args.dry_run))
    return 0


def summary(actions, dry_run):
    """The run's last line: what it would do, what it did, or that it changed nothing."""
    deletes = [a for a in actions if a["do"] == DELETE]
    hand = sum(1 for a in actions if a["do"] == BY_HAND)
    left = len(actions) - len(deletes) - hand
    rest = f"{hand} to delete by hand, {left} left"
    if dry_run:
        return f"would delete {len(deletes)} messages, {rest}; nothing changed"
    done = sum(1 for a in deletes if a.get("done"))
    if not done:
        return f"nothing deleted: {len(deletes)} to delete, {rest}"
    return f"deleted {done} of {len(deletes)} messages, {rest}"


if __name__ == "__main__":
    sys.exit(main())
