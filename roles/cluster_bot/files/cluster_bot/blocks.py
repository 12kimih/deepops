"""Block Kit pieces every feature shares: buttons, form fields, and the administrators'
message for a held request (cluster_bot.requests), approved or denied with
request.approve and request.deny whatever the feature."""


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


def field(block_id, label, element, optional=False):
    """A form field. Its value is read back as state.values[BLOCK_ID]["v"]."""
    return {"type": "input", "block_id": block_id, "optional": optional,
            "label": {"type": "plain_text", "text": label}, "element": {**element, "action_id": "v"}}  # fmt: skip


def mentioning(who, text, blocks):
    """TEXT and BLOCKS with WHO first (the administrators' mention, cfg admin_mention), so
    Slack notifies them: a message with blocks notifies whom its blocks mention. The bot's
    counterpart of cluster_tools.slack.mentioning (the bot does not load that library)."""
    if not who:
        return text, blocks
    return f"{who} {text}", [{"type": "section", "text": {"type": "mrkdwn", "text": who}}, *blocks]


def held(user_mention, text, key):
    """The administrators' message for a request held for them (any feature)."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{user_mention}: {text}"}},
        {"type": "actions", "elements": [
            button("승인", "request.approve", key, style="primary"),
            button("거절", "request.deny", key, style="danger"),
        ]},
    ]  # fmt: skip
