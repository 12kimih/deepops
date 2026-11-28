"""Block Kit pieces every feature shares: buttons, form fields, and the message for a
held request (cluster_bot.requests), approved or denied with request.approve and
request.deny whatever the feature: in Korean to the people it is held for (a project's
owners, by DM), in English in the administrators' channel (held_admin, decided_admin)."""

from cluster_tools import rich  # the cluster's library (PYTHONPATH, roles/cluster_bot)


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


def held(user_mention, text, key):
    """The message to a person a request is held for (a project's owner, by DM)."""
    return [
        rich.section(f"{user_mention}: {text}"),
        {"type": "actions", "elements": [
            button("승인", "request.approve", key, style="primary"),
            button("거절", "request.deny", key, style="danger"),
        ]},
    ]  # fmt: skip


def held_admin(who, what, key):
    """(text, blocks) of a request held for the administrators, in their channel: WHO
    asked (a login, as text), WHAT (the feature's describe_en)."""
    text = f"Approval needed: {what} (asked by {who})"
    return text, [
        rich.section(f"**Approval needed**: {what}\nAsked by {who}"),
        {"type": "actions", "elements": [
            button("Approve", "request.approve", key, style="primary"),
            button("Deny", "request.deny", key, style="danger"),
        ]},
    ]  # fmt: skip


def decided_admin(outcome, approved, by, what):
    """The administrators' held message once decided: approved or denied BY (a login, as
    text), WHAT was asked, and how it went (OUTCOME, cluster_bot.requests)."""
    if not outcome.request:
        return "Already decided."
    if not approved:
        return f"Denied by {by}: {what}"
    error = outcome.data.get("error")
    return f"Approved by {by}: {what}. " + (f"Failed: {error}" if error else "Done.")
