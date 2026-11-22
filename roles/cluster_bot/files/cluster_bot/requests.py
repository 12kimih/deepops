"""The request engine every feature of the bot goes through.

    engine = Engine({"scratch": ScratchFeature(...), ...}, pending_path)
    engine.submit(req)          -> Outcome(done | held | refused, text, data, req, key)
    engine.approve(key, admin)  -> Outcome      engine.deny(key, admin, why) -> Outcome
    engine.note(key, **info)    keeps where a held request's messages are

A request is a dict with "kind" (the feature) and "user"; the feature decides it
(auto; admin; or ("people", [login, ...]): any one of them, a project's owners say, or
an administrator; with the reason), applies it, and describes it in one line. A held
request is kept in a JSON file (atomic, 0600), so an approval survives a restart.
A feature adds only its policy and its action; the flow is the same for all.
"""

import secrets
import time
from dataclasses import dataclass, field

from . import state


class Refused(Exception):
    """A request nobody may make (someone else's allocation, a bad key); str() says why."""


class Failed(Exception):
    """The system the request acts on refused or could not be reached; str() says why."""


@dataclass
class Outcome:
    status: str  # done, held, refused
    text: str
    data: dict = field(default_factory=dict)
    request: dict = field(default_factory=dict)
    key: str = ""


class Feature:
    """What a feature provides. decide -> ("auto" | "admin" | ("people", [login]), why);
    apply -> (text, data)."""

    def decide(self, req):
        raise NotImplementedError

    def apply(self, req):
        raise NotImplementedError

    def describe(self, req):
        raise NotImplementedError


class Engine:
    def __init__(self, features, pending_path):
        self.features, self.pending_path = features, pending_path

    def _pending(self):
        return state.read(self.pending_path, {})

    def _save(self, pending):
        state.write(self.pending_path, pending)

    def describe(self, req):
        return self.features[req["kind"]].describe(req)

    def _apply(self, req):
        try:
            text, data = self.features[req["kind"]].apply(req)
        except (Failed, Refused) as e:
            return Outcome("refused", f"처리하지 못했습니다: {e}", request=req)
        return Outcome("done", text, data, req)

    def submit(self, req):
        feature = self.features.get(req.get("kind"))
        if feature is None:
            return Outcome("refused", f"알 수 없는 요청입니다: {req.get('kind')}", request=req)
        try:
            verdict, why = feature.decide(req)
        except Refused as e:
            return Outcome("refused", f"요청할 수 없습니다: {e}", request=req)
        except Failed as e:
            return Outcome("refused", f"처리할 시스템에 닿지 못했습니다: {e}", request=req)
        if verdict == "auto":
            return self._apply(req)
        # Who approves: the administrators, or any of some people (an administrator may too).
        approvers = list(verdict[1]) if isinstance(verdict, tuple) and verdict[0] == "people" else []
        pending = self._pending()
        key = secrets.token_hex(4)
        pending[key] = {**req, "why": why, "asked": int(time.time()), "approvers": approvers}
        self._save(pending)
        whom = f"{', '.join(approvers)} 중 한 명의" if approvers else "관리자"
        return Outcome(
            "held", f"{whom} 승인을 기다립니다 ({why}): {feature.describe(req)}", request=req, key=key
        )

    def approvers(self, key):
        """Who may approve the held request KEY besides an administrator: logins, [] for
        administrators only (or no such request)."""
        return list((self._pending().get(key) or {}).get("approvers") or [])

    def note(self, key, **info):
        pending = self._pending()
        if key in pending:
            pending[key].update(info)
            self._save(pending)

    def _take(self, key):
        pending = self._pending()
        req = pending.pop(key, None)
        if req is not None:
            self._save(pending)
        return req

    def approve(self, key, admin):
        req = self._take(key)
        if req is None:
            return Outcome("refused", "이미 처리된 요청입니다.")
        out = self._apply(req)
        out.text += f" (승인: {admin})"
        return out

    def deny(self, key, admin, why=""):
        req = self._take(key)
        if req is None:
            return Outcome("refused", "이미 처리된 요청입니다.")
        return Outcome(
            "refused",
            f"거절되었습니다: {self.describe(req)} ({admin}{', ' + why if why else ''})",
            request=req,
        )
