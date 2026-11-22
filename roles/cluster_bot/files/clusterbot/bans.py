"""Unban requests as a feature of the request engine (clusterbot.requests): someone
whose address fail2ban banned asks, an administrator approves (bans are for good, so a
person always looks), and the ban is lifted on every host that holds it.

    feature = UnbanFeature({host: Remote(..., SERVICE)})
    feature.where(ip) -> {host: [ban, ...]} where IP is banned now

Each host runs clustertools.bans for SERVICE: list, unban IP.
req: user, ip (as address() writes it), reason.
"""

import ipaddress

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

    def where(self, ip):
        """The hosts that ban IP now, with their bans; Failed when none could be asked."""
        found, errors = {}, []
        for name, run in sorted(self.hosts.items()):
            try:
                bans = [b for b in run("list") if b["ip"] == ip]
            except Failed as e:
                errors.append(f"{name}: {e}")
                continue
            if bans:
                found[name] = bans
        if errors and len(errors) == len(self.hosts):
            raise Failed("차단 목록을 읽지 못했습니다 (" + "; ".join(errors) + ")")
        return found

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
