"""Derive access lists from a people roster: the one place a site records its users.

A roster file declares the roles a site uses and the people:

    roles:                        # role -> {description, requires}
      admin: {description: sudo and job-less SSH to compute nodes}
      docker: {description: Docker daemon socket, requires: admin}
    attributes: [slack]           # extra per-person keys the site uses
    member_keys: [slack]          # what every active Slurm user must set
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
        status: active            # active (default) or left

Every list a role consumes (sudo netgroup, group members, Slurm associations,
chat IDs) is computed from `people` with the filters below, so access is granted
and revoked in one place. Only `active` people get anything; someone who has left
keeps an entry so the UID stays reserved and the account can be locked.

check() is the single validation, used at deploy time through people_check and at
commit time by scripts/deepops/people.py.
"""

import re

STATUSES = ("active", "left")
KEYS = {"uid", "name", "shell", "accounts", "default_account", "roles", "status"}
LOGIN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
UID_MIN = 1000
TOP_KEYS = {"roles", "attributes", "member_keys", "people"}


def _active(people):
    return {n: p or {} for n, p in (people or {}).items() if (p or {}).get("status", "active") == "active"}


def people_active(people):
    """The active part of the roster."""
    return _active(people)


def people_left(people):
    """Login names of people who have left, sorted."""
    return sorted(set(people or {}) - set(_active(people)))


def people_with_role(people, role):
    """Sorted login names of active people holding role."""
    return sorted(n for n, p in _active(people).items() if role in (p.get("roles") or []))


def people_attr(people, key):
    """{login: value} of key for active people who set it."""
    return {n: p[key] for n, p in sorted(_active(people).items()) if p.get(key) not in (None, "")}


def people_accounting(people):
    """{login: {accounts, default_account}} for active people with Slurm accounts.

    This is the `users` part of a slurm_accounting spec.
    """
    out = {}
    for n, p in sorted(_active(people).items()):
        if p.get("accounts"):
            u = {"accounts": {a: o or {} for a, o in p["accounts"].items()}}
            if p.get("default_account"):
                u["default_account"] = p["default_account"]
            out[n] = u
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
    uids = {}
    for name, p in roster["people"].items():
        p = p or {}
        where = f"people.{name}"
        if not isinstance(name, str) or not LOGIN.match(name):
            problems.append(f"{where}: not a valid login name")
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
        accounts = p.get("accounts") or {}
        if not isinstance(accounts, dict):
            problems.append(f"{where}.accounts: must be a mapping of account -> options")
            accounts = {}
        default = p.get("default_account")
        if default is not None and default not in accounts:
            problems.append(f"{where}.default_account: {default} is not one of its accounts")
        if default is None and len(accounts) > 1:
            problems.append(f"{where}.default_account: required with several accounts")
        if status == "left":
            for key in ("roles", "accounts"):
                if p.get(key):
                    problems.append(f"{where}.{key}: must be empty for someone who has left")
        elif accounts:
            for key in member_keys:
                if p.get(key) in (None, ""):
                    problems.append(f"{where}.{key}: required for an active Slurm user")
    return problems


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
            "people_with_role": people_with_role,
            "people_attr": people_attr,
            "people_accounting": people_accounting,
            "people_check": people_check,
        }
