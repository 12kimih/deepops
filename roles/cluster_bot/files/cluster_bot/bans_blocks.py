"""The Slack form of unban requests (cluster_bot.bans)."""

from cluster_tools.slurm import hostlist  # the cluster's library (PYTHONPATH, roles/cluster_bot)

from .blocks import field


def unban_form():
    """The unban request: the banned address and what happened."""
    return {
        "type": "modal", "callback_id": "unban.submit",
        "title": {"type": "plain_text", "text": "차단 해제 신청"},
        "submit": {"type": "plain_text", "text": "신청"},
        "blocks": [
            {"type": "context", "elements": [{"type": "mrkdwn", "text":
                "로그인에 여러 번 실패하면 그 IP 주소는 클러스터에 접속할 수 없게 됩니다. 차단된 곳(집, 카페 등)의 "
                "공인 IP 주소를 적어 주세요. 그 네트워크에서 웹 브라우저로 \"what is my ip\"를 검색하면 나옵니다. "
                "관리자가 확인한 뒤 풀어 드립니다."}]},
            field("ip", "IP 주소", {"type": "plain_text_input", "placeholder": {"type": "plain_text", "text": "203.0.113.7"}}),
            field("reason", "어떤 상황이었나요?", {"type": "plain_text_input", "multiline": True}),
        ],
    }  # fmt: skip


# The most bans one message lists (a section holds 3000 characters); the rest are counted.
LIST_MAX = 40


def _line(b):
    return f"`{b['ip']}` {b['host']} ({b['jail']}), {b['since']}부터"


def _sections(lines):
    """LINES as sections, each under Slack's 3000 characters."""
    out, chunk = [], []
    for line in lines:
        if chunk and len("\n".join([*chunk, line])) > 2900:
            out.append(chunk)
            chunk = []
        chunk.append(line)
    if chunk:
        out.append(chunk)
    return [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(c)}} for c in out]


def new_bans(bans):
    """The administrators' notice of the bans made since the last look: (text, blocks)."""
    text = f"새로 차단된 주소 {len(bans)}개"
    shown = [_line(b) for b in bans[:LIST_MAX]]
    if len(bans) > LIST_MAX:
        shown.append(f"외 {len(bans) - LIST_MAX}개")
    return text, [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{text}*"}}, *_sections(shown)]


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
    """The bans now, newest first, as the one message kept up to date: (text, blocks).
    ERRORS ({host: Failed}) are the hosts not read this time, by reason with what to do;
    their bans stay as last seen. READ: any host was read. COMMAND is the bot's."""
    if bans:
        text = f"현재 차단된 주소 {len(bans)}개"
    else:
        text = "현재 차단된 주소가 없습니다" if read else "차단 목록을 읽지 못했습니다"
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{text}*"}}]
    lines = [_line(b) for b in bans[:LIST_MAX]]
    if len(bans) > LIST_MAX:
        lines.append(f"외 {len(bans) - LIST_MAX}개 (가장 최근 {LIST_MAX}개만 보입니다)")
    blocks += _sections(lines)
    if errors:
        shown = [
            f"• {hosts}: {reason}." + (f" {fix}." if fix else "") for hosts, reason, fix in unread(errors)
        ]
        blocks += _sections(["*읽지 못한 호스트* (그 호스트의 차단은 마지막으로 읽은 대로입니다)", *shown])
    note = f"해제는 `{command} unban`으로 신청을 받아 승인합니다."
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": note}]})
    return text, blocks
