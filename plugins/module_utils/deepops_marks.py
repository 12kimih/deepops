"""Whether deepops made a file: the one convention its roles follow, for every module
that must tell deepops' files from a site's (marked_file's and component_record's
removals, the stale_files audit).

A deepops role says so in what it writes: the role by its path ("# Managed by
roles/node_user_policy"), or "DeepOps", or puts the file under a name or directory that
says deepops. A template's "Ansible managed" header (ansible_managed) is every Ansible's
default, a site's own playbooks' too: it marks a file only for the audit, which removes
nothing (text_marked), never for a removal (made_by).
"""

import os
import re

MARK = re.compile(r"(?i)ansible managed|deepops|\broles/[a-z0-9_-]+")
DEEPOPS = re.compile(r"(?i)deepops")
LIMIT = 1 << 20


def _search(pattern, path):
    """Whether PATTERN occurs in the first LIMIT bytes of PATH."""
    try:
        with open(path, errors="replace") as f:
            return pattern.search(f.read(LIMIT)) is not None
    except OSError:
        return False


def text_marked(path):
    """Whether PATH's text has any mark, "Ansible managed" included: for an audit only."""
    return _search(MARK, path)


def role_marked(path, role):
    """Whether PATH's text names roles/ROLE (roles/ROLE-x or roles/ROLE_x is another)."""
    return bool(role) and _search(re.compile(r"\broles/" + re.escape(role) + r"(?![\w-])"), path)


def named_deepops(path):
    """Whether PATH's own name or one of its directories says deepops."""
    return any("deepops" in part.lower() for part in path.split(os.sep))


def made_by(path, role):
    """Whether PATH exists and shows deepops made it, so a removal may take it: its name
    or its text says deepops, or its text names ROLE, the role that writes it."""
    return os.path.lexists(path) and (
        named_deepops(path) or _search(DEEPOPS, path) or role_marked(path, role)
    )
