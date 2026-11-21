"""Block Kit pieces every feature shares: buttons and the administrators' message for a
held request (clusterbot.requests), approved or denied with request.approve and
request.deny whatever the feature."""


def button(text, action, value, style=None, confirm=None):
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


def held(user_mention, text, key):
    """The administrators' message for a request held for them (any feature)."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{user_mention}: {text}"}},
        {"type": "actions", "elements": [
            button("승인", "request.approve", key, style="primary"),
            button("거절", "request.deny", key, style="danger"),
        ]},
    ]  # fmt: skip
