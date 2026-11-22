"""What a scratch request gets without an administrator.

    decide(request, listing, policy, now) -> ("auto", why) or ("admin", why)

A person has one allocation, /scratch/<login>. A request is {"op": create | resize |
extend | release, "user", and "size" (bytes) or "days"}; listing is the tool's
(cluster_tools.scratch_alloc list: allocations and the disk); policy is {"auto_bytes",
"auto_days", "guard"}. One table decides, every period counted from the day the
allocation was made:

    up to auto_bytes and auto_days        at once
    beyond, up to the hard caps           an administrator approves
    past the hard caps (the tool's)       nobody

Giving space back (release, a smaller size) is always at once. While the disk is at or
past `guard` (a fraction) full, anything that grows (a new allocation, a larger size,
an extension) waits for an administrator.
"""

from .requests import Refused

DAY = 86400


def _theirs(listing, user):
    return next((a for a in listing["allocations"] if a["user"] == user and a["state"] != "released"), None)


def decide(request, listing, policy, now):
    op, user = request["op"], request["user"]
    entry = _theirs(listing, user)
    if op == "create" and entry:
        raise Refused("이미 스크래치 공간이 있습니다: 크기를 바꾸거나 연장하세요")
    if op != "create" and not entry:
        raise Refused("스크래치 공간이 없습니다")
    if op == "release" or (op == "resize" and request["size"] <= entry["bytes"]):
        return "auto", "giving space back is always automatic"
    disk = listing["disk"]
    if disk["bytes"] and disk["used"] / disk["bytes"] >= policy["guard"]:
        return "admin", f"the scratch disk is {100 * disk['used'] / disk['bytes']:.0f}% full"
    if op == "create":
        if request["size"] > policy["auto_bytes"] or request["days"] > policy["auto_days"]:
            return "admin", "larger or longer than what is given at once"
        return "auto", "within what is given at once"
    if op == "resize":
        if request["size"] > policy["auto_bytes"]:
            return "admin", "larger than what is given at once"
        return "auto", "within what is given at once"
    if op == "extend":
        end = max(entry["expires"], now) + request["days"] * DAY
        if end > entry["created"] + policy["auto_days"] * DAY:
            return "admin", f"past {policy['auto_days']} days from when it was made"
        return "auto", f"within {policy['auto_days']} days from when it was made"
    raise Refused(f"unknown request {op!r}")
