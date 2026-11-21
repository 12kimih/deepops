"""Scratch allocations through the bot: a request is decided (scratch_policy), applied at
once on the scratch server or held for an administrator, and every outcome is a message.

    service = Scratch(executor, policy, pending_path)
    service.request(req)        -> Outcome(done | held | refused, text, allocation, req)
    service.approve(key, admin) -> Outcome      service.deny(key, admin, why) -> Outcome
    service.mine(user)          -> the user's allocations

The executor runs clustertools.scratch_alloc on the scratch server (over the bot's SSH
key, whose forced command accepts nothing else) and returns its JSON. Held requests are
kept in a small JSON file, so an approval survives a restart of the bot.
"""

import json
import os
import secrets
import shlex
import subprocess
import time
from dataclasses import dataclass, field

from . import scratch_policy

TIMEOUT = 120


class Failed(Exception):
    """The scratch server refused or could not be reached; str() says why."""


class SshExecutor:
    """scratch-alloc on the scratch server, over the bot's SSH key (forced command serve)."""

    def __init__(self, host, key, user, known_hosts):
        self.host, self.key, self.user, self.known_hosts = host, key, user, known_hosts

    def __call__(self, *words):
        argv = ["ssh", "-i", self.key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self.known_hosts}",
                f"{self.user}@{self.host}", shlex.join(str(w) for w in words)]  # fmt: skip
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, errors="replace", timeout=TIMEOUT, check=False
            )
        except subprocess.TimeoutExpired:
            raise Failed(f"the scratch server did not answer within {TIMEOUT}s") from None
        if done.returncode != 0:
            raise Failed((done.stderr or done.stdout).strip().removeprefix("scratch-alloc: ") or "it refused")
        return json.loads(done.stdout)


def human(n):
    for unit, scale in (("T", 1024**4), ("G", 1024**3), ("M", 1024**2)):
        if n >= scale:
            return f"{n / scale:.1f}".rstrip("0").rstrip(".") + unit
    return f"{n}B"


def day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


@dataclass
class Outcome:
    status: str  # done, held, refused
    text: str
    allocation: dict = field(default_factory=dict)
    request: dict = field(default_factory=dict)
    key: str = ""


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


class Scratch:
    def __init__(self, executor, policy, pending_path):
        self.run, self.policy, self.pending_path = executor, policy, pending_path

    def _pending(self):
        try:
            with open(self.pending_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, pending):
        os.makedirs(os.path.dirname(self.pending_path), mode=0o700, exist_ok=True)
        tmp = self.pending_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(pending, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.pending_path)

    def mine(self, user):
        return self.run("list", user)

    def _apply(self, req):
        try:
            a = self.run(*words(req))
        except Failed as e:
            return Outcome("refused", f"처리하지 못했습니다: {e}", request=req)
        if req["op"] == "release":
            return Outcome("done", f"{a['id']}번을 반납했습니다. 곧 지워집니다.", a, req)
        return Outcome("done", f"완료: {summary(a)}", a, req)

    def request(self, req):
        try:
            verdict, why = scratch_policy.decide(req, self.run("list"), self.policy)
        except scratch_policy.Refused as e:
            return Outcome("refused", f"요청할 수 없습니다: {e}", request=req)
        except Failed as e:
            return Outcome("refused", f"스크래치 서버에 닿지 못했습니다: {e}", request=req)
        if verdict == "auto":
            return self._apply(req)
        pending = self._pending()
        key = secrets.token_hex(4)
        pending[key] = {**req, "why": why, "asked": int(time.time())}
        self._save(pending)
        return Outcome("held", f"관리자 승인을 기다립니다 ({why}): {describe(req)}", request=req, key=key)

    def note(self, key, **info):
        """Keep INFO (where the request's messages are) with a held request."""
        pending = self._pending()
        if key in pending:
            pending[key].update(info)
            self._save(pending)

    def approve(self, key, admin):
        pending = self._pending()
        req = pending.pop(key, None)
        if req is None:
            return Outcome("refused", "이미 처리된 요청입니다.")
        self._save(pending)
        out = self._apply(req)
        out.text += f" (승인: {admin})"
        return out

    def deny(self, key, admin, why=""):
        pending = self._pending()
        req = pending.pop(key, None)
        if req is None:
            return Outcome("refused", "이미 처리된 요청입니다.")
        self._save(pending)
        return Outcome(
            "refused",
            f"관리자가 거절했습니다: {describe(req)} ({admin}{', ' + why if why else ''})",
            request=req,
        )
