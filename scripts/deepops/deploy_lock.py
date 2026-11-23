"""The per-inventory deploy lock, standard library only: plugins/callback/run_lock.py
takes it for a run's whole life, and scripts/deepops/sync.py takes every one of them
before it moves the shared checkouts, so neither runs under the other.

An flock, released by the kernel when its process ends however it ends, in the
operator's own ~/.cache/deepops/run-locks (0700): in a directory others can write, any
user could hold one and stop every deploy."""

import fcntl
import glob
import hashlib
import os

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


def hold_all(holder, base=None):
    """Every deploy lock there is, held (their open descriptors), or the runs holding
    some (their texts); a caller holding none of them releases what it got."""
    held, busy = [], []
    for path in sorted(glob.glob(os.path.join(base or LOCKS, "*.lock"))):
        fd, by = acquire(path, holder)
        if fd is None:
            busy.append(by)
        else:
            held.append(fd)
    return held, busy
