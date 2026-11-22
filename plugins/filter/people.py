"""Derive access lists from a people roster: the one place a site records its users.

A roster file declares the roles a site uses and the people:

    roles:                        # role -> {description, requires}
      admin: {description: sudo and job-less SSH to compute nodes}
      docker: {description: Docker daemon socket, requires: admin}
    attributes: [slack]           # extra per-person keys the site uses
    member_keys: [slack]          # what every active Slurm user must set
    several_accounts: [admin]     # optional: only these roles may be in several Slurm
                                  # accounts (each association has its own fair share,
                                  # so a second one doubles a person's); absent: anyone
    reserved_logins: [shared]     # optional: names no person may have, such as a
                                  # directory under the home root that is no one's home
    projects:                     # optional: collaboration folders (roles/shared_space)
      vision:
        gid: 50001                # group vision: owners and members (read and write)
        read_gid: 50002           # group vision-read: viewers (read); no person's UID
        owners: [alice]           # one or more: manage people, roles and purpose
        members: [bob]            # active people, one role each
        viewers: [carol]
        purpose: shared eval set  # optional
    people:
      alice:
        uid: 1001                 # required, unique, >= 1000
        name: Alice Example       # optional, the account's comment (GECOS)
        shell: /bin/bash          # optional, the login shell
        slack: U0ALICE01          # an attribute declared above
        accounts:                 # optional, Slurm account -> association options
          team1: {}
          admin: {qos: [high, urgent]}
        default_account: admin    # needed with several accounts
        roles: [admin]
        disk_quota: {block_warn: 50G, block_hard: 100G}  # optional, over the site's
                                  # default per-user disk quota (roles/disk_quota)
        status: active            # active (default) or left
        ends: 2027-02-28          # optional: the last day of membership (graduation)

Every list a role consumes (sudo netgroup, group members, Slurm associations,
chat IDs, subordinate IDs) is computed from `people` with the filters below, so access is granted
and revoked in one place. Only `active` people get anything; someone who has left
keeps an entry so the UID stays reserved and the account can be locked.

Leaving has two steps. Past `ends`, a person keeps their account and their files (to
take them away) but no Slurm association and no role; login stops at
people_login_expires (ends + people_leave_grace_days), applied by the directory and
the local accounts from that one date. `status: left` is the final state (the files
are kept for people_leave_keep_days, then an administrator decides).

check() is the single validation, used at deploy time through people_check and at
commit time by scripts/deepops/people.py. roster_add() and roster_leave() are the one
way a roster file is edited (scripts/deepops/people.py, the cluster bot's requests).
"""

import re

STATUSES = ("active", "left")
KEYS = {"uid", "name", "shell", "accounts", "default_account", "roles", "status", "disk_quota", "ends"}
QUOTA_KEYS = ("block_warn", "block_hard", "inode_warn", "inode_hard")
LOGIN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
UID_MIN = 1000
TOP_KEYS = {"roles", "attributes", "member_keys", "several_accounts", "reserved_logins", "projects", "people"}
# A project's roles, most rights first: owners manage it (people, roles, purpose) and
# read and write, members read and write, viewers read.
PROJECT_ROLES = ("owners", "members", "viewers")
PROJECT_KEYS = {"gid", "read_gid", "purpose", *PROJECT_ROLES}
# The viewers' group is <name>-read; a group name is at most 32 characters.
READ_SUFFIX = "-read"
PROJECT_NAME = re.compile(r"^[a-z][a-z0-9-]{1,26}$")


def _date(value):
    """A roster date (YAML reads 2027-02-28 as a date; quoted, a string) or None."""
    import datetime

    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


def _today(today=None):
    import datetime

    return _date(today) or datetime.date.today()


def _ended(person, today=None):
    """Whether PERSON is past their `ends`: account kept, no Slurm and no role."""
    ends = _date((person or {}).get("ends"))
    return ends is not None and _today(today) > ends


def _member(people, today=None):
    """Active people not past their end: those who hold associations and roles."""
    return {n: p for n, p in _active(people).items() if not _ended(p, today)}


