"""The bot's state files (held requests, reminders sent, people's choices): small JSON
documents read whole and replaced atomically, so a crash mid-write leaves the previous
one, never half of it.

    read(path, default)          -> the document, or DEFAULT when absent or unreadable
    write(path, data, mode=0o600)   its directory is made (0700) when missing
"""

import json
import os
import tempfile
from contextlib import suppress


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
