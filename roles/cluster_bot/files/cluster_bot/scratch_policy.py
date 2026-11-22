"""What a scratch request gets without an administrator.

    decide(request, allocations, policy) -> ("auto", why) or ("admin", why)

A request is {"op": create|resize|extend|release, "user", and "size" (bytes) or "days"
or "id"}; allocations are the registry's entries (cluster_tools.scratch_alloc list);
policy is {"auto_bytes", "auto_days", "auto_extensions"}. Within those a request is
applied at once; beyond them an administrator approves it. Giving space back (release,
a smaller size) is always automatic. Someone may not act on another person's
allocation.
"""

from .requests import Refused


def _mine(allocations, user, alloc_id):
    entry = next((a for a in allocations if str(a["id"]) == str(alloc_id)), None)
    if entry is None:
        raise Refused(f"there is no allocation {alloc_id}")
    if entry["user"] != user:
        raise Refused(f"allocation {alloc_id} is {entry['user']}'s")
    return entry


def held(allocations, user, but=None):
    """The bytes USER's live allocations hold (but one, when resizing it)."""
    return sum(
        a["bytes"] for a in allocations if a["user"] == user and a["state"] != "released" and a["id"] != but
    )


def decide(request, allocations, policy):
    op, user = request["op"], request["user"]
    if op == "release":
        _mine(allocations, user, request["id"])
        return "auto", "giving space back is always automatic"
    if op == "create":
        total = held(allocations, user) + request["size"]
        if request["days"] > policy["auto_days"]:
            return "admin", f"longer than {policy['auto_days']} days"
        if total > policy["auto_bytes"]:
            return "admin", "more than the automatic total for one person"
        return "auto", "within the automatic size and period"
    if op == "resize":
        entry = _mine(allocations, user, request["id"])
        if request["size"] <= entry["bytes"]:
            return "auto", "a smaller size is always automatic"
        if held(allocations, user, but=entry["id"]) + request["size"] > policy["auto_bytes"]:
            return "admin", "more than the automatic total for one person"
        return "auto", "within the automatic total"
    if op == "extend":
        entry = _mine(allocations, user, request["id"])
        if entry.get("extensions", 0) >= policy["auto_extensions"]:
            return "admin", f"already extended {entry.get('extensions', 0)} times"
        if request["days"] > policy["auto_days"]:
            return "admin", f"longer than {policy['auto_days']} days"
        return "auto", "within the automatic extensions"
    raise Refused(f"unknown request {op!r}")