def people_login_expires(person, grace_days=30):
    """The day PERSON's login stops, "YYYY-MM-DD", or "" for no end: ends + GRACE_DAYS
    for someone leaving; 1970-01-02, long past, for someone who has left. The one
    derivation the directory (shadowExpire) and local accounts (expires) both use."""
    import datetime

    person = person or {}
    if person.get("status", "active") != "active":
        return "1970-01-02"
    ends = _date(person.get("ends"))
    return (ends + datetime.timedelta(days=int(grace_days))).isoformat() if ends else ""


def people_shadow_day(day):
    """A "YYYY-MM-DD" from people_login_expires as shadow(5) counts it, days since
    1970-01-01 in UTC (shadowExpire; the user module's expires is this times 86400), or
    "" for no end. The one conversion: through a local-time timestamp a day would slip."""
    import datetime

    return (_date(day) - datetime.date(1970, 1, 1)).days if _date(day) else ""


def people_leaving(people, grace_days=30):
    """Active people with an end date, login -> {ends, login_expires} (both YYYY-MM-DD,
    sorted by login): what tells them in time to move their files away (the cluster
    bot's reminders) reads it, so the dates are the ones the accounts apply."""
    return {
        n: {"ends": _date(p["ends"]).isoformat(), "login_expires": people_login_expires(p, grace_days)}
        for n, p in sorted(_active(people).items())
        if _date(p.get("ends"))
    }


def _active(people):
    return {n: p or {} for n, p in (people or {}).items() if (p or {}).get("status", "active") == "active"}


def people_active(people):
    """The active part of the roster."""
    return _active(people)


def people_left(people):
    """Login names of people who have left, sorted."""
    return sorted(set(people or {}) - set(_active(people)))


def people_former(people, today=None):
    """Sorted logins of the roster who are no longer members: left, or past their end.
    What holds nothing any more (their Slurm associations go, slurm_accounting revoke)."""
    return sorted(set(people or {}) - set(_member(people, today)))


def people_purge_dates(people, keep_days=90):
    """{login: {uid, ends, due}} of everyone with an `ends`: their files fall due KEEP_DAYS
    after it (dates YYYY-MM-DD). Someone without `ends` never falls due
    (people_purge_undated)."""
    import datetime

    out = {}
    for n, p in sorted((people or {}).items()):
        ends = _date((p or {}).get("ends"))
        if ends is not None:
            due = ends + datetime.timedelta(days=int(keep_days))
            out[n] = {"uid": p["uid"], "ends": ends.isoformat(), "due": due.isoformat()}
    return out


def people_purge_due(people, keep_days=90, today=None):
    """people_purge_dates of everyone whose files are due now (playbooks/utilities/
    people-purge.yml): past their due day."""
    return {n: d for n, d in people_purge_dates(people, keep_days).items() if _today(today) > _date(d["due"])}


def people_purge_undated(people):
    """Sorted logins who have left without an `ends` date: their files never fall due."""
    return sorted(
        n for n, p in (people or {}).items() if (p or {}).get("status") == "left" and not _date(p.get("ends"))
    )


def people_purge_targets(people, keep_days=90, paths=(), today=None, dated=False):
    """[{login, uid, ends, due, path}] for each due person (people_purge_due), or with
    DATED everyone with an `ends` (people_purge_dates), and each of PATHS, a template
    holding {login} below an absolute directory. Refuses a template that could name
    anything but one person's own directory."""
    import posixpath

    for t in paths:
        parent = posixpath.dirname(t)
        if not (t.startswith("/") and posixpath.basename(t) == "{login}" and parent not in ("", "/")
                and "{" not in parent and posixpath.normpath(t) == t):  # fmt: skip
            raise ValueError(f"people_purge_paths: {t!r} must be /<dir>/{{login}}")
    out = []
    people_of = people_purge_dates(people, keep_days) if dated else people_purge_due(people, keep_days, today)
    for n, d in people_of.items():
        if not LOGIN.fullmatch(n):  # an empty or odd name must never make {login} mean its parent
            raise ValueError(f"people_purge_targets: {n!r} is not a login")
        out += [{"login": n, **d, "path": t.replace("{login}", n)} for t in paths]
    return out


