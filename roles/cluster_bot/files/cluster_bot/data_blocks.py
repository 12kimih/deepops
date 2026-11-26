"""The Slack form and messages of shared data requests (cluster_bot.data)."""

import time

from cluster_tools import rich  # the cluster's library (PYTHONPATH, roles/cluster_bot)

from .blocks import button, field

KIND_NAMES = {"datasets": "데이터셋", "models": "모델 가중치", "containers": "컨테이너 이미지"}


def _option(value):
    return {
        "text": {"type": "plain_text", "text": f"{KIND_NAMES.get(value, value)} ({value})"},
        "value": value,
    }


def form(kinds):
    """The request: what, from where, how large, why."""
    text = {"type": "plain_text_input"}
    return {
        "type": "modal", "callback_id": "data.submit",
        "title": {"type": "plain_text", "text": "공유 데이터 신청"},
        "submit": {"type": "plain_text", "text": "신청"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": [
            rich.context("여러 사람이 쓰는 공개 데이터를 한 곳에 한 번만 받아 둡니다. 관리자가 승인하면 공유 계정이 "
                "공개된 https 주소에서 내려받아 두고, 누구나 읽을 수 있습니다. 받은 파일은 출처에 올라온 그대로 둡니다 "
                "(압축 파일은 풀지 않습니다: 작은 파일이 많으면 공유 스토리지가 느려집니다)."),
            field("category", "종류", {"type": "static_select", "options": [_option(k) for k in kinds]}),
            field("name", "이름 (폴더 이름: 영문 소문자, 숫자, . _ -)", {"type": "plain_text_input", "max_length": 64}),
            field("url", "공개된 https 주소", text),
            field("size", "크기 (예: 120G, 1.5T)", text),
            field("sha256", "sha256 (출처가 알려 주면)", text, optional=True),
            field("purpose", "용도", {"type": "plain_text_input", "multiline": True}),
        ],
    }  # fmt: skip


def ended(state, path, entry):
    """The requester's DM when a download ended."""
    if state == "done":
        return f"{path}에 받아 두었습니다 ({entry.get('file')}, {entry.get('bytes', 0)} bytes). 모두 읽을 수 있습니다."
    return (
        f"{path}을(를) 받지 못했습니다: {entry.get('error') or '알 수 없는 이유'}. 질문 채널에 알려 주세요."
    )


def _date(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def notice_dm(key, path, unused_days, removal):
    """The requester's notice: (text, blocks) with the button that keeps it."""
    text = (f"{path}을(를) {unused_days}일 동안 쓴다고 알린 사람이 없습니다. "
            f"계속 쓰시려면 아래 버튼을 눌러 주세요. 아니면 {_date(removal)}에 지웁니다.")  # fmt: skip
    return text, [rich.section(text),
                  {"type": "actions", "elements": [button("계속 사용", "data.use", key, style="primary")]}]  # fmt: skip


def notice_channel(key, path, requester, removal):
    """The help channel's notice: (text, blocks), anyone who uses it presses the button."""
    text = (f"공유 데이터 {path} ({requester} 요청)을(를) {_date(removal)}에 지웁니다. "
            "쓰고 있다면 버튼을 눌러 주세요.")  # fmt: skip
    return text, [rich.section(text),
                  {"type": "actions", "elements": [button("저도 사용", "data.use", key)]}]  # fmt: skip


def removed(path, unused_days, grace_days):
    return (f"{path}을(를) 지웠습니다: {unused_days}일 동안 쓴다는 사람이 없었고, "
            f"알림 뒤 {grace_days}일 안에도 없었습니다.")  # fmt: skip
