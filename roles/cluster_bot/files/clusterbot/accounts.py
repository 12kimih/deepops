"""Account requests as features of the request engine: /join and /leave become pull
requests on the site configuration, which an administrator merges and deploys.

    JoinFeature(repo, people, settings)    LeaveFeature(repo, people, settings)

`people` is the roster module (plugins/filter/people.py: check, roster_add,
roster_leave): the same editors an administrator's people.py uses, so the bot never
edits the roster its own way. `settings`: base (branch), roster and accounting (paths
in the repository), reserved (system account IDs). The roster stays the only source of
accounts: the bot proposes, a person reviews, the deploy applies.
"""

import re

from ruamel.yaml import YAML

from . import sshkeys
from .requests import Failed, Feature, Refused
from .github import Error as GitHubError


def load(text):
    """A YAML document (ruamel, pure Python: the bot's venv has no compiled YAML)."""
    return YAML(typ="safe", pure=True).load(text)


class _RosterFeature(Feature):
    def __init__(self, repo, people, settings):
        self.repo, self.people, self.s = repo, people, settings

    def _read(self, path):
        try:
            return self.repo.read(path, self.s["base"])
        except GitHubError as e:
            raise Failed(str(e)) from None

    def _roster(self):
        text, _ = self._read(self.s["roster"])
        return load(text)["people"]

    def _propose(self, branch, text, sha, title, body):
        try:
            return self.repo.propose(self.s["base"], branch, {self.s["roster"]: (text, sha)}, title, body)
        except GitHubError as e:
            raise Failed(str(e)) from None


class JoinFeature(_RosterFeature):
    """req: user (the requested login), name, account, key (a public key line), slack."""

    def teams(self):
        """The accounts a new person may pick: those without children, but the excluded."""
        accounts = (load(self._read(self.s["accounting"])[0]) or {}).get("accounts") or {}
        parents = {(a or {}).get("parent") for a in accounts.values()}
        return sorted(n for n in accounts if n not in parents and n not in set(self.s.get("exclude", [])))

    def decide(self, req):
        if not re.fullmatch(self.people.LOGIN.pattern, req["user"]):
            raise Refused("아이디는 영문 소문자로 시작하고 영문 소문자, 숫자, -, _ 만 쓸 수 있습니다")
        try:
            sshkeys.parse(req["key"])
        except sshkeys.Invalid as e:
            raise Refused(str(e)) from None
        if req["user"] in self._roster():
            raise Refused(f"{req['user']}은(는) 이미 있는 아이디입니다")
        accounts = (load(self._read(self.s["accounting"])[0]) or {}).get("accounts") or {}
        if req["account"] not in accounts:
            raise Refused(f"{req['account']} 팀이 없습니다")
        return "admin", "새 계정은 관리자가 승인합니다"

    def apply(self, req):
        text, sha = self._read(self.s["roster"])
        accounts = (load(self._read(self.s["accounting"])[0]) or {}).get("accounts") or {}
        try:
            new, uid = self.people.roster_add(text, req["user"], req["account"], req.get("name"), req.get("slack"),
                                              self.s["reserved"], accounts)  # fmt: skip
        except self.people.EditError as e:
            raise Refused(str(e)) from None
        key = sshkeys.parse(req["key"])
        body = (f"Account request from Slack ({req.get('slack', '')}), approved in the admin channel.\n\n"
                f"- login: {req['user']} (uid {uid})\n- account: {req['account']}\n"
                f"- SSH key: {key.kind} {key.fingerprint}\n\n"
                "Merge, then run `slurm-cluster.yml --tags people`.")  # fmt: skip
        url = self._propose(f"join/{req['user']}", new, sha, f"people: add {req['user']}", body)
        return f"계정 신청 PR을 열었습니다: {url} (관리자가 반영하면 계정이 생기고 안내 DM이 갑니다)", {
            "url": url,
            "uid": uid,
        }

    def describe(self, req):
        return f"새 계정 {req['user']} ({req['account']})"


class LeaveFeature(_RosterFeature):
    """req: user (the login that leaves), asked_by."""

    def decide(self, req):
        person = self._roster().get(req["user"])
        if person is None:
            raise Refused(f"{req['user']}은(는) 명부에 없습니다")
        if (person or {}).get("status") == "left":
            raise Refused(f"{req['user']}은(는) 이미 정리된 계정입니다")
        return "admin", "계정 정리는 관리자가 승인합니다"

    def apply(self, req):
        text, sha = self._read(self.s["roster"])
        try:
            new = self.people.roster_leave(text, req["user"])
        except self.people.EditError as e:
            raise Refused(str(e)) from None
        body = f"Leave request from Slack (asked by {req.get('asked_by', req['user'])}).\n\nMerge, then run `slurm-cluster.yml --tags people`."
        url = self._propose(f"leave/{req['user']}", new, sha, f"people: {req['user']} leaves", body)
        return f"계정 정리 PR을 열었습니다: {url}", {"url": url}

    def describe(self, req):
        return f"계정 정리 {req['user']}"
