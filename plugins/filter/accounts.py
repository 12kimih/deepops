"""Local accounts with fixed IDs (roles/local_accounts): their one validation.

    local_accounts:           # name -> declaration
      slurm:
        uid: 22078            # required
        gid: 22078            # default: uid; the account's own group, named like it
        home: /local/slurm    # optional
        shell: /bin/bash      # default: the role's local_accounts_shell
        comment: Slurm        # optional
        system: true          # default: true
        create_home: true     # default: true

local_accounts_check returns the declarations with every default filled in, or fails
listing every problem: a bad name, a missing or non-integer ID, an unknown key, or an
ID two accounts share. accounts_disjoint fails when two sets of accounts share a UID or
GID (the people roster against the service accounts). private_group says whether a
group is one user's own (named like the user, no other member): the only group a
directory may be writable by when what is in it stands for root's word
(roles/textfile_keep, tasks_from: expected).
"""

import re

LOGIN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
KEYS = {"uid", "gid", "home", "shell", "comment", "system", "create_home"}


def _id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def check(accounts):
    """Problems in a local_accounts mapping, as a list of messages; empty means valid."""
    if not isinstance(accounts, dict):
        return ["local_accounts must be a mapping of name -> {uid, ...}"]
    problems, uids, gids = [], {}, {}
    for name, a in accounts.items():
        if not isinstance(name, str) or not LOGIN.match(name):
            problems.append(f"{name}: not a valid account name")
        if not isinstance(a, dict):
            problems.append(f"{name}: must be a mapping")
            continue
        problems += [f"{name}.{key}: unknown key" for key in sorted(set(a) - KEYS)]
        uid, gid = a.get("uid"), a.get("gid", a.get("uid"))
        for key, value, seen in (("uid", uid, uids), ("gid", gid, gids)):
            if not _id(value):
                problems.append(f"{name}.{key}: must be a non-negative integer")
            elif value in seen:
                problems.append(f"{name}.{key}: {value} is also {seen[value]}'s")
            else:
                seen[value] = name
    return problems


def _fail(message, problems):
    from ansible.errors import AnsibleFilterError

    raise AnsibleFilterError(message + ":\n  " + "\n  ".join(problems))


def local_accounts_check(accounts, shell="/usr/sbin/nologin"):
    """The declarations with defaults filled in, or an error listing every problem."""
    problems = check(accounts)
    if problems:
        _fail("invalid local_accounts", problems)
    return {
        name: {"gid": a["uid"], "shell": shell, "system": True, "create_home": True, **a}
        for name, a in accounts.items()
    }


def overlap(first, second):
    """What two mappings of accounts share: a name, or an ID (UID or GID, the group's ID
    defaulting to the UID). Either one would make two accounts one."""

    def ids(accounts):
        return {n: {a["uid"], a.get("gid", a["uid"])} for n, a in (accounts or {}).items()}

    found = [f"{name} is the name of both" for name in sorted(set(first or {}) & set(second or {}))]
    for name, mine in ids(first).items():
        for other, theirs in ids(second).items():
            found += [f"{name} and {other} both use ID {i}" for i in sorted(mine & theirs)]
    return found


def accounts_disjoint(first, second):
    """FIRST unchanged, or an error when it shares a name, UID or GID with SECOND."""
    problems = overlap(first, second)
    if problems:
        _fail("accounts share a name or an ID", problems)
    return first


def private_group(entry, name, user):
    """Whether group NAME, as getent reads it (ENTRY: [password, gid, members] or None
    when there is no such group), is USER's own: named like USER, with no member but
    USER. A shared primary group (users, a lab's) lets every member write."""
    if not entry or name != user or len(entry) < 3:
        return False
    members = {m for m in str(entry[2] or "").split(",") if m}
    return members <= {user}


class FilterModule:
    def filters(self):
        return {
            "local_accounts_check": local_accounts_check,
            "accounts_disjoint": accounts_disjoint,
            "private_group": private_group,
        }
