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
an extension) waits for an administrator. What waits says, for the administrator, the
person's cumulative use (the tool's ledger: allocated and used TB-days from their first
allocation) and what they hold now: the fairness they weigh it by.
"""

from .requests import Refused

DAY = 86400
TB_DAY = 1024**4 * DAY


def tb_days(byte_seconds):
    return byte_seconds / TB_DAY


def standing(listing, user):
    """USER's cumulative use and what they hold now, one line (an administrator's reason)."""
    book = (listing.get("ledger") or {}).get(user) or {}
    held = sum(a["bytes"] for a in listing["allocations"] if a["user"] == user and a["state"] != "released")
    return (f"누적 할당 {tb_days(book.get('allocated_byte_seconds', 0)):.1f} TB·일, "
            f"실사용 {tb_days(book.get('used_byte_seconds', 0)):.1f} TB·일, 지금 {held / 1024**4:.1f}TB")  # fmt: skip


def _theirs(listing, user):
    return next((a for a in listing["allocations"] if a["user"] == user and a["state"] != "released"), None)


def decide(request, listing, policy, now):
    verdict, why = _decide(request, listing, policy, now)
    return (verdict, f"{why}; {standing(listing, request['user'])}") if verdict == "admin" else (verdict, why)


def _decide(request, listing, policy, now):
    op, user = request["op"], request["user"]
    entry = _theirs(listing, user)
    if op == "create" and entry:
        raise Refused("이미 스크래치 공간이 있습니다: 크기를 바꾸거나 연장하세요")
    if op != "create" and not entry:
        raise Refused("스크래치 공간이 없습니다")
    if op == "release" or (op == "resize" and request["size"] <= entry["bytes"]):
        return "auto", "반납과 줄이기는 바로 처리됩니다"
    disk = listing["disk"]
    if disk["bytes"] and disk["used"] / disk["bytes"] >= policy["guard"]:
        return "admin", f"스크래치 디스크가 {100 * disk['used'] / disk['bytes']:.0f}% 찼습니다"
    auto = f"{policy['auto_bytes'] / 1024**4:g}TB, 만든 날부터 {policy['auto_days']}일"
    if op == "create":
        if request["size"] > policy["auto_bytes"] or request["days"] > policy["auto_days"]:
            return "admin", f"바로 주는 범위({auto})보다 크거나 깁니다"
        return "auto", f"바로 주는 범위({auto}) 안입니다"
    if op == "resize":
        if request["size"] > policy["auto_bytes"]:
            return "admin", f"바로 주는 범위({auto})보다 큽니다"
        return "auto", f"바로 주는 범위({auto}) 안입니다"
    if op == "extend":
        end = max(entry["expires"], now) + request["days"] * DAY
        if end > entry["created"] + policy["auto_days"] * DAY:
            return "admin", f"바로 주는 범위({auto})를 넘깁니다"
        return "auto", f"바로 주는 범위({auto}) 안입니다"
    raise Refused(f"알 수 없는 요청입니다: {op!r}")