def people_purge_owned(results):
    """The purge targets of RESULTS (a stat loop over people_purge_targets, as
    roles/people tasks/purge_local registers it) that exist as a directory, not a link,
    owned by their person: what may be measured or removed."""
    out = []
    for r in results or []:
        st, target = r.get("stat") or {}, (r.get("item") or {}).get("item") or {}
        if (
            st.get("exists")
            and st.get("isdir")
            and not st.get("islnk")
            and st.get("uid") == target.get("uid")
        ):
            out.append(target)
    return out


def people_with_role(people, role, today=None):
    """Sorted login names of members holding role (none once past their end)."""
    return sorted(n for n, p in _member(people, today).items() if role in (p.get("roles") or []))


def people_uids(people, role, today=None):
    """Sorted UIDs of members holding role (a kernel matches a uid, not a name)."""
    return sorted(p["uid"] for n, p in _member(people, today).items() if role in (p.get("roles") or []))


def people_attr(people, key):
    """{login: value} of key for active people who set it."""
    return {n: p[key] for n, p in sorted(_active(people).items()) if p.get(key) not in (None, "")}


def people_local_accounts(people, home_root="/home", shell="/bin/bash"):
    """Active people as roles/local_accounts declarations: the roster UID, a per-user
    group with GID = UID, the name as the comment, the shell, home under HOME_ROOT."""
    return {
        name: {
            "uid": p["uid"],
            "shell": p.get("shell") or shell,
            "home": f"{home_root}/{name}",
            "system": False,
            **({"comment": p["name"]} if p.get("name") else {}),
        }
        for name, p in _active(people).items()
    }


def people_shells(people, default="/bin/bash"):
    """Sorted login shells the roster gives active people (DEFAULT for those who set
    none): what every host must have installed for them to log in."""
    return sorted({p.get("shell") or default for p in _active(people).values()} | {default})


def people_accounting(people, today=None):
    """{login: {accounts, default_account}} for members with Slurm accounts (nobody past
    their end: no new jobs while they take their files away).

    This is the `users` part of a slurm_accounting spec.
    """
    out = {}
    for n, p in sorted(_member(people, today).items()):
        if p.get("accounts"):
            u = {"accounts": {a: o or {} for a, o in p["accounts"].items()}}
            if p.get("default_account"):
                u["default_account"] = p["default_account"]
            out[n] = u
    return out


SUBID_MAX = 2**32 - 2  # the highest valid ID; 2**32 - 1 is (uid_t)-1


def people_subids(people, base=100000, count=65536, uid_min=UID_MIN):
    """[{name, uid, start, count}] subordinate ID ranges for active people, by UID.

    A range is computed from the UID alone (start = base + (uid - uid_min) * count),
    so every node agrees without coordination and a range never moves when someone is
    added or leaves. A left person's range stays reserved by their UID. The ranges
    must lie above every roster UID and within the 32-bit ID space."""
    from ansible.errors import AnsibleFilterError

    people = people or {}
    uids = [int((p or {})["uid"]) for p in people.values()]
    if uids and base <= max(uids):
        raise AnsibleFilterError(f"subordinate ID base {base} must exceed the highest roster UID {max(uids)}")
    out = []
    for name, p in sorted(_active(people).items(), key=lambda item: int(item[1]["uid"])):
        uid = int(p["uid"])
        start = base + (uid - uid_min) * count
        if uid < uid_min or start + count - 1 > SUBID_MAX:
            raise AnsibleFilterError(
                f"{name}: UID {uid} gives no subordinate range within {base}..{SUBID_MAX} of width {count}"
            )
        out.append({"name": name, "uid": uid, "start": start, "count": count})
    return out


