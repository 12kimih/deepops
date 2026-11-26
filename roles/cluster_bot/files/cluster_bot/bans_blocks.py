"""The Slack form of unban requests (cluster_bot.bans)."""

from cluster_tools import rich  # the cluster's library (PYTHONPATH, roles/cluster_bot)
from cluster_tools.slack import escape, pages  # the cluster's library (PYTHONPATH, roles/cluster_bot)
from cluster_tools.slurm import hostlist  # the cluster's library (PYTHONPATH, roles/cluster_bot)

from .blocks import field


def unban_form():
    """The unban request: the banned address and what happened."""
    return {
        "type": "modal", "callback_id": "unban.submit",
        "title": {"type": "plain_text", "text": "차단 해제 신청"},
        "submit": {"type": "plain_text", "text": "신청"},
        "blocks": [
            rich.context("로그인에 여러 번 실패하면 그 IP 주소는 클러스터에 접속할 수 없게 됩니다. 차단된 곳(집, 카페 등)의 "
                "공인 IP 주소를 적어 주세요. 그 네트워크에서 웹 브라우저로 \"what is my ip\"를 검색하면 나옵니다. "
                "관리자가 확인한 뒤 풀어 드립니다."),
            field("ip", "IP 주소", {"type": "plain_text_input", "placeholder": {"type": "plain_text", "text": "203.0.113.7"}}),
            field("reason", "어떤 상황이었나요?", {"type": "plain_text_input", "multiline": True}),
        ],
    }  # fmt: skip


def _line(b):
    return f"`{escape(b['ip'])}` {escape(b['host'])} ({escape(b['jail'])}), {escape(b['since'])}부터"


def _summary(bans):
    """The lines that sum BANS up: how many on each host, and the newest."""
    hosts = {}
    for b in bans:
        hosts[b["host"]] = hosts.get(b["host"], 0) + 1
    newest = max(bans, key=lambda b: b["since"])
    return [
        "호스트별: " + ", ".join(f"{escape(h)} {n}개" for h, n in sorted(hosts.items())),
        f"가장 최근: {_line(newest)}",
    ]


def _listing(title, bans):
    """TITLE and BANS as a card (text, blocks: the count, each host's count, the newest)
    and the thread [(text, blocks)] with every ban, newest first, in as many replies as
    Slack's limits need (cluster_tools.slack.pages); none when the card shows them all."""
    bans = sorted(bans, key=lambda b: b["since"], reverse=True)
    blocks = [rich.section(f"**{title}**"), *rich.blocks("\n".join(_summary(bans)))]
    if len(bans) < 2:
        return (title, blocks), []
    lines = rich.blocks("\n".join(_line(b) for b in bans))
    return (title, blocks), pages(f"{title}: 전체 목록", lines)


def new_bans(bans):
    """The administrators' notice of the bans made since the last look: the card (text,
    blocks) and its thread [(text, blocks)], every ban in it."""
    return _listing(f"새로 차단된 주소 {len(bans)}개", bans)


def unread(errors):
    """ERRORS ({host: Failed}) grouped by reason: [(host list, reason, fix)], the hosts
    of one reason folded into one short list (gpu[1-8],master)."""
    groups = {}
    for host, e in errors.items():
        groups.setdefault((str(e), getattr(e, "fix", "")), []).append(host)
    return sorted(
        ((hostlist(hosts), reason, fix) for (reason, fix), hosts in groups.items()), key=lambda g: g[0]
    )


def by_reason(errors):
    """ERRORS on one line: 'gpu[1-8],master: reason; login: reason'."""
    return "; ".join(f"{hosts}: {reason}" for hosts, reason, _ in unread(errors))


def ban_list(bans, errors, command="/cluster", read=True):
    """The bans now as the one message kept up to date: the card (text, blocks) sums them
    up, the thread [(text, blocks)] lists every one, newest first. ERRORS ({host: Failed})
    are the hosts not read this time, by reason with what to do; their bans stay as last
    seen. READ: any host was read. COMMAND is the bot's."""
    if bans:
        (text, blocks), thread = _listing(f"현재 차단된 주소 {len(bans)}개", bans)
    else:
        text = "현재 차단된 주소가 없습니다" if read else "차단 목록을 읽지 못했습니다"
        blocks, thread = [rich.section(f"**{text}**")], []
    if errors:
        shown = [
            f"- {escape(hosts)}: {reason}." + (f" {fix}." if fix else "")
            for hosts, reason, fix in unread(errors)
        ]
        blocks += rich.blocks(
            "\n".join(["**읽지 못한 호스트** (그 호스트의 차단은 마지막으로 읽은 대로입니다)", *shown])
        )
    note = f"해제는 `{command} unban` 신청을 받아 승인합니다."
    if thread:
        note = f"전체 목록은 이 메시지의 스레드에 있습니다. {note}"
    blocks.append(rich.context(note))
    return (text, blocks), thread
