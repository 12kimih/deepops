"""files: writing a file so a reader sees the old one or the new one, never half of it.
Python 3.9 or newer, standard library only.
"""

import os
import tempfile


def write_atomic(path, text, mode=0o644):
    """Replace PATH with TEXT: a temporary file in the same directory, fsync, rename,
    then fsync the directory, so the rename itself survives a power cut."""
    directory = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}-", dir=directory)
    except OSError as e:  # name the file asked for, not the temporary one
        raise OSError(e.errno, e.strerror, path) from None
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    dfd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def write_textfile(path, text, kept=""):
    """A node_exporter textfile: PATH, in node_exporter's textfile directory (a tmpfs,
    empty after a boot), and KEPT, its copy on disk, which roles/textfile_keep puts back
    into PATH at boot. Written whenever the metrics are, so a reboot after a daily run
    keeps that run's result (and its last success) instead of losing the series until
    the next run. Without KEPT, only PATH. Each is atomic; missing directories are made."""
    for target in (kept, path):
        if target:
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            write_atomic(target, text)


def read_textfile(path, kept=""):
    """The metrics last written (write_textfile): PATH, else KEPT (after a boot, before
    the restore), else ""."""
    for source in (path, kept):
        if source and os.path.exists(source):
            with open(source) as f:
                return f.read()
    return ""
