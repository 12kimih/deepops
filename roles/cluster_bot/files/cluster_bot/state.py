"""The bot's state files (held requests, reminders sent, people's choices): small JSON
documents read whole and replaced atomically, so a crash mid-write leaves the previous
one, never half of it.

    read(path, default)          -> the document, or DEFAULT when absent or unreadable
    write(path, data, mode=0o600)   its directory is made (0700) when missing
    with locked(path, default, mode) as doc:   change DOC (a dict) in place; written at
                                 the end, under one lock for the whole read, change, write

The bot's handlers run on threads (slack_bolt), and a second process may run beside it:
two read-change-write cycles that interleave lose one (a request approved twice is
applied twice). Every cycle goes through locked(): a lock per file within the process
and an flock on <path>.lock between processes.
"""

import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager, suppress

_LOCKS = {}
_GUARD = threading.Lock()


def read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write(path, data, mode=0o600):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")  # 0600 from the start
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


@contextmanager
def locked(path, default, mode=0o600):
    """The document at PATH (DEFAULT, a dict, when absent), held under the file's lock
    while the caller changes it in place, then written atomically; nothing is written
    when the caller raises."""
    key = os.path.abspath(path)
    with _GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        os.makedirs(os.path.dirname(key) or ".", mode=0o700, exist_ok=True)
        with open(f"{key}.lock", "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            doc = read(path, default)
            if not isinstance(doc, dict):
                doc = default
            yield doc
            write(path, doc, mode)
