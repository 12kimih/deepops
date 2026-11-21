"""The Slack messages and forms of scratch allocations, as plain Block Kit data.

Every action id starts with "scratch." so the app routes them with one pattern; a
button's value carries what it acts on (an allocation id, a held request's key).
"""

from .scratch import describe as describe_request
from .scratch import human, summary

__all__ = ["describe_request", "home", "new_form", "number_form", "held", "reminder"]

SIZES = ["100G", "500G", "1T", "2T", "5T", "10T"]


def _button(text, action, value, style=None, confirm=None):
    b = {
        "type": "button",
        "text": {"type": "plain_text", "text": text},
        "action_id": action,
        "value": str(value),
    }
    if style:
        b["style"] = style
    if confirm:
        b["confirm"] = confirm
    return b


def home(allocations):
    """What /scratch shows its user: their allocations with what they can do to each, and
    the way to ask for a new one."""
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "*내 스크래치 공간*"}}]
    live = [a for a in allocations if a["state"] != "released"]
    for a in live:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary(a)}})
        release = {
            "title": {"type": "plain_text", "text": "반납할까요?"},
            "text": {"type": "plain_text", "text": f"{a['path']}와 그 안의 모든 파일이 지워집니다."},
            "confirm": {"type": "plain_text", "text": "반납"},
            "deny": {"type": "plain_text", "text": "취소"},
        }
        blocks.append({"type": "actions", "elements": [
            _button("연장", "scratch.extend", a["id"]),
            _button("크기 변경", "scratch.resize", a["id"]),
            _button("반납", "scratch.release", a["id"], style="danger", confirm=release),
        ]})  # fmt: skip
    if not live:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "배정받은 공간이 없습니다."}]}
        )
    blocks.append(
        {"type": "actions", "elements": [_button("새로 신청", "scratch.new", "new", style="primary")]}
    )
    return blocks


def new_form(policy):
    """The form for a new allocation: size, period, reason."""
    auto = f"{human(policy['auto_bytes'])}, {policy['auto_days']}일 안에서는 바로 배정됩니다. 그보다 크거나 길면 관리자가 승인합니다."
    return {
        "type": "modal", "callback_id": "scratch.create",
        "title": {"type": "plain_text", "text": "스크래치 신청"},
        "submit": {"type": "plain_text", "text": "신청"},
        "blocks": [
            {"type": "context", "elements": [{"type": "mrkdwn", "text": auto}]},
            {"type": "input", "block_id": "size", "label": {"type": "plain_text", "text": "크기"},
             "element": {"type": "static_select", "action_id": "v",
                         "options": [{"text": {"type": "plain_text", "text": s}, "value": s} for s in SIZES]}},
            {"type": "input", "block_id": "days", "label": {"type": "plain_text", "text": "기간 (일)"},
             "element": {"type": "number_input", "action_id": "v", "is_decimal_allowed": False,
                         "min_value": "1", "initial_value": str(policy["auto_days"])}},
            {"type": "input", "block_id": "reason", "label": {"type": "plain_text", "text": "사유 (빠른 I/O가 필요한 이유)"},
             "element": {"type": "plain_text_input", "action_id": "v", "multiline": True}},
        ],
    }  # fmt: skip


def number_form(kind, alloc_id):
    """The form for extending (days) or resizing (size) one allocation."""
    if kind == "extend":
        label, element = (
            "연장할 기간 (일)",
            {"type": "number_input", "action_id": "v", "is_decimal_allowed": False, "min_value": "1"},
        )
    else:
        label = "새 크기"
        element = {"type": "static_select", "action_id": "v",
                   "options": [{"text": {"type": "plain_text", "text": s}, "value": s} for s in SIZES]}  # fmt: skip
    return {
        "type": "modal", "callback_id": f"scratch.{kind}", "private_metadata": str(alloc_id),
        "title": {"type": "plain_text", "text": "스크래치 연장" if kind == "extend" else "크기 변경"},
        "submit": {"type": "plain_text", "text": "요청"},
        "blocks": [{"type": "input", "block_id": "v", "label": {"type": "plain_text", "text": label}, "element": element}],
    }  # fmt: skip


def held(user_mention, text, key):
    """The administrators' message for a request beyond the automatic limits."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{user_mention}: {text}"}},
        {"type": "actions", "elements": [
            _button("승인", "scratch.approve", key, style="primary"),
            _button("거절", "scratch.deny", key, style="danger"),
        ]},
    ]  # fmt: skip


def reminder(a, days_left):
    """The DM before an allocation ends, with the way to extend it."""
    when = "내일" if days_left <= 1 else f"{days_left}일 뒤"
    text = (
        f"스크래치 공간 {summary(a)}이 {when} 만료됩니다. 만료되면 읽기 전용이 되고, 유예 기간 뒤 지워집니다."
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                _button("연장", "scratch.extend", a["id"]),
                _button("반납", "scratch.release", a["id"]),
            ],
        },
    ]
