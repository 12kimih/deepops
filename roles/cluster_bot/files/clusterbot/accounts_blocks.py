"""The Slack forms of account requests (clusterbot.accounts)."""


def _input(block_id, label, element, optional=False):
    return {"type": "input", "block_id": block_id, "optional": optional,
            "label": {"type": "plain_text", "text": label}, "element": {**element, "action_id": "v"}}  # fmt: skip


def join_form(teams):
    """The new-account form: login, name, team, SSH public key."""
    return {
        "type": "modal", "callback_id": "join.submit",
        "title": {"type": "plain_text", "text": "계정 신청"},
        "submit": {"type": "plain_text", "text": "신청"},
        "blocks": [
            {"type": "context", "elements": [{"type": "mrkdwn", "text":
                "공개키는 내 컴퓨터에서 `ssh-keygen -t ed25519`로 만든 `~/.ssh/id_ed25519.pub`의 내용입니다. "
                "개인키(.pub가 없는 파일)는 절대 보내지 마세요."}]},
            _input("login", "아이디 (영문 소문자로 시작)", {"type": "plain_text_input"}),
            _input("name", "이름", {"type": "plain_text_input"}),
            _input("team", "팀", {"type": "static_select",
                   "options": [{"text": {"type": "plain_text", "text": t}, "value": t} for t in teams]}),
            _input("key", "SSH 공개키", {"type": "plain_text_input", "multiline": True}),
        ],
    }  # fmt: skip


def leave_form(login, may_choose):
    """The leave form: whose account (an administrator may name anyone)."""
    element = {"type": "plain_text_input", "initial_value": login}
    if not may_choose:
        return {
            "type": "modal", "callback_id": "leave.submit", "private_metadata": login,
            "title": {"type": "plain_text", "text": "계정 정리 신청"},
            "submit": {"type": "plain_text", "text": "신청"},
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text":
                f"`{login}` 계정 정리를 신청합니다. 승인되면 종료일 뒤 유예 기간 동안 로그인과 파일 백업만 가능하고, "
                "그 뒤 로그인이 막힙니다. 파일은 보관 기간 동안 남습니다."}}],
        }  # fmt: skip
    return {
        "type": "modal", "callback_id": "leave.submit",
        "title": {"type": "plain_text", "text": "계정 정리 신청"},
        "submit": {"type": "plain_text", "text": "신청"},
        "blocks": [_input("login", "정리할 계정의 아이디", element)],
    }  # fmt: skip
