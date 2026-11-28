"""The Slack form of unban requests (cluster_bot.bans): the request form in Korean (a
person asks), the ban list and the new bans in English (the administrators' channel)."""

from cluster_tools import cards, rich  # the cluster's library (PYTHONPATH, roles/cluster_bot)
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
            field("reason", "상황", {"type": "plain_text_input", "multiline": True}),
        ],
    }  # fmt: skip


def _line(b):
    return f"`{escape(b['ip'])}` on {escape(b['host'])} ({escape(b['jail'])}) since {escape(b['since'])}"


def _summary(bans):
    """The Markdown that sums BANS up: how many on each host, one bullet each, and the
    newest."""
    hosts = {}
    for b in bans:
        hosts[b["host"]] = hosts.get(b["host"], 0) + 1
    newest = max(bans, key=lambda b: b["since"])
    return "\n".join(
        [
            "**By host**",
            *(f"- {escape(h)}: {n}" for h, n in sorted(hosts.items())),
            f"**Newest**: {_line(newest)}",
        ]
    )


def _listing(title, bans):
    """TITLE and BANS as a card (text, blocks: each host's count, the newest) and the
    thread [(text, blocks)] with every ban, newest first, one bullet each, in as many
    replies as Slack's limits need (cluster_tools.slack.pages); none when the card shows
    them all. The administrators' channel: English."""
    bans = sorted(bans, key=lambda b: b["since"], reverse=True)
    blocks = [cards.title(title), *rich.blocks(_summary(bans))]
    if len(bans) < 2:
        return (title, blocks), []
    lines = rich.blocks("\n".join(f"- {_line(b)}" for b in bans))
    return (title, blocks), pages(f"{title}: every ban", lines)


def new_bans(bans):
    """The day log's entry ([(text, blocks)]) of the bans made since the last look
    (cluster_bot.bans.watch): how many, and every one."""
    title = f"New bans: {len(bans)}"
    lines = "\n".join(f"- {_line(b)}" for b in sorted(bans, key=lambda b: b["since"]))
    return pages(title, rich.blocks(f"**{title}**\n{lines}"))


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
    seen. READ: any host was read. COMMAND is the bot's. The administrators' channel:
    English."""
    if bans:
        (text, blocks), thread = _listing(f"Banned addresses: {len(bans)}", bans)
    else:
        text = "Banned addresses: none" if read else "Banned addresses: unread"
        blocks, thread = [cards.title(text)], []
    if errors:
        shown = [
            f"- {escape(hosts)}: {reason}" + (f" (to fix: {fix})" if fix else "")
            for hosts, reason, fix in unread(errors)
        ]
        blocks += cards.part("Hosts not read", "\n".join(shown))
        blocks.append(cards.note("Their bans are as last read."))
    note = f"An unban is asked with `{command} unban` and approved here."
    if thread:
        note = f"Every ban is in this message's thread. {note}"
    blocks.append(cards.note(note))
    return (text, blocks), thread
