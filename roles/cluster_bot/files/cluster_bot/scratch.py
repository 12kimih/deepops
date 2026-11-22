"""Scratch allocations as a feature of the request engine (cluster_bot.requests): the
policy (scratch_policy) decides, the scratch server applies, and every result is a line
the person reads.

    feature = ScratchFeature(executor, policy)
    feature.mine(user) -> the user's allocations

The executor runs cluster_tools.scratch_alloc on the scratch server (a
cluster_bot.remote.Remote for SERVICE) and returns its JSON.
"""

import time

from . import scratch_policy
from .requests import Feature

# The scratch server's service for the bot (roles/scratch_alloc offers it by this name).
SERVICE = "scratch"


def human(n):
    for unit, scale in (("T", 1024**4), ("G", 1024**3), ("M", 1024**2)):
        if n >= scale:
            return f"{n / scale:.1f}".rstrip("0").rstrip(".") + unit
    return f"{n}B"


def day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def describe(req):
    """One line saying what a request asks for."""
    op = req["op"]
    if op == "create":
        return f"새 스크래치 {human(req['size'])}, {req['days']}일 ({req.get('reason') or '사유 없음'})"
    if op == "resize":
        return f"{req['id']}번 크기를 {human(req['size'])}로"
    if op == "extend":
        return f"{req['id']}번 {req['days']}일 연장"
    return f"{req['id']}번 반납"


def words(req):
    """The scratch-alloc command line for a request."""
    op = req["op"]
    if op == "create":
        return ["create", req["user"], f"{req['size'] // 1024}K", req["days"], req.get("reason", "")]
    if op == "resize":
        return ["resize", req["id"], f"{req['size'] // 1024}K"]
    if op == "extend":
        return ["extend", req["id"], req["days"]]
    return ["release", req["id"]]


def summary(a):
    """One line for an allocation as the user sees it."""
    state = {"active": "사용 중", "frozen": "만료됨(읽기 전용)", "released": "반납됨"}.get(
        a["state"], a["state"]
    )
    return f"`{a['path']}` {human(a['bytes'])}, {day(a['expires'])}까지, {state} (번호 {a['id']})"


class ScratchFeature(Feature):
    def __init__(self, executor, policy):
        self.run, self.policy = executor, policy

    def mine(self, user):
        return self.run("list", user)

    def deadlines(self):
        """The active allocations' end dates (a cluster_bot.reminders source's)."""
        return {
            str(a["id"]): (a["expires"], a["user"], a) for a in self.run("list") if a["state"] == "active"
        }

    def decide(self, req):
        return scratch_policy.decide(req, self.run("list"), self.policy)

    def apply(self, req):
        a = self.run(*words(req))
        if req["op"] == "release":
            return f"{a['id']}번을 반납했습니다. 곧 지워집니다.", a
        return f"완료: {summary(a)}", a

    def describe(self, req):
        return describe(req)