def check(roster, uid_min=UID_MIN):
    """Problems in a roster document, as a list of messages; empty means valid."""
    if not isinstance(roster, dict) or not isinstance(roster.get("people"), dict):
        return ["the roster must be a mapping with a `people` mapping"]
    problems = [f"{key}: unknown top-level key" for key in sorted(set(roster) - TOP_KEYS)]
    roles = roster.get("roles") or {}
    if not isinstance(roles, dict):
        return problems + ["roles: must be a mapping of role -> {description, requires}"]
    for role, r in roles.items():
        req = (r or {}).get("requires")
        if req is not None and req not in roles:
            problems.append(f"roles.{role}.requires: unknown role {req}")
    attributes = set(roster.get("attributes") or [])
    member_keys = roster.get("member_keys") or []
    for key in member_keys:
        if key not in KEYS | attributes:
            problems.append(f"member_keys: {key} is neither a person key nor in attributes")
    several = roster.get("several_accounts")
    for role in several or []:
        if role not in roles:
            problems.append(f"several_accounts: unknown role {role}")
    reserved = roster.get("reserved_logins") or []
    if not isinstance(reserved, list) or not all(isinstance(r, str) for r in reserved):
        problems.append("reserved_logins: must be a list of names")
        reserved = []
    uids = {}
    for name, p in roster["people"].items():
        p = p or {}
        where = f"people.{name}"
        if not isinstance(name, str) or not LOGIN.match(name):
            problems.append(f"{where}: not a valid login name")
        elif name in reserved:
            problems.append(f"{where}: a reserved name (reserved_logins), no one's login")
        if not isinstance(p, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        for key in sorted(set(p) - KEYS - attributes):
            problems.append(f"{where}.{key}: unknown key")
        uid = p.get("uid")
        if not isinstance(uid, int) or isinstance(uid, bool) or uid < uid_min:
            problems.append(f"{where}.uid: must be an integer >= {uid_min}")
        elif uid in uids:
            problems.append(f"{where}.uid: {uid} is also {uids[uid]}'s")
        else:
            uids[uid] = name
        try:
            _date(p.get("ends"))
        except (TypeError, ValueError):
            problems.append(f"{where}.ends: a date, YYYY-MM-DD")
        status = p.get("status", "active")
        if status not in STATUSES:
            problems.append(f"{where}.status: must be one of {', '.join(STATUSES)}")
        held = p.get("roles") or []
        if not isinstance(held, list):
            problems.append(f"{where}.roles: must be a list")
            held = []
        for role in held:
            if role not in roles:
                problems.append(f"{where}.roles: unknown role {role}")
                continue
            req = (roles[role] or {}).get("requires")
            if req and req not in held:
                problems.append(f"{where}.roles: {role} requires {req}")
        quota = p.get("disk_quota")
        if quota is not None and (not isinstance(quota, dict) or set(quota) - set(QUOTA_KEYS)):
            problems.append(f"{where}.disk_quota: a mapping of {', '.join(QUOTA_KEYS)}")
        accounts = p.get("accounts") or {}
        if not isinstance(accounts, dict):
            problems.append(f"{where}.accounts: must be a mapping of account -> options")
            accounts = {}
        default = p.get("default_account")
        if default is not None and default not in accounts:
            problems.append(f"{where}.default_account: {default} is not one of its accounts")
        if default is None and len(accounts) > 1:
            problems.append(f"{where}.default_account: required with several accounts")
        if several is not None and len(accounts) > 1 and not set(held) & set(several):
            problems.append(
                f"{where}.accounts: several accounts are for {', '.join(several) or 'nobody'} "
                "(several_accounts): each association is its own fair share"
            )
        if status == "left":
            for key in ("roles", "accounts"):
                if p.get(key):
                    problems.append(f"{where}.{key}: must be empty for someone who has left")
        elif accounts:
            for key in member_keys:
                if p.get(key) in (None, ""):
                    problems.append(f"{where}.{key}: required for an active Slurm user")
    return problems + project_problems(roster, uid_min)


def project_people(proj):
    """{login: role} of a project: owners, members and viewers (one role each)."""
    return {login: role for role in PROJECT_ROLES for login in (proj.get(role) or [])}


def project_problems(roster, uid_min=UID_MIN):
    """Problems of the roster's projects: each two groups of active people, <name> (its
    owners and members: read and write) and <name>-read (its viewers: read), each with
    its own GID (no person's UID: their per-user group has it), at least one owner, one
    role a person, and names that are no one's login."""
    projects = roster.get("projects") or {}
    if not isinstance(projects, dict):
        return ["projects: must be a mapping of name -> {gid, read_gid, owners, members, viewers, purpose}"]
    people = roster.get("people") or {}
    active = {n for n, p in people.items() if (p or {}).get("status", "active") != "left"}
    taken_names = set(people) | set(roster.get("reserved_logins") or [])
    uids = {(p or {}).get("uid") for p in people.values()}
    problems, gids = [], {}
    for name, proj in projects.items():
        where = f"projects.{name}"
        if not isinstance(name, str) or not PROJECT_NAME.match(name):
            problems.append(f"{where}: a name of lowercase letters, digits and dashes (2 to 27)")
        elif {name, f"{name}{READ_SUFFIX}"} & taken_names:
            problems.append(f"{where}: its groups ({name}, {name}{READ_SUFFIX}) need names no login has")
        if not isinstance(proj, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        for key in sorted(set(proj) - PROJECT_KEYS):
            problems.append(f"{where}.{key}: unknown key")
        for key in ("gid", "read_gid"):
            gid = proj.get(key)
            if not isinstance(gid, int) or isinstance(gid, bool) or gid < uid_min:
                problems.append(f"{where}.{key}: must be an integer >= {uid_min}")
            elif gid in uids:
                problems.append(f"{where}.{key}: {gid} is a person's UID (their per-user group)")
            elif gid in gids:
                problems.append(f"{where}.{key}: {gid} is also {gids[gid]}'s")
            else:
                gids[gid] = f"{name}.{key}"
        seen = {}
        for role in PROJECT_ROLES:
            logins = proj.get(role) or []
            if not isinstance(logins, list):
                problems.append(f"{where}.{role}: must be a list of logins")
                continue
            for login in logins:
                if login not in active:
                    problems.append(f"{where}.{role}: {login} is not an active person of the roster")
                if login in seen:
                    problems.append(f"{where}: {login} is listed as {seen[login]} and {role}; one role each")
                seen.setdefault(login, role)
        if not proj.get("owners"):
            problems.append(f"{where}.owners: at least one owner")
        if not isinstance(proj.get("purpose", ""), str):
            problems.append(f"{where}.purpose: must be text")
    return problems


def people_project_groups(projects):
    """The cluster groups of each project (roster `projects`, checked by check()), for
    directory_groups_all: <name> {gid, owners and members} and <name>-read {read_gid,
    viewers}, so a role change is a membership change and never touches a file."""
    out = []
    for name, p in sorted((projects or {}).items()):
        out.append(
            {
                "name": str(name),
                "gid": p["gid"],
                "members": list(p.get("owners") or []) + list(p.get("members") or []),
            }
        )
        out.append(
            {"name": f"{name}{READ_SUFFIX}", "gid": p["read_gid"], "members": list(p.get("viewers") or [])}
        )
    return out


class EditError(ValueError):
    """A roster edit refused: the reason, or the check's problems, one per line."""


def _round_trip(text):
    from ruamel.yaml import YAML  # only the editors need it, not the deploy's filters

    rt = YAML()
    rt.preserve_quotes = True
    rt.indent(mapping=2, sequence=4, offset=2)
    rt.explicit_start = text.lstrip().startswith("---")
    return rt, rt.load(text)


def _dump_checked(rt, doc, uid_min):
    import io

    out = io.StringIO()
    rt.dump(doc, out)
    problems = check(rt.load(out.getvalue()), uid_min)
    if problems:
        raise EditError("\n".join(problems))
    return out.getvalue()


def roster_add(text, login, account, name=None, slack=None, reserved=(), accounts=None, uid_min=UID_MIN):
    """TEXT (a roster file) with LOGIN added in ACCOUNT, and the UID given: the next after
    the roster's, never one in RESERVED (system account IDs); ACCOUNTS, when given, the
    declared Slurm accounts. The person goes beside their neighbours and every other
    line and comment stays. EditError unless the result passes check()."""
    from ruamel.yaml.comments import CommentedMap

    if accounts is not None and account not in accounts:
        raise EditError(f"account {account} is not declared in the Slurm accounting file")
    rt, doc = _round_trip(text)
    roster = doc["people"]
    if login in roster:
        raise EditError(f"{login} is already in the roster")
    uids = {p["uid"] for p in roster.values() if p and "uid" in p}
    uid = max(uids | {uid_min - 1}) + 1
    while uid in uids or uid in set(reserved):
        uid += 1
    entry = CommentedMap(uid=uid)
    if name:
        entry["name"] = name
    if slack:
        entry["slack"] = slack
    entry["accounts"] = CommentedMap({account: CommentedMap()})
    entry["accounts"][account].fa.set_flow_style()
    roster.insert(sum(1 for other in roster if str(other) < login), login, entry)
    return _dump_checked(rt, doc, uid_min), uid


def roster_leave(text, login, ends=None, uid_min=UID_MIN):
    """TEXT with LOGIN leaving: with ENDS ("YYYY-MM-DD"), their last day (the account stays
    for the grace period, see the module's doc); without, `status: left` at once: no
    roles, accounts or quota, the UID stays reserved."""
    rt, doc = _round_trip(text)
    entry = doc["people"].get(login)
    if entry is None:
        raise EditError(f"{login} is not in the roster")
    if ends is not None:
        try:
            entry["ends"] = _date(ends).isoformat()
        except (TypeError, ValueError):
            raise EditError(f"ends: {ends} is not a date, YYYY-MM-DD") from None
        return _dump_checked(rt, doc, uid_min)
    for name, proj in (doc.get("projects") or {}).items():
        if project_people(proj).get(login):
            _set_role(proj, name, login, None)
    for key in ("roles", "accounts", "default_account", "disk_quota"):
        entry.pop(key, None)
    entry["status"] = "left"
    return _dump_checked(rt, doc, uid_min)


def _project(doc, name):
    proj = (doc.get("projects") or {}).get(name)
    if proj is None:
        raise EditError(f"project {name} is not in the roster")
    return proj


def _set_role(proj, name, login, role):
    """LOGIN takes ROLE in PROJ (None: leaves it); the last owner never goes."""
    from ruamel.yaml.comments import CommentedSeq

    current = project_people(proj).get(login)
    if current == "owners" and role != "owners" and len(proj["owners"]) == 1:
        raise EditError(f"{login} is the only owner of project {name}: make someone else an owner first")
    if current:
        proj[current].remove(login)
        if not proj[current]:
            del proj[current]
    if role:
        if role not in proj:
            proj[role] = CommentedSeq()
            proj[role].fa.set_flow_style()
        proj[role].append(login)
        # The roles stay in their order, whatever order they were added in.
        for key in [k for k in PROJECT_ROLES if k in proj]:
            proj.move_to_end(key)
        if "purpose" in proj:
            proj.move_to_end("purpose")


def roster_project_new(text, name, owners, purpose="", members=(), viewers=(), reserved=(), *, gid_min,
                       uid_min=UID_MIN):  # fmt: skip
    """TEXT with project NAME added: OWNERS (one or more), MEMBERS and VIEWERS, and its two
    GIDs (gid, read_gid) given: the lowest free from GID_MIN (shared_projects_gid_min in
    roles/shared_space_defaults), never a person's UID,
    another project's or one in RESERVED (the site's other cluster groups). Returns
    (text, gid, read_gid); EditError unless check() passes."""
    from ruamel.yaml.comments import CommentedMap

    rt, doc = _round_trip(text)
    if doc.get("projects") is None:
        doc["projects"] = CommentedMap()
    projects = doc["projects"]
    if name in projects:
        raise EditError(f"project {name} is already in the roster")
    taken = {p.get(k) for p in projects.values() for k in ("gid", "read_gid")}
    taken |= {p.get("uid") for p in doc["people"].values() if p} | set(reserved)
    free = []
    gid = gid_min
    while len(free) < 2:
        if gid not in taken:
            free.append(gid)
        gid += 1
    entry = CommentedMap(gid=free[0], read_gid=free[1])
    for role, logins in (("owners", owners), ("members", members), ("viewers", viewers)):
        for login in logins:
            if login not in project_people(entry):  # one role each, the first given
                _set_role(entry, name, login, role)
    if purpose:
        entry["purpose"] = purpose
    projects.insert(sum(1 for other in projects if str(other) < name), name, entry)
    return _dump_checked(rt, doc, uid_min), free[0], free[1]


def roster_project_set(text, name, login, role, uid_min=UID_MIN):
    """TEXT with LOGIN in project NAME as ROLE (owners, members or viewers), added or
    moved; None removes them. The last owner never goes."""
    if role is not None and role not in PROJECT_ROLES:
        raise EditError(f"role {role} is not one of {', '.join(PROJECT_ROLES)}")
    rt, doc = _round_trip(text)
    proj = _project(doc, name)
    current = project_people(proj).get(login)
    if current == role:
        raise EditError(f"{login} is already {role or 'out'} in project {name}")
    _set_role(proj, name, login, role)
    return _dump_checked(rt, doc, uid_min)


def roster_project_purpose(text, name, purpose, uid_min=UID_MIN):
    """TEXT with project NAME's purpose set ("" removes it)."""
    rt, doc = _round_trip(text)
    proj = _project(doc, name)
    if purpose:
        proj["purpose"] = purpose
    else:
        proj.pop("purpose", None)
    return _dump_checked(rt, doc, uid_min)


def roster_project_close(text, name, uid_min=UID_MIN):
    """TEXT without project NAME: its groups go, its folder and files stay for an
    administrator to move to an owner's home (roles/shared_space lists it)."""
    rt, doc = _round_trip(text)
    _project(doc, name)
    del doc["projects"][name]
    return _dump_checked(rt, doc, uid_min)


def people_disk_quotas(people, default=None):
    """{login: limits} for every active person: their own disk_quota over DEFAULT (a
    mapping of QUOTA_KEYS). Someone with neither is left out: not limited."""
    out = {}
    for name, p in sorted(_active(people).items()):
        limits = {**(default or {}), **(p.get("disk_quota") or {})}
        if any(limits.get(k) not in (None, "", 0, "0") for k in QUOTA_KEYS):
            out[name] = limits
    return out


def people_check(roster, uid_min=UID_MIN):
    """The roster unchanged, or an error listing every problem."""
    from ansible.errors import AnsibleFilterError

    problems = check(roster, uid_min)
    if problems:
        raise AnsibleFilterError("invalid people roster:\n  " + "\n  ".join(problems))
    return roster


class FilterModule:
    def filters(self):
        return {
            "people_active": people_active,
            "people_left": people_left,
            "people_former": people_former,
            "people_purge_dates": people_purge_dates,
            "people_purge_due": people_purge_due,
            "people_purge_undated": people_purge_undated,
            "people_purge_targets": people_purge_targets,
            "people_purge_owned": people_purge_owned,
            "people_with_role": people_with_role,
            "people_uids": people_uids,
            "people_attr": people_attr,
            "people_accounting": people_accounting,
            "people_local_accounts": people_local_accounts,
            "people_shells": people_shells,
            "people_subids": people_subids,
            "people_disk_quotas": people_disk_quotas,
            "people_check": people_check,
            "people_project_groups": people_project_groups,
            "people_login_expires": people_login_expires,
            "people_shadow_day": people_shadow_day,
            "people_leaving": people_leaving,
        }
