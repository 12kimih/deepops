"""bot_serve: the one forced command of the cluster bot's SSH key on a host. The
request's first word names a service, the rest is that service's own request.

    cluster-bot-serve DIR     (as root, through one sudo rule; from SSH_ORIGINAL_COMMAND)

DIR holds a file per service a role offers the bot on this host, <service>.json:
{"argv": [command, ...]}. The command runs with SSH_ORIGINAL_COMMAND set to the rest of
the request, so each service parses and limits its own (scratch-alloc ... serve, for
one); nothing is run through a shell. A service file must be root's and writable by
nobody else, or the request is refused: the files decide what root runs.

A service reads its request with request(), the one way to split it: as a shell
would split words (a quoted reason stays one), never run by one, and only operations
with the number of arguments the service allows.

Roles offer and withdraw services through roles/cluster_bot_access, which owns this
command, its sudo rule and the bot's authorized key. Python 3.9 or newer, standard
library only.
"""

import json
import os
import re
import shlex
import stat
import sys

from . import proc

NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
# The environment a service starts with: nothing of the caller's but the request.
PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class Error(Exception):
    """A request this host does not serve, said plainly."""


def request(original, arity):
    """A service's request (SSH_ORIGINAL_COMMAND) as words, or an Error. ARITY maps
    each operation the bot may ask for to the argument counts it takes."""
    words = shlex.split(original or "")
    if not words or len(words) - 1 not in arity.get(words[0], ()):
        raise Error(f"not a request this key may make: {original!r}")
    return words


def resolve(directory, original, owner=0):
    """(argv, the service's request) for a request over SSH, or an Error. OWNER is the
    only user the directory and the service file may belong to (root)."""
    words = shlex.split(original or "")
    if not words or not NAME.fullmatch(words[0]):
        raise Error(f"not a request: {original!r}")
    path = os.path.join(directory, words[0] + ".json")
    for each, kind in ((directory, stat.S_ISDIR), (path, stat.S_ISREG)):
        try:
            st = os.lstat(each)
        except FileNotFoundError:
            raise Error(f"this host offers no {words[0]!r}") from None
        if st.st_uid != owner or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or not kind(st.st_mode):
            raise Error(f"{each} must be root's, writable by root alone, and not a link")
    with open(path) as f:
        argv = json.load(f).get("argv")
    if not argv or not all(isinstance(a, str) for a in argv) or not os.path.isabs(argv[0]):
        raise Error(f"{path}: argv must be a list of words starting with an absolute path")
    return argv, shlex.join(words[1:])


def main(argv=None):
    args = (sys.argv if argv is None else argv)[1:]
    if len(args) != 1:
        print("usage: cluster-bot-serve DIR  (the request in SSH_ORIGINAL_COMMAND)", file=sys.stderr)
        return 2
    try:
        command, request = resolve(args[0], os.environ.get("SSH_ORIGINAL_COMMAND"))
    except (Error, OSError, ValueError) as e:
        print(f"cluster-bot-serve: {e}", file=sys.stderr)
        return 2
    proc.become(command, {"PATH": PATH, "LANG": "C.UTF-8", "SSH_ORIGINAL_COMMAND": request})


if __name__ == "__main__":
    sys.exit(main())
