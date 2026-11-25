"""A test's scratch directory on this machine's own disk or memory, never under a home.

The checks run on a login node too, whose /home is the shared NFS disk: a test writing
there is slow (Grafana's first start took 35 s there, 6 s in memory) and loads the disk
everyone shares, which the cluster's I/O rules forbid. So a test keeps what it writes in

    directory(prefix)               the login's /tmp (tempfile's), a local disk
    directory(prefix, docker=True)  $XDG_RUNTIME_DIR (the login's tmpfs under /run/user)
                                    when set, else /tmp: a directory the Docker daemon
                                    can bind-mount, since it reads a source in its own
                                    mount namespace, where a login's private /tmp
                                    (roles/login_private_dirs) is not

and removes it when done (the caller's addCleanup or tearDown: remove(path)).
tests/local-scratch checks that no test names a home directory itself.
"""

import os
import shutil
import tempfile
from pathlib import Path

SHARED_FILESYSTEMS = ("nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "lustre", "beegfs", "gpfs")


def filesystem(path, mounts=None):
    """The type of the filesystem PATH lies on, from MOUNTS (/proc/self/mounts' text by
    default; '' when unknown)."""
    best, kind = "", ""
    if mounts is None:
        try:
            mounts = Path("/proc/self/mounts").read_text()
        except OSError:
            return ""
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        point = fields[1].replace("\\040", " ")
        if (path == point or path.startswith(point.rstrip("/") + "/")) and len(point) >= len(best):
            best, kind = point, fields[2]
    return kind


def base(docker=False, mounts=None):
    """Where a test's scratch directories go; ValueError when that is a shared filesystem."""
    where = (docker and os.environ.get("XDG_RUNTIME_DIR")) or tempfile.gettempdir()
    real = os.path.realpath(where)
    if filesystem(real, mounts) in SHARED_FILESYSTEMS:
        raise ValueError(f"{real} is on a shared filesystem: set TMPDIR to a local directory")
    return real


def directory(prefix, docker=False):
    """A new directory, mode 0700, for one test; remove(path) when done."""
    path = Path(tempfile.mkdtemp(prefix=f"deepops-{prefix}-", dir=base(docker)))
    path.chmod(0o700)
    return path


def remove(path):
    shutil.rmtree(path, ignore_errors=True)
