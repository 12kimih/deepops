"""The Slack messages and forms of scratch allocations, as plain Block Kit data.

Every action id starts with "scratch."; a button's value carries what it acts on (an
allocation id). Approving a held request is the engine's (cluster_bot.blocks).
"""

from .blocks import button as _button
from .blocks import field
from .scratch import human, summary

SIZES = ["100G", "500G", "1T", "2T", "5T", "10T"]


def _sizes():
    return [{"text": {"type": "plain_text", "text": s}, "value": s} for s in SIZES]


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
            field("size", "크기", {"type": "static_select", "options": _sizes()}),
            field("days", "기간 (일)", {"type": "number_input", "is_decimal_allowed": False, "min_value": "1",
                                       "initial_value": str(policy["auto_days"])}),
            field("reason", "사유 (빠른 I/O가 필요한 이유)", {"type": "plain_text_input", "multiline": True}),
        ],
    }  # fmt: skip


def number_form(kind, alloc_id):
    """The form for extending (days) or resizing (size) one allocation."""
    if kind == "extend":
        label, element = (
            "연장할 기간 (일)",
            {"type": "number_input", "is_decimal_allowed": False, "min_value": "1"},
        )
    else:
        label, element = "새 크기", {"type": "static_select", "options": _sizes()}
    return {
        "type": "modal", "callback_id": f"scratch.{kind}", "private_metadata": str(alloc_id),
        "title": {"type": "plain_text", "text": "스크래치 연장" if kind == "extend" else "크기 변경"},
        "submit": {"type": "plain_text", "text": "요청"},
        "blocks": [field("v", label, element)],
    }  # fmt: skip


def reminder(a, days_left):
    """The DM before an allocation ends, with the way to extend it: (text, blocks)."""
    when = "내일" if days_left <= 1 else f"{days_left}일 뒤"
    text = (
        f"스크래치 공간 {summary(a)}이 {when} 만료됩니다. 만료되면 읽기 전용이 되고, 유예 기간 뒤 지워집니다."
    )
    return "스크래치 공간이 곧 만료됩니다", [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                _button("연장", "scratch.extend", a["id"]),
                _button("반납", "scratch.release", a["id"]),
            ],
        },
    ]
