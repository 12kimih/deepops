"""The bot's Home tab: a person's own view of the cluster, opened from the bot's profile."""

from .blocks import button
from .scratch import summary


def home(login, allocations, muted, features):
    """LOGIN's Home tab: their scratch allocations, whether job notices reach them, and
    the commands they can use (FEATURES: the request kinds this cluster runs)."""
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"{login}님의 클러스터"}}]
    if "scratch" in features:
        live = [a for a in allocations if a["state"] != "released"]
        lines = "\n".join(summary(a) for a in live) or "배정받은 스크래치 공간이 없습니다."
        blocks += [{"type": "section", "text": {"type": "mrkdwn", "text": f"*스크래치 공간*\n{lines}"}},
                   {"type": "context", "elements": [{"type": "mrkdwn", "text": "`/scratch`로 신청, 연장, 크기 변경, 반납"}]}]  # fmt: skip
    state = "받지 않음" if muted else "받음"
    blocks += [
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"*잡 알림*: {state}\n`--mail-type=END,FAIL`을 적은 잡이 끝나면 DM이 옵니다 (10분 안의 알림은 한 메시지로)."},
         "accessory": button("알림 켜기" if muted else "알림 끄기", "home.notices", "on" if muted else "off")},
    ]  # fmt: skip
    if "join" in features:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "`/leave`: 졸업 등으로 계정 정리 신청"}],
            }
        )
    return {"type": "home", "blocks": blocks}
