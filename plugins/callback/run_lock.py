"""run_lock: one run that changes hosts at a time per inventory.

Two runs at once interfere: a rolling upgrade rebooted a node while a Slurm deploy was
configuring it, and the deploy lost the node midway (2026-11-23). A run of
ansible-playbook that is not --check takes an exclusive lock for its whole life (an
flock, released by the kernel when the process ends, however it ends); a second one
against the same inventory stops before its first play and names the run holding it.
Previews (--check), ad-hoc commands and runs against another inventory (molecule's)
take no lock. The locks live in the operator's own ~/.cache/deepops/run-locks (0700),
like the fact cache: in a directory others can write, any user could hold one and stop
every deploy.

ansible.cfg loads this file (callback_plugins = ./plugins/callback); it needs no
enabling.
"""

from __future__ import annotations

import datetime
import fcntl
import getpass
import hashlib
import os
import sys

from ansible import context
from ansible.plugins.callback import CallbackBase
from ansible.utils.display import Display

DOCUMENTATION = """
name: run_lock
type: aggregate
short_description: one run that changes hosts at a time per inventory
description:
  - A run of ansible-playbook without --check holds an exclusive lock on its inventory
    for its whole life; a second one stops before its first play, naming the holder.
"""

display = Display()
ADHOC = "__adhoc_playbook__"  # the name ansible's ad-hoc CLI gives its playbook


LOCKS = os.path.expanduser("~/.cache/deepops/run-locks")


def lock_path(inventory, base=LOCKS):
    """The lock of a set of inventory sources (paths or host lists), the same for every
    spelling of the same paths."""
    sources = sorted(os.path.realpath(s) if os.path.exists(s) else s for s in inventory or ())
    digest = hashlib.sha256("\0".join(sources).encode()).hexdigest()[:16]
    return os.path.join(base, f"{digest}.lock")


def acquire(path, holder):
    """The open lock, or the text of the run holding it."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        with open(fd, closefd=True) as held:
            return None, held.read().strip() or "a run that did not say who it is"
    os.ftruncate(fd, 0)
    os.pwrite(fd, holder.encode(), 0)
    return fd, None


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "run_lock"
    CALLBACK_NEEDS_ENABLED = False

    def __init__(self):
        super().__init__()
        self._fd = None  # held open, and so locked, until the process ends

    def v2_playbook_on_start(self, playbook):
        if self._fd is not None or getattr(playbook, "_file_name", "") == ADHOC:
            return
        if context.CLIARGS.get("check"):
            return
        path = lock_path(context.CLIARGS.get("inventory"))
        started = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        holder = f"pid {os.getpid()}, {getpass.getuser()}, since {started}: {' '.join(sys.argv)}"
        self._fd, held_by = acquire(path, holder)
        if held_by is not None:
            display.error(
                f"Another run against this inventory is under way ({held_by}). Two runs at"
                " once interfere (a reboot in one drops the host from the other): wait"
                f" for it to end, then run again. Previews (--check) take no lock. {path}"
            )
            # Raised past the callback dispatcher, which turns an Exception into a warning.
            raise SystemExit(4)
