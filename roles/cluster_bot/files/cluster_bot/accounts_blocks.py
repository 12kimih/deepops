"""The Slack forms of account requests (cluster_bot.accounts)."""

from cluster_tools import rich  # the cluster's library (PYTHONPATH, roles/cluster_bot)

from .blocks import field


def join_form(teams):
    """The new-account form: login, name, team, SSH public key."""
    return {
        "type": "modal", "callback_id": "join.submit",
        "title": {"type": "plain_text", "text": "계정 신청"},
        "submit": {"type": "plain_text", "text": "신청"},
        "blocks": [
            rich.context("공개키는 내 컴퓨터에서 `ssh-keygen -t ed25519`로 만든 `~/.ssh/id_ed25519.pub`의 내용입니다. "
                "개인키(.pub가 없는 파일)는 절대 보내지 마세요."),
            field("login", "아이디 (영문 소문자로 시작)", {"type": "plain_text_input"}),
            field("name", "이름", {"type": "plain_text_input"}),
            field("team", "팀", {"type": "static_select",
                   "options": [{"text": {"type": "plain_text", "text": t}, "value": t} for t in teams]}),
            field("key", "SSH 공개키", {"type": "plain_text_input", "multiline": True}),
        ],
    }  # fmt: skip


def leave_form(login, may_choose, today):
    """The leave form: the last day, and whose account (an administrator may name anyone)."""
    blocks = [
        rich.section("마지막 날이 지나면 새 잡은 낼 수 없고, 유예 기간 동안 로그인해서 파일을 옮길 수 있습니다. "
            "그 뒤 로그인이 막히고, 파일은 보관 기간 동안 남습니다."),
        field("ends", "마지막 날", {"type": "datepicker", "initial_date": today}),
    ]  # fmt: skip
    if may_choose:
        blocks.append(
            field("login", "정리할 계정의 아이디", {"type": "plain_text_input", "initial_value": login})
        )
    return {
        "type": "modal", "callback_id": "leave.submit", "private_metadata": login,
        "title": {"type": "plain_text", "text": "계정 정리 신청"},
        "submit": {"type": "plain_text", "text": "신청"},
        "blocks": blocks,
    }  # fmt: skip


def leave_reminder(person, days_left):
    """The DM before someone's last day: what stops when, and to move their files away
    while they still can log in. (text, blocks)"""
    when = "내일이" if days_left <= 1 else f"{days_left}일 뒤가"
    text = (
        f"{when} 클러스터 사용 마지막 날입니다 ({person['ends']}). 다음 날부터 새 잡은 낼 수 없고, "
        f"{person['login_expires']}부터는 로그인이 막힙니다. 홈 디렉터리와 공유 저장소에서 필요한 파일은 "
        "그 전에 옮겨 주세요."
    )
    note = "날짜가 바뀌었다면 관리자에게 알려 주세요."
    return "클러스터 사용 종료 안내", [
        rich.section(text),
        rich.context(note),
    ]
