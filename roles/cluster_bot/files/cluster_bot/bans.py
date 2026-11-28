"""Unban requests as a feature of the request engine (cluster_bot.requests): someone
whose address fail2ban banned asks, an administrator approves (bans are for good, so a
person always looks), and the ban is lifted on every host that holds it.

    feature = UnbanFeature({host: Remote(..., SERVICE)})
    feature.where(ip) -> {host: [ban, ...]} where IP is banned now
    watch(feature, slack, channel, seen)   one round of the administrators' ban watch

Each host runs cluster_tools.bans for SERVICE: list, unban IP.
req: user, ip (as address() writes it), reason.
"""

import ipaddress
import sys

from cluster_tools.slack import DailyLog, Error, keep  # the cluster's library (PYTHONPATH)

from . import bans_blocks
from .requests import Failed, Feature, Refused

# Each banning host's service for the bot (roles/fail2ban offers it by this name).
SERVICE = "bans"
# The ban list's key among the messages the bot keeps (cluster_tools.slack.keep).
LIST = "bans"
# The new bans' day log (cluster_tools.slack.DailyLog): its key and its replies' event_type.
LOG, LOGGED = "ban-log", "cluster_ban_logged"


def log_title(day, count):
    """The new bans' day message (English: the administrators' channel)."""
    return f"Ban log {day}: {count} rounds with new bans (each in the thread)"


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
        """Every host's bans now: ({host: [ban, ...]}, {host: the Failed it could not be
        read for})."""
        found, errors = {}, {}
        for name, run in sorted(self.hosts.items()):
            try:
                found[name] = run("list")
            except Failed as e:
                errors[name] = e
        return found, errors

    def where(self, ip):
        """The hosts that ban IP now, with their bans; Failed when none could be asked."""
        found, errors = self.survey()
        if errors and not found:
            raise Failed(f"could not read the bans ({bans_blocks.by_reason(errors)})")
        return {h: hits for h, bans in found.items() if (hits := [b for b in bans if b["ip"] == ip])}

    def decide(self, req):
        ip = address(req["ip"])
        if ip is None:
            raise Refused(f"{req['ip']}은(는) IP 주소가 아닙니다")
        found = self.where(ip)
        if not found:
            raise Refused(f"{ip}은(는) 지금 차단되어 있지 않습니다. 접속이 안 되면 질문 채널에 알려 주세요")
        since = min(b["since"] for bans in found.values() for b in bans)
        return (
            "admin",
            f"{ip} 차단 해제는 관리자가 승인합니다 ({bans_blocks.hostlist(found)}, {since}부터 차단)",
        )

    def apply(self, req):
        lifted, errors = [], {}
        for name, run in sorted(self.hosts.items()):
            try:
                if run("unban", req["ip"])["unbanned"]:
                    lifted.append(name)
            except Failed as e:
                errors[name] = e
        if errors:
            raise Failed(f"could not lift every ban of {req['ip']} ({bans_blocks.by_reason(errors)})")
        where = f" ({bans_blocks.hostlist(lifted)})" if lifted else ""
        return f"{req['ip']} 차단을 풀었습니다{where}. 다시 접속해 보세요.", {"hosts": lifted}

    def describe(self, req):
        return f"IP {req['ip']} 차단 해제 ({req.get('reason') or '사유 없음'})"

    def describe_en(self, req):
        return (
            f"unban IP {req['ip']} ("
            + (f"reason: {req['reason']}" if req.get("reason") else "no reason given")
            + ")"
        )


def _old_list(message):
    """A ban list from before the lists were marked (cluster_tools.slack.keep)."""
    text = message.get("text") or ""
    return text.startswith("현재 차단된 주소") or text == "차단 목록을 읽지 못했습니다"


def watch(feature, slack, channel, seen, log=None):
    """One round of the ban watch in CHANNEL through SLACK (cluster_tools.slack.Slack).
    The bans new since the last round go in the day's ban log (a DailyLog: one message a
    day, each round's new bans in its thread, mentioning no one: a ban needs nobody to
    act, an unban comes as a request to approve). The bans now are one message kept up to
    date (cluster_tools.slack.keep: found in the channel, never posted twice, pinned): a
    summary, and every ban in its thread. SEEN ({"bans": {key: ban}, "list": the kept
    record, "read": the hosts read once}, the caller keeps it) is updated. A host's first
    read only learns what it bans already: nothing of it is new then, whether that is the
    watch's first round or the first since the host could be reached (a round that read no
    host learned nothing, so the next one told every ban as new). A host that cannot be
    read keeps the bans it had, on the list as last seen, and the list names it."""
    log = log or (lambda line: print(f"cluster-bot: ban watch: {line}", file=sys.stderr))
    found, errors = feature.survey()
    before = seen.get("bans", {})
    # A record from before "read": the hosts it holds bans of were read.
    read = set(seen.get("read", {b["host"] for b in before.values()}))
    now = {f"{h} {b['jail']} {b['ip']}": {**b, "host": h} for h, bans in found.items() for b in bans}
    now.update({k: b for k, b in before.items() if b["host"] in errors})
    new = sorted((k for k in set(now) - set(before) if now[k]["host"] in read), key=lambda k: now[k]["since"])
    seen["read"] = sorted(read | set(found))
    if new:
        try:
            DailyLog(channel, LOG, log_title, LOGGED).record(
                slack, bans_blocks.new_bans([now[k] for k in new])
            )
        except Error as e:  # the bans are seen all the same: the list below holds them
            log(f"the new bans were not logged: {e}")
    seen["bans"] = now
    card, thread = bans_blocks.ban_list(
        list(now.values()), errors, getattr(feature, "command", "/cluster"), read=bool(found)
    )
    try:
        seen["list"] = keep(
            slack, channel, LIST, card, thread, seen.get("list"), legacy=_old_list, pin=True, log=log
        )
    except Error as e:  # the bans seen are kept: the next round tells nothing twice
        log(f"the list was not brought up to date: {e}")
