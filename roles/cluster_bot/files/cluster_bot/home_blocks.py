"""The bot's Home tab: a person's own view of the cluster, opened from the bot's profile."""

from cluster_tools import rich  # the cluster's library (PYTHONPATH, roles/cluster_bot)

from . import commands
from .blocks import button

# The services a person opens in a browser, as the Home tab's buttons: (connect key, label).
LINKS = (("portal_url", "웹 포털"), ("dashboard_url", "Grafana"), ("docs_url", "사용 안내"))


def links(connect):
    """[(label, address)] of the services the bot's config gives an address (connect)."""
    return [(label, connect[key]) for key, label in LINKS if connect.get(key)]


HELP_TITLE = "도움 받기"
NOTICES_TITLE = "잡 알림"
COMMANDS_TITLE = "명령"
# Where a person stands against the rules: the Home tab's part and the /cluster usage
# reply (cluster_bot.app usage_lines), one wording for both.
USAGE_TITLE = "내 현황"
USAGE_NOTE = "자세히 보기: 로그인 노드에서 `usage`"


def help_text(channel, group):
    """How to ask for help: in CHANNEL, one post and its thread, tagging GROUP (the
    administrators' Slack user group), with a ready-made example; under HELP_TITLE (the
    Home tab's header, help_footer's bold line)."""
    # Without an administrators' group there is no one to tag: the line and the tag go.
    tag = f"관리자를 부를 때는 {group}을(를) 태그해 주세요.\n" if group else "\n"
    lead = f"{group} " if group else ""
    return (
        f"{channel}에 글을 하나 올리고, 이어지는 이야기는 **그 글의 스레드** 안에 적어 주세요. "
        + tag
        + "```\n"
        f"{lead}잡이 시작하자마자 끝납니다.\n"
        "- 잡 ID: 123456\n"
        "- 시각: 오늘 14:05\n"
        "- 오류: (메시지를 그대로 붙여넣기)\n"
        "- 해 본 것: --mem을 늘려 다시 제출\n"
        "```"
    )


def help_footer(channel, group):
    """help_text under its title, for a message (the /cluster help reply)."""
    return f"**{HELP_TITLE}**\n{help_text(channel, group)}"


def home(
    login, muted, features, command="/cluster", help_channel="", admin_group="", web=(), cluster=(), usage=()
):
    """LOGIN's Home tab, one part a header (a noun): the services to open in a browser
    (WEB: links()); where they stand against the rules (USAGE: cluster_bot.app
    usage_lines, the CLI's rows); whether job notices reach them; the commands they have
    (FEATURES: the request kinds this cluster runs, and CLUSTER: the cluster commands, as
    cluster_bot.commands groups); how to ask for help (help_text). Everything goes through
    cluster_tools.rich; the collaboration folders follow (projects_blocks.home)."""
    blocks = [rich.header(f"{login}님의 클러스터")]
    if web:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": label}, "url": url, "action_id": f"home.link.{i}"}
            for i, (label, url) in enumerate(web)]})  # fmt: skip
    if usage:
        blocks += [{"type": "divider"}, rich.header(USAGE_TITLE), *rich.blocks("\n".join(usage)),
                   rich.context(USAGE_NOTE)]  # fmt: skip
    state = "받지 않음" if muted else "받음"
    blocks += [
        {"type": "divider"},
        rich.header(NOTICES_TITLE),
        # Beside a button Slack takes only mrkdwn, which cannot show a command before a
        # particle: the state goes there, the explanation in rich text of its own.
        rich.section(f"지금 **{state}**", accessory=button("알림 켜기" if muted else "알림 끄기", "home.notices",
                                                           "on" if muted else "off")),
        *rich.blocks("`--mail-type=END,FAIL`을 적은 잡이 끝나면 DM이 옵니다. 잠깐 사이에 생긴 알림은 한 메시지로 묶어 보냅니다."),
    ]  # fmt: skip
    parts = commands.groups(command, features, cluster)
    if parts:
        blocks += [{"type": "divider"}, rich.header(COMMANDS_TITLE)]
        for title, text in parts:
            blocks += rich.blocks(f"**{title}**\n{text}")
    if help_channel:
        blocks += [
            {"type": "divider"},
            rich.header(HELP_TITLE),
            *rich.blocks(help_text(help_channel, admin_group)),
        ]
    return {"type": "home", "blocks": blocks}
