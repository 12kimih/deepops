"""Block Kit for the collaboration folders (cluster_bot.projects): the Home tab's section,
the forms, and the notice a person gets once a change is deployed."""

from .blocks import button, field
from .projects import ROLE_NAMES, role_name

ROLES = ("owners", "members", "viewers")
MENU = {
    "manage": "사람 추가, 역할 변경, 빼기",
    "purpose": "설명 바꾸기",
    "close": "프로젝트 닫기",
    "leave": "나가기",
}


def role_of(proj, login):
    return next((r for r in ROLES if login in (proj.get(r) or [])), None)


def _option(text, value):
    return {"text": {"type": "plain_text", "text": text[:75]}, "value": value}


def _people(proj):
    parts = []
    for role in ROLES:
        logins = proj.get(role) or []
        if logins:
            parts.append(f"{ROLE_NAMES[role]} {', '.join(logins)}")
    return " / ".join(parts)


def home(login, projects, root):
    """LOGIN's projects with what they may do there, and how to start or join one."""
    mine = {n: p for n, p in sorted(projects.items()) if role_of(p, login)}
    blocks = [{"type": "divider"},
              {"type": "section", "text": {"type": "mrkdwn", "text": "*공동작업 폴더*"}}]  # fmt: skip
    for name, proj in mine.items():
        role = role_of(proj, login)
        ops = ["manage", "purpose", "close", "leave"] if role == "owners" else ["leave"]
        text = f"*{name}* ({ROLE_NAMES[role]})  `{root}/{name}`\n{_people(proj)}"
        if proj.get("purpose"):
            text += f"\n_{proj['purpose']}_"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text},
                       "accessory": {"type": "overflow", "action_id": "project.menu",
                                     "options": [_option(MENU[op], f"{op}:{name}") for op in ops]}})  # fmt: skip
    if not mine:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "참여 중인 프로젝트가 없습니다."}]}
        )
    blocks.append({"type": "actions", "elements": [
        button("새 프로젝트", "project.new", "new", style="primary"),
        button("참여 요청", "project.join", "join"),
    ]})  # fmt: skip
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
        "관리자와 멤버는 읽고 쓰고, 열람자는 읽기만 합니다. 바뀐 그룹은 새로 로그인하면 적용됩니다. "
        "파일은 만든 사람의 디스크 할당량에 들어갑니다."}]})  # fmt: skip
    return blocks


def _modal(callback, title, blocks, submit="신청", metadata=""):
    return {"type": "modal", "callback_id": callback, "private_metadata": metadata,
            "title": {"type": "plain_text", "text": title}, "submit": {"type": "plain_text", "text": submit},
            "close": {"type": "plain_text", "text": "취소"}, "blocks": blocks}  # fmt: skip


def place_names(places):
    """{place: its label} of where a folder lives (people.PROJECT_PLACES; the bot's config
    `projects.places`: each place's root, and the scratch space's stale period): its
    files count to each writer's quota on that filesystem."""
    return {
        "home": f"{places['home']}: 오래 보관할 데이터",
        "scratch": f"{places['scratch']}: 빠른 작업 공간, {places['scratch_stale_days']}일 동안 쓰지 않은 파일은 정리됩니다",
    }


def new_form(places):
    """A new project: its name and purpose, where it lives (PLACES, place_names), and whom
    to add (the asker owns it)."""
    names = place_names(places)
    people = {"type": "multi_users_select", "placeholder": {"type": "plain_text", "text": "Slack에서 고르기"}}
    return _modal("project.new.submit", "새 공동작업 폴더", [
        field("name", "이름 (영문 소문자, 숫자, -)", {"type": "plain_text_input", "max_length": 27}),
        field("purpose", "무엇을 위한 폴더인가요", {"type": "plain_text_input"}, optional=True),
        field("place", "어디에 둘까요", {"type": "radio_buttons", "initial_option": _option(names["home"], "home"),
                                        "options": [_option(names[p], p) for p in names]}),
        field("owners", "함께 관리할 사람 (관리자)", people, optional=True),
        field("members", "멤버 (읽고 쓰기)", people, optional=True),
        field("viewers", "열람자 (읽기만)", people, optional=True),
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "신청한 사람이 관리자가 됩니다. 관리자가 승인하면 폴더가 만들어집니다."}]},
    ])  # fmt: skip


def join_form(projects, login):
    """Ask to join a project LOGIN is not in, as a member or a viewer."""
    options = [_option(f"{n} - {p.get('purpose', '')}".rstrip(" -"), n)
               for n, p in sorted(projects.items()) if not role_of(p, login)][:100]  # fmt: skip
    if not options:
        return None
    return _modal("project.join.submit", "프로젝트 참여 요청", [
        field("project", "프로젝트", {"type": "static_select", "options": options}),
        field("role", "역할", {"type": "radio_buttons", "initial_option": _option(ROLE_NAMES["members"], "members"),
                               "options": [_option(ROLE_NAMES[r], r) for r in ("members", "viewers")]}),
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "프로젝트 관리자 중 한 명이 승인하면 반영됩니다."}]},
    ])  # fmt: skip


def manage_form(name):
    """An owner adds someone, changes their role, or takes them out."""
    choices = [_option(ROLE_NAMES[r], r) for r in ROLES] + [_option("빼기", "remove")]
    return _modal("project.manage.submit", f"{name}: 사람과 역할", [
        field("person", "사람", {"type": "users_select"}),
        field("role", "역할", {"type": "static_select", "options": choices}),
    ], metadata=name)  # fmt: skip


def purpose_form(name, purpose):
    element = {"type": "plain_text_input"}
    if purpose:
        element["initial_value"] = purpose
    return _modal("project.purpose.submit", f"{name}: 설명", [
        field("purpose", "무엇을 위한 폴더인가요 (비우면 지움)", element, optional=True),
    ], submit="저장", metadata=name)  # fmt: skip


def notice(name, before, after, root):
    """The DM to someone whose role in project NAME changed in the deployed roster."""
    if after is None:
        return f"공동작업 폴더 {name}에서 빠졌습니다 (이전: {role_name(before)}). 새로 로그인하면 더는 접근할 수 없습니다."
    access = "읽기만" if after == "viewers" else "읽고 쓰기"
    verb = (
        f"{role_name(before)}에서 {role_name(after)}(으)로 바뀌었습니다"
        if before
        else f"{role_name(after)}(으)로 추가되었습니다"
    )
    return f"공동작업 폴더 {name}에 {verb}: `{root}/{name}` ({access}). 새로 로그인하면 적용됩니다."
