"""Collaboration folder requests as a feature of the request engine: every change is a
pull request on the roster's `projects` (plugins/filter/people.py), which an
administrator merges and deploys, as for the join and leave requests.

    ProjectFeature(repo, people, settings)

A project has roles (people.PROJECT_ROLES): owners manage it (people, roles, purpose)
and read and write; members read and write; viewers read. Who decides what:

    op        asks                         decided by
    new       anyone (they own it)         an administrator
    set       an owner: someone's role,    at once (the pull request is the review)
              adding or removing them
    leave     anyone, for themselves       at once (the last owner cannot)
    join      anyone, as member or viewer  any one of the owners
    purpose   an owner                     at once
    close     an owner                     an administrator (the folder stays)

req: kind "project", op, user (who asks, a login), project, and per op: owners,
members, viewers, purpose (new); login, role (set; role None removes); role (join);
purpose (purpose). `settings` adds to the roster's (base, roster, reserved): gid_min,
reserved_gids (the site's other cluster groups), root (where the folders are).
"""

import secrets

from .accounts import RosterFeature
from .requests import Refused

ROLE_NAMES = {"owners": "소유자", "members": "멤버", "viewers": "열람자"}


def role_name(role):
    return ROLE_NAMES.get(role, "없음") if role else "없음"


class ProjectFeature(RosterFeature):
    def _project(self, name):
        proj = (self._document().get("projects") or {}).get(name)
        if proj is None:
            raise Refused(f"{name} 공동작업 폴더가 없습니다")
        return proj

    def _role(self, proj, login):
        return self.people.project_people(proj).get(login)

    def decide(self, req):
        op, name, who = req["op"], req["project"], req["user"]
        person = self._roster().get(who)
        if person is None or (person or {}).get("status") == "left":
            raise Refused(f"{who}은(는) 명부의 활성 사용자가 아닙니다")
        if op == "new":
            if not self.people.PROJECT_NAME.match(name):
                raise Refused("이름은 영문 소문자로 시작하고 영문 소문자, 숫자, - 만 쓸 수 있습니다 (2~27자)")
            if name in (self._document().get("projects") or {}):
                raise Refused(f"{name} 공동작업 폴더가 이미 있습니다")
            return "admin", "새 공동작업 폴더는 관리자가 승인합니다"
        proj = self._project(name)
        mine = self._role(proj, who)
        if op == "join":
            if mine:
                raise Refused(f"이미 {name}의 {role_name(mine)}입니다")
            if req["role"] not in ("members", "viewers"):
                raise Refused("참여 요청은 멤버나 열람자로만 할 수 있습니다")
            return ("people", list(proj["owners"])), "공동작업 폴더 소유자가 승인합니다"
        if op == "leave":
            if not mine:
                raise Refused(f"{name}에 속해 있지 않습니다")
            return "auto", ""
        if mine != "owners":
            raise Refused(f"{name}의 소유자만 할 수 있습니다")
        if op == "close":
            return "admin", "공동작업 폴더 닫기는 관리자가 승인합니다"
        if op not in ("set", "purpose"):
            raise Refused(f"알 수 없는 요청입니다: {op}")
        return "auto", ""

    def apply(self, req):
        op, name = req["op"], req["project"]
        text, sha = self._read(self.s["roster"])
        P = self.people
        try:
            if op == "new":
                new, gid, read_gid = P.roster_project_new(
                    text, name, [req["user"], *req.get("owners", [])], req.get("purpose", ""),
                    members=req.get("members", []), viewers=req.get("viewers", []),
                    reserved=self.s["reserved_gids"], gid_min=self.s["gid_min"], place=req.get("place", "home"),
                )  # fmt: skip
                extra = f"\n- groups: {name} (gid {gid}), {name}{P.READ_SUFFIX} (gid {read_gid})"
            elif op == "set":
                new, extra = P.roster_project_set(text, name, req["login"], req.get("role")), ""
            elif op in ("join", "leave"):
                role = req["role"] if op == "join" else None
                new, extra = P.roster_project_set(text, name, req["user"], role), ""
            elif op == "purpose":
                new, extra = P.roster_project_purpose(text, name, req.get("purpose", "")), ""
            else:
                new, extra = P.roster_project_close(text, name), ""
        except P.EditError as e:
            raise Refused(str(e)) from None
        title = f"projects: {self.describe_en(req)}"
        body = (
            f"Project request from Slack by {req['user']}: {self.describe_en(req)}.{extra}\n\n"
            "Merge, then run `slurm-cluster.yml --tags people` (groups, then the folder)."
        )
        url = self._propose(f"project/{name}-{op}-{secrets.token_hex(2)}", new, sha, title, body)
        return f"PR을 열었습니다: {url} (관리자가 PR을 병합하면 알려 드립니다)", {"url": url}

    def describe_en(self, req):
        op, name = req["op"], req["project"]
        return {
            "new": f"new project {name} on {req.get('place', 'home')}",
            "set": f"{req.get('login')} as {req.get('role') or 'nothing'} in {name}",
            "join": f"{req['user']} joins {name} as {req.get('role')}",
            "leave": f"{req['user']} leaves {name}",
            "purpose": f"purpose of {name}",
            "close": f"close {name}",
        }.get(op, f"{op} {name}")

    def describe(self, req):
        op, name = req["op"], req["project"]
        return {
            "new": f"새 공동작업 폴더 {name} ({'/scratch' if req.get('place') == 'scratch' else '/home'})",
            "set": f"{name}: {req.get('login')}을(를) {role_name(req.get('role'))}(으)로",
            "join": f"{name} 참여 ({role_name(req.get('role'))})",
            "leave": f"{name}에서 나가기",
            "purpose": f"{name} 설명 바꾸기",
            "close": f"{name} 닫기",
        }.get(op, f"{name} {op}")


def changes(before, after):
    """Whose role changed between two deployed rosters' projects: [(login, project, role
    before, role after)], None for not in it (a project gone is everyone out)."""
    out = []
    for name in sorted(set(before) | set(after)):
        old = {login: role for role in ROLE_NAMES for login in (before.get(name) or {}).get(role) or []}
        new = {login: role for role in ROLE_NAMES for login in (after.get(name) or {}).get(role) or []}
        out += [(login, name, old.get(login), new.get(login)) for login in sorted(set(old) | set(new))
                if old.get(login) != new.get(login)]  # fmt: skip
    return out


def notify_deployed(projects, seen_path, root, slack_of, dms, notice):
    """Tell each person whose role in a project changed since the bot last looked (SEEN,
    the deployed projects then), by a DM through DMS (cluster_tools.slack.Slack, logged
    for the administrators). The first look only records."""
    from . import state

    seen = state.read(seen_path, None)
    if seen is not None:
        for login, name, old, new in changes(seen, projects):
            slack_id = slack_of(login)
            if slack_id:
                dms.dm(slack_id, notice(name, old, new, root), kind="project", login=login)
    state.write(seen_path, projects)
