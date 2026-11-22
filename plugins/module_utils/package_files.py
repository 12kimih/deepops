"""What the host's packages and deepops' staged installs own: the one reading of dpkg,
rpm, ucf and the staged_install manifests, for every module that must not take a path
something else owns (stale_files' removals, staged_install's overwrites).

    owned = {canonical(p) for p in owned_paths() | staged_paths(manifests)}
    canonical(path) in owned
"""

import glob
import json
import os
import subprocess
import sys

UCF_REGISTRY = "/var/lib/ucf/registry"
# Paths read from a file or a command are decoded as the OS decodes file names
# (os.fsdecode), so a name that is not UTF-8 equals the one os.walk yields: decoded with
# errors="replace", a package's own file would not match itself and count as unowned.
# How long a whole-database query (rpm -qal, dpkg --verify) may take: a held rpm lock
# or a hung NFS path must end the run with an error, not hold it forever.
QUERY_TIMEOUT = 900
FS = {"encoding": sys.getfilesystemencoding(), "errors": sys.getfilesystemencodeerrors()}


def owned_paths():
    """Every path a package owns (dpkg, else rpm). On Debian that includes the
    configuration files a package's scripts keep through ucf (/etc/ssh/sshd_config,
    /etc/default/grub): dpkg does not list them, ucf's registry does."""
    paths = set()
    info = "/var/lib/dpkg/info"
    if os.path.isdir(info):
        for name in os.listdir(info):
            if name.endswith(".list"):
                with open(os.path.join(info, name), **FS) as listing:
                    paths.update(line.rstrip("\n") for line in listing if line.strip())
        if os.path.isfile(UCF_REGISTRY):
            with open(UCF_REGISTRY, **FS) as registry:
                paths.update(line.split()[-1] for line in registry if len(line.split()) > 1)
        return paths
    try:
        out = subprocess.run(
            ["rpm", "-qal"], capture_output=True, check=False, timeout=QUERY_TIMEOUT, **FS
        ).stdout
    except OSError:  # no rpm either: nothing is owned
        return paths
    paths.update(line for line in out.splitlines() if line.startswith("/"))
    return paths


def staged_paths(manifests):
    """Every file and directory a staged install's manifest in MANIFESTS lists."""
    paths = set()
    if not manifests:
        return paths
    for manifest in glob.glob(os.path.join(manifests, "*.json")):
        files, dirs = manifest_paths(manifest)
        paths.update(files, dirs)
    return paths


def manifest_paths(manifest):
    """The files and the directories one staged_install manifest lists (none if absent)."""
    try:
        with open(manifest, **FS) as f:
            data = json.load(f)
    except FileNotFoundError:
        return set(), set()
    return set(data.get("files", [])), set(data.get("dirs", []))


# Merged /usr: /lib, /bin and /sbin are links into /usr, and dpkg lists a file under
# whichever name its package was built with (a kernel's modules under /lib/modules, a
# newer one's under /usr/lib/modules). Every path is compared spelled one way, or an
# installed kernel's module tree reads as nobody's.
MERGED = tuple(f"/{d}" for d in ("bin", "sbin", "lib", "lib32", "lib64", "libx32") if os.path.islink(f"/{d}"))


def canonical(path):
    """PATH with a merged-/usr alias spelled through /usr (/lib/x -> /usr/lib/x)."""
    for alias in MERGED:
        if path == alias or path.startswith(alias + "/"):
            return "/usr" + path
    return path
