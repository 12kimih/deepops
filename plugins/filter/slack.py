"""Slack: the administrators' user group's ID, from Slack's own answer.

    usergroups_response | slack_usergroup_id("@sysadmin")   -> "S0123ABCD", or ""

The answer of usergroups.list (its JSON); the handle with or without its @. "" when the
call failed (ok false, missing_scope, no answer) or no group has that handle: the
notices then mention no one (roles/slack_defaults).
"""


def slack_usergroup_id(response, handle):
    """The ID of the user group HANDLE names in RESPONSE (usergroups.list), or ""."""
    want = str(handle or "").lstrip("@")
    if not want or not isinstance(response, dict) or not response.get("ok"):
        return ""
    for group in response.get("usergroups") or []:
        if isinstance(group, dict) and group.get("handle") == want and group.get("id"):
            return str(group["id"])
    return ""


class FilterModule:
    def filters(self):
        return {"slack_usergroup_id": slack_usergroup_id}
