"""Unban requests as a feature of the request engine (cluster_bot.requests): someone
whose address fail2ban banned asks, an administrator approves (bans are for good, so a
person always looks), and the ban is lifted on every host that holds it.

    feature = UnbanFeature({host: Remote(..., SERVICE)})
    feature.where(ip) -> {host: [ban, ...]} where IP is banned now
    watch(feature, post, update, seen)   one round of the administrators' ban watch

Each host runs cluster_tools.bans for SERVICE: list, unban IP.
req: user, ip (as address() writes it), reason.
"""

import ipaddress

from . import bans_blocks
from .requests import Failed, Feature, Refused

# Each banning host's service for the bot (roles/fail2ban offers it by this name).
SERVICE = "bans"


def address(text):
    """TEXT as an IP address (v4 or v6), or None."""
    try:
        return str(ipaddress.ip_address((text or "").strip()))
    except ValueError:
        return None


class UnbanFeature(Feature):
    def __init__(self, hosts):
        self.hosts = hosts

    def survey(self):
        """Every host's bans now: ({host: [ban, ...]}, {host: why it could not be read})."""
        found, errors = {}, {}
        for name, run in sorted(self.hosts.items()):
            try:
                found[name] = run("list")
            except Failed as e:
                errors[name] = str(e)
        return found, errors

    def where(self, ip):
        """The hosts that ban IP now, with their bans; Failed when none could be asked."""
        found, errors = self.survey()
        if errors and not found:
            raise Failed(
                "차단 목록을 읽지 못했습니다 (" + "; ".join(f"{h}: {e}" for h, e in errors.items()) + ")"
            )
        return {h: hits for h, bans in found.items() if (hits := [b for b in bans if b["ip"] == ip])}

    def decide(self, req):
        ip = address(req["ip"])
        if ip is None:
            raise Refused(f"{req['ip']}은(는) IP 주소가 아닙니다")
        found = self.where(ip)
        if not found:
            raise Refused(f"{ip}은(는) 지금 차단되어 있지 않습니다. 접속이 안 되면 도움 채널에 알려 주세요")
        since = min(b["since"] for bans in found.values() for b in bans)
        return "admin", f"{ip} 차단 해제는 관리자가 승인합니다 ({', '.join(found)}, {since}부터 차단)"

    def apply(self, req):
        lifted, errors = [], []
        for name, run in sorted(self.hosts.items()):
            try:
                if run("unban", req["ip"])["unbanned"]:
                    lifted.append(name)
            except Failed as e:
                errors.append(f"{name}: {e}")
        if errors:
            raise Failed(f"{req['ip']} 차단을 다 풀지 못했습니다 (" + "; ".join(errors) + ")")
        where = f" ({', '.join(lifted)})" if lifted else ""
        return f"{req['ip']} 차단을 풀었습니다{where}. 다시 접속해 보세요.", {"hosts": lifted}

    def describe(self, req):
        return f"IP {req['ip']} 차단 해제 ({req.get('reason') or '사유 없음'})"


def watch(feature, post, update, seen):
    """One round of the ban watch: each ban that is new since the last round is told in
    one message (POST(text, blocks, pin=False) -> the message, {channel, ts}), and the
    list of the bans now is one message edited in place (UPDATE(message, text, blocks) ->
    False when it is gone; a new one is posted and pinned). SEEN ({"bans": {key: ban},
    "list": message}, the caller keeps it) is updated. The first round only learns what
    is banned already: nothing is new then. A host that cannot be read keeps the bans it
    had, on the list as last seen, and the list names it."""
    found, errors = feature.survey()
    first = "bans" not in seen
    before = seen.get("bans", {})
    now = {f"{h} {b['jail']} {b['ip']}": {**b, "host": h} for h, bans in found.items() for b in bans}
    now.update({k: b for k, b in before.items() if b["host"] in errors})
    new = sorted(set(now) - set(before), key=lambda k: now[k]["since"])
    if new and not first:
        post(*bans_blocks.new_bans([now[k] for k in new]))
    seen["bans"] = now
    text, blocks = bans_blocks.ban_list(
        sorted(now.values(), key=lambda b: b["since"], reverse=True),
        errors,
        getattr(feature, "command", "/cluster"),
    )
    if not (seen.get("list") and update(seen["list"], text, blocks)):
        seen["list"] = post(text, blocks, pin=True)
