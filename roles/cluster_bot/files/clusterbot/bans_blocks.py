"""The Slack form of unban requests (clusterbot.bans)."""

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
