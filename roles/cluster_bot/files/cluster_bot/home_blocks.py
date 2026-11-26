"""The bot's Home tab: a person's own view of the cluster, opened from the bot's profile."""

from cluster_tools import rich  # the cluster's library (PYTHONPATH, roles/cluster_bot)

from . import commands
from .blocks import button

# The services a person opens in a browser, as the Home tab's buttons: (connect key, label).
LINKS = (("portal_url", "웹 포털"), ("dashboard_url", "Grafana"), ("docs_url", "사용 안내"))


def links(connect):
    """[(label, address)] of the services the bot's config gives an address (connect)."""
    return [(label, connect[key]) for key, label in LINKS if connect.get(key)]


def help_text(channel, group):
    """How to ask for help: in CHANNEL, one post and its thread, tagging GROUP (the
    administrators' Slack user group), with a ready-made example."""
    # Without an administrators' group there is no one to tag: the line and the tag go.
    tag = f"관리자를 부를 때는 {group}을(를) 태그해 주세요.\n" if group else "\n"
    lead = f"{group} " if group else ""
    return (
        f"**도움 받기**: {channel}에 글을 하나 올리고, 이어지는 이야기는 **그 글의 스레드** 안에 적어 주세요. "
        + tag
        + "```\n"
        f"{lead}잡이 시작하자마자 끝납니다.\n"
        "- 잡 ID: 123456\n"
        "- 시각: 오늘 14:05\n"
        "- 오류: (메시지를 그대로 붙여넣기)\n"
        "- 해 본 것: --mem을 늘려 다시 제출\n"
        "```"
    )


def home(
    login, muted, features, command="/cluster", help_channel="", admin_group="", web=(), cluster=(), usage=()
):
    """LOGIN's Home tab: the services to open in a browser (WEB: links()), whether job
    notices reach them, the subcommands of COMMAND they can use (FEATURES: the request
    kinds this cluster runs; cluster_bot.commands' list) and how to ask for help
    (help_text)."""
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"{login}님의 클러스터"}}]
    if web:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": label}, "url": url, "action_id": f"home.link.{i}"}
            for i, (label, url) in enumerate(web)]})  # fmt: skip
    state = "받지 않음" if muted else "받음"
    blocks += [
        {"type": "divider"},
        rich.section(f"**잡 알림**: {state}\n`--mail-type=END,FAIL`을 적은 잡이 끝나면 DM이 옵니다 (잠깐 사이에 생긴 알림은 한 메시지로 묶어 보냅니다).", accessory=button("알림 켜기" if muted else "알림 끄기", "home.notices", "on" if muted else "off")),
    ]  # fmt: skip
    if usage:  # the same rows as the usage command (cluster_bot.app usage_lines)
        blocks += [{"type": "divider"}, rich.section("\n".join(usage))]
    blocks.append(rich.context(commands.help_lines(command, features, cluster=cluster)))
    if help_channel:
        blocks += [
            {"type": "divider"},
            rich.section(help_text(help_channel, admin_group)),
        ]
    return {"type": "home", "blocks": blocks}
