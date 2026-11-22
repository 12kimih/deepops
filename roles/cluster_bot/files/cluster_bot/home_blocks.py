"""The bot's Home tab: a person's own view of the cluster, opened from the bot's profile."""

from .blocks import button


def home(login, muted, features):
    """LOGIN's Home tab: whether job notices reach them, and the commands they can use
    (FEATURES: the request kinds this cluster runs)."""
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"{login}님의 클러스터"}}]
    state = "받지 않음" if muted else "받음"
    blocks += [
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"*잡 알림*: {state}\n`--mail-type=END,FAIL`을 적은 잡이 끝나면 DM이 옵니다 (10분 안의 알림은 한 메시지로)."},
         "accessory": button("알림 켜기" if muted else "알림 끄기", "home.notices", "on" if muted else "off")},
    ]  # fmt: skip
    commands = [line for kind, line in (("join", "`/leave`: 졸업 등으로 계정 정리 신청"),
                                        ("unban", "`/unban-request`: 로그인 실패로 차단된 IP 주소의 해제 신청"))
                if kind in features]  # fmt: skip
    if commands:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "\n".join(commands)}]})
    return {"type": "home", "blocks": blocks}
