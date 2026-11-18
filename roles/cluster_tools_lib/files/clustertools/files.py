"""files: writing a file so a reader sees the old one or the new one, never half of it.
Python 3.9 or newer, standard library only.
"""

import os
import tempfile


def write_atomic(path, text, mode=0o644):
    """Replace PATH with TEXT: a temporary file in the same directory, fsync, rename."""
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
