#!/usr/bin/env python3
"""The cluster_tools package as one release: built once on the controller, unpacked
and switched to on each host (roles/cluster_tools_lib, tasks/release.yml).

    release.py build OUT_DIR MODULE...       the archive; prints {"path", "hash"}
    release.py unpack ARCHIVE RELEASE_DIR    RELEASE_DIR/cluster_tools, compiled
    release.py switch LIVE RELEASE_DIR KEEP  LIVE -> RELEASE_DIR/cluster_tools, atomically

Copying the package's modules one by one cost every run minutes on nine hosts even
when nothing changed (ARA 285: 3m28s for the config check's copy, 2m14s for the
install). One archive is one checksum compared per host, and one transfer when the
package changed.

build is deterministic: the same modules give the same bytes (sorted names, fixed
owner, mode and time, gzip without a timestamp), so its hash names the release.
unpack writes beside the release and renames it into place, so a half-unpacked
release is never one; the bytecode is compiled there by the host's python, as the
commands' users cannot write it into a root-owned tree. switch replaces LIVE (the
first time a real directory, then a symbolic link) in one renameat2(RENAME_EXCHANGE):
a command importing the package at that instant finds the old or the new one, never
none. It then removes every release in its directory but the new one and KEEP, the
one LIVE held before, for a rollback. Python 3.9 or newer, standard library only.
"""

import compileall
import ctypes
import gzip
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile

PACKAGE = "cluster_tools"
AT_FDCWD = -100
RENAME_EXCHANGE = 2


def archive(modules):
    """The release archive's bytes for MODULES (paths), and its hash."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        top = tarfile.TarInfo(PACKAGE)
        top.type, top.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(top)
        for path in sorted(modules, key=os.path.basename):
            with open(path, "rb") as f:
                data = f.read()
            info = tarfile.TarInfo(f"{PACKAGE}/{os.path.basename(path)}")
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
    data = gzip.compress(raw.getvalue(), compresslevel=9, mtime=0)
    return data, hashlib.sha256(data).hexdigest()[:16]


def build(out_dir, modules):
    data, digest = archive(modules)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{PACKAGE}-{digest}.tar.gz")
    if not os.path.exists(path):
        tmp = f"{path}.{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    print(json.dumps({"path": path, "hash": digest}))
    return 0


def unpack(archive_path, release_dir):
    if os.path.isdir(os.path.join(release_dir, PACKAGE)):
        return 0
    tmp = f"{release_dir}.unpacking"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    with tarfile.open(archive_path) as tar:
        members = tar.getmembers()
        if any(not (m.name == PACKAGE or m.name.startswith(PACKAGE + "/")) or ".." in m.name.split("/")
               or not (m.isfile() or m.isdir()) for m in members):  # fmt: skip
            raise SystemExit(f"release.py: {archive_path} holds more than the package")
        # The data filter where Python has it (3.12, and the 3.9+ security releases).
        extra = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tar.extractall(tmp, members=members, **extra)
    if not compileall.compile_dir(os.path.join(tmp, PACKAGE), quiet=1):
        raise SystemExit(f"release.py: {archive_path} does not compile")
    os.rename(tmp, release_dir)
    return 0


def exchange(a, b):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(AT_FDCWD, os.fsencode(a), AT_FDCWD, os.fsencode(b), RENAME_EXCHANGE) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), b)


def switch(live, release_dir, keep):
    target = os.path.join(release_dir, PACKAGE)
    if not os.path.isdir(target):
        raise SystemExit(f"release.py: {target} is not unpacked")
    new = f"{live}.switching"
    if os.path.lexists(new):
        os.unlink(new) if os.path.islink(new) else shutil.rmtree(new)
    os.symlink(target, new)
    if os.path.lexists(live):
        exchange(new, live)
        os.unlink(new) if os.path.islink(new) else shutil.rmtree(new)  # the previous one
    else:
        os.rename(new, live)
    releases = os.path.dirname(release_dir)
    wanted = {os.path.basename(release_dir), keep}
    for name in os.listdir(releases):
        digest = name[len(PACKAGE) + 1 : -len(".tar.gz")] if name.endswith(".tar.gz") else name
        if digest not in wanted:
            path = os.path.join(releases, name)
            shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.unlink(path)
    return 0


def main(argv):
    if len(argv) >= 3 and argv[0] == "build":
        return build(argv[1], argv[2:])
    if len(argv) == 3 and argv[0] == "unpack":
        return unpack(argv[1], argv[2])
    if len(argv) == 4 and argv[0] == "switch":
        return switch(argv[1], argv[2], argv[3])
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
