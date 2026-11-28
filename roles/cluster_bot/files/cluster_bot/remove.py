"""The message shortcut that deletes one of the bot's messages (SHORTCUT): Slack lets a
member delete only their own messages, and an app's only a workspace owner or admin, so
the cluster's administrators had no way to remove what the bot posted.

Only a member of the administrators' user group (admin_group_id, read live from Slack
each time) may use it. What it may delete is what Slack lets the bot's token delete,
the rule `cluster-bot tidy` decides by (cluster_tools.slack is_own): the bot's own
message, with the bot's replies under it. An integration's or a person's message is
refused, saying who posted it. A message a feature keeps up to date (marked KEPT: the
ban list, the DM log's day message) is refused too: the feature would post it again,
so it is changed through the feature. A notice post may go: the notices round holds
its notice as announced (cluster_bot.notices SEEN_PATH) and, the post gone, says
"missing" and never posts or pins it again. Every delete is logged for the
administrators like the DMs (cluster_tools.slack.DailyLog, log()): one message a day in
their channel, each delete in its thread, in English, naming who as text (no mention),
where, and the message as it was.
"""

from cluster_tools import rich  # the cluster's library (PYTHONPATH)
from cluster_tools.slack import (
    KEPT,
    DailyLog,
    Error,
    delete,
    escape,
    is_own,
    own,
    pages,
)  # the cluster's library

from .commands import SHORTCUT  # noqa: F401 - the app registers its handler by it

NOT_ADMIN = "관리자 그룹의 사람만 봇의 글을 지울 수 있습니다."
NO_GROUP = "관리자 그룹이 정해지지 않아 아무도 봇의 글을 지울 수 없습니다."
KEPT_TEXT = "이 글은 봇이 늘 최신으로 고쳐 두는 글이라, 지워도 다음 차례에 다시 올라옵니다. 그 기능의 설정으로 바꾸세요."
GONE = "이 글은 이미 없습니다."
DONE = "지웠습니다."
# The delete log's replies' metadata event_type (cluster_tools.slack.DailyLog).
LOGGED = "cluster_delete_logged"


def log_title(day, count):
    """The delete log's day message (English: the administrators' channel)."""
    return f"Deleted bot messages {day}: {count} (each in the thread)"


def log(channel):
    """The delete log in CHANNEL (cluster_tools.slack.DailyLog), None for no channel."""
    return DailyLog(channel, "delete-log", log_title, LOGGED) if channel else None


def log_entry(name, channel, message):
    """The log's replies [(text, blocks)]: a line naming who (NAME, as text), where, and
    the deleted MESSAGE whole, as it showed."""
    head = f"**{escape(name)}** deleted a bot message in <#{channel}>"
    shown = message.get("blocks") or rich.blocks(escape(message.get("text") or "(empty)"))
    return pages(rich.plain(head), rich.blocks(head) + rich.quiet(list(shown)))


def whose(message, ids, known):
    """Who posted MESSAGE, in words, for a refusal: a known integration by its name."""
    if message.get("bot_id") in known:
        return known[message["bot_id"]]
    if message.get("bot_id"):
        return "봇이 아닌 연동 (웹훅 등)" if ids[1] and message.get("app_id") == ids[1] else "다른 앱"
    return "사람"


def decide(message, ids, known):
    """None when the bot may delete MESSAGE, else why not (to say to the person)."""
    if not is_own(message, ids):
        return f"이 글은 봇이 지울 수 없습니다 (작성: {whose(message, ids, known)}). Slack에서는 워크스페이스 관리자만 지울 수 있습니다."
    if (message.get("metadata") or {}).get("event_type") == KEPT:
        return KEPT_TEXT
    return None


def admin(slack, group_id, user):
    """USER is a member of the administrators' user group, as Slack says now."""
    if not group_id:
        return False
    return user in (slack.call("usergroups.users.list", usergroup=group_id).get("users") or [])


def message_at(slack, channel, ts, thread_ts=None):
    """The message TS in CHANNEL as Slack holds it, with its metadata (a shortcut's
    payload carries none), or None when it is gone."""
    params = {"channel": channel, "limit": 1, "inclusive": "true", "include_all_metadata": "true"}
    if thread_ts and thread_ts != ts:
        found = slack.call("conversations.replies", ts=thread_ts, **params, latest=ts).get("messages", [])
    else:
        found = slack.call("conversations.history", latest=ts, oldest=ts, **params).get("messages", [])
    return next((m for m in found if m.get("ts") == ts), None)


def run(slack, *, user, name, channel, ts, thread_ts, group_id, known, deletes):
    """What the shortcut USER (NAME: their login, as the log names them) used on the
    message TS in CHANNEL comes to: the text to tell them (only they see it). Deletes,
    and records it in DELETES (log(); None records nothing), when allowed."""
    if not group_id:
        return NO_GROUP
    if not admin(slack, group_id, user):
        return NOT_ADMIN
    ids = own(slack)
    try:
        message = message_at(slack, channel, ts, thread_ts)
    except Error as e:
        return f"이 글을 읽지 못했습니다: {e}"
    if message is None:
        return GONE
    why = decide(message, ids, known)
    if why:
        return why
    delete(slack, channel, ts, ids)
    if deletes is not None:
        try:
            deletes.record(slack, log_entry(name, channel, message))
        except (Error, OSError) as e:  # deleted all the same: say the log missed it
            return f"{DONE} 다만 삭제 기록을 남기지 못했습니다: {e}"
    return DONE
