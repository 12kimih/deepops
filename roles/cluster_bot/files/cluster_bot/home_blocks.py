"""The bot's Home tab: a person's own view of the cluster, opened from the bot's profile."""

from . import commands
from .blocks import button


def help_text(channel, group):
    """How to ask for help: in CHANNEL, one post and its thread, tagging GROUP (the
    administrators' Slack user group), with a ready-made example."""
    who = group or "관리자"
    return (
        f"*도움 받기*: {channel}에 글을 하나 올리고, 이어지는 이야기는 *그 글의 스레드* 안에 적어 주세요. "
        f"관리자를 부를 때는 {who}을(를) 태그해 주세요.\n"
        "```\n"
        f"{who} 잡이 시작하자마자 끝납니다.\n"
        "- 잡 ID: 123456\n"
        "- 시각: 오늘 14:05\n"
        "- 에러: (메시지를 그대로 붙여넣기)\n"
        "- 해 본 것: --mem 을 늘려 다시 제출\n"
        "```"
    )


def home(login, muted, features, command="/cluster", help_channel="", admin_group=""):
    """LOGIN's Home tab: whether job notices reach them, the subcommands of COMMAND they
    can use (FEATURES: the request kinds this cluster runs; cluster_bot.commands' list)
    and how to ask for help (help_text)."""
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"{login}님의 클러스터"}}]
    state = "받지 않음" if muted else "받음"
    blocks += [
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"*잡 알림*: {state}\n`--mail-type=END,FAIL`을 적은 잡이 끝나면 DM이 옵니다 (10분 안의 알림은 한 메시지로)."},
         "accessory": button("알림 켜기" if muted else "알림 끄기", "home.notices", "on" if muted else "off")},
    ]  # fmt: skip
    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": commands.help_lines(command, features)}]}
    )
    if help_channel:
        blocks += [
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": help_text(help_channel, admin_group)}},
        ]
    return {"type": "home", "blocks": blocks}
