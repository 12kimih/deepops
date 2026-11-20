"""Whether deepops made a file: the one convention its roles follow, for every module
that must tell deepops' files from a site's (component_record's adoption, the
stale_files audit).

A deepops role says so in what it writes: "Ansible managed" (ansible_managed in a
template's header), "DeepOps", or the role by its path ("# roles/nvidia_driver: ..."),
or puts the file under a name or directory that says deepops.
"""

import os
import re

MARK = re.compile(r"(?i)ansible managed|deepops|\broles/[a-z0-9_-]+")


def text_marked(path, limit=1 << 20):
    """Whether the first LIMIT bytes of PATH name deepops or a deepops role."""
    try:
        with open(path, errors="replace") as f:
            return MARK.search(f.read(limit)) is not None
    except OSError:
        return False


def named_deepops(path):
    """Whether PATH's own name or one of its directories says deepops."""
    return any("deepops" in part.lower() for part in path.split(os.sep))


def made_by_deepops(path):
    """Whether PATH exists and its name or its text says a deepops role wrote it."""
    return os.path.lexists(path) and (named_deepops(path) or text_marked(path))
