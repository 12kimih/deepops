#!/usr/bin/env python3
"""Install, or check, the Galaxy collections and roles of roles/requirements.yml.

roles/requirements.yml is the one place their versions are set (ansible-core
ships no collections). They go where ansible.cfg looks: collections/ and
roles/galaxy/. An install builds both afresh beside the old ones and then swaps
them in, so nothing outside the requirements is left behind; on Linux the swap is
one atomic exchange, so a playbook running meanwhile never sees them missing.
Installs in one checkout take turns (a lock), so two never remove each other's
staging. A live directory may be a symlink (a worktree borrowing another
checkout's): the install replaces the link, never what it points to. config/requirements.yml (or
$DEEPOPS_CONFIG_DIR/requirements.yml), when a site has one, is installed on top.

Usage:
  galaxy.py            install, unless everything already matches (retries, since
                       Galaxy downloads time out now and then)
  galaxy.py --check    exit 1 unless every pinned collection and role is installed
                       at its pinned version
"""

import ctypes
import errno
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COLLECTIONS = ROOT / "collections"
ROLES = ROOT / "roles" / "galaxy"
# The ansible-galaxy of this interpreter's environment, whatever PATH holds.
GALAXY = shutil.which("ansible-galaxy", path=str(Path(sys.executable).parent)) or "ansible-galaxy"
REQUIREMENTS = [
    ROOT / "roles" / "requirements.yml",
    Path(os.environ.get("DEEPOPS_CONFIG_DIR", ROOT / "config")) / "requirements.yml",
]


def galaxy(kind, req, dest):
    # Point the search path at the staging directory alone: ansible-galaxy otherwise
    # skips whatever the live directory already holds, and the swap would drop it.
    env = dict(os.environ, ANSIBLE_COLLECTIONS_PATH=str(dest), ANSIBLE_ROLES_PATH=str(dest))
    args = [kind, "install", "-r", str(req), "-p", str(dest)]
    for attempt in range(1, 6):
        if subprocess.run([GALAXY, *args], cwd=ROOT, env=env).returncode == 0:
            return
        print(f"ansible-galaxy {args[0]} install failed (attempt {attempt}/5)", file=sys.stderr)
        time.sleep(attempt * 20)
    raise SystemExit(f"ansible-galaxy {args[0]} install failed after 5 attempts")


LOCK = ROOT / ".galaxy.lock"
AT_FDCWD = -100
RENAME_EXCHANGE = 2


def remove(path):
    """Remove path whatever it is, and say so when that fails: a symlink or file is
    unlinked (never what a link points to), a directory removed with its contents."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def exchange(a, b):
    """Swap two existing paths in one step (Linux renameat2 RENAME_EXCHANGE); False
    where the system cannot, so the caller falls back to two renames."""
    if not sys.platform.startswith("linux"):
        return False
    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        return False
    if renameat2(AT_FDCWD, os.fsencode(a), AT_FDCWD, os.fsencode(b), RENAME_EXCHANGE) == 0:
        return True
    err = ctypes.get_errno()
    if err in (errno.EINVAL, errno.ENOSYS):  # a file system or kernel without it
        return False
    raise OSError(err, os.strerror(err), str(a), None, str(b))


def swap(live, new):
    """Put new in live's place and remove what live was."""
    if not (live.exists() or live.is_symlink()):
        new.rename(live)
    elif exchange(new, live):
        remove(new)  # now the old set
    else:
        old = live.with_name(f".{live.name}.old")
        remove(old)
        live.rename(old)
        new.rename(live)
        remove(old)


def install():
    with LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # another install in this checkout finishes first
        return install_locked()


def install_locked():
    if not problems():
        return 0
    staged = {live: live.with_name(f".{live.name}.new") for live in (COLLECTIONS, ROLES)}
    for new in staged.values():
        remove(new)
    for req in (r for r in REQUIREMENTS if r.exists()):
        data = yaml.safe_load(req.read_text()) or {}
        if data.get("collections"):
            galaxy("collection", req, staged[COLLECTIONS])
        if data.get("roles"):
            galaxy("role", req, staged[ROLES])
    for new in staged.values():
        new.mkdir(parents=True, exist_ok=True)
    # Swap in only a complete install; a partial one would replace a working set.
    incomplete = problems(staged[COLLECTIONS], staged[ROLES])
    if incomplete:
        raise SystemExit(
            "staged install is incomplete, left the installed set alone:\n  " + "\n  ".join(incomplete)
        )
    for live, new in staged.items():
        remove(live.with_name(f".{live.name}.old"))  # what an older version left
        swap(live, new)
    return 0


def installed_collections(where=COLLECTIONS):
    result = subprocess.run(
        [GALAXY, "collection", "list", "--format", "json", "-p", str(where)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="replace",
    )
    # Nothing installed yet is an empty answer; a failure is not, and would read as
    # "have none" for every collection.
    if result.returncode != 0 and where.joinpath("ansible_collections").is_dir():
        raise SystemExit(f"ansible-galaxy collection list failed: {result.stderr.strip()}")
    found = {}
    for path, colls in (json.loads(result.stdout or "{}")).items():
        if Path(path).resolve() == (where / "ansible_collections").resolve():
            found.update({name: info["version"] for name, info in colls.items()})
    return found


def installed_role(name, where=ROLES):
    info = where / name / "meta" / ".galaxy_install_info"
    return (yaml.safe_load(info.read_text()) or {}).get("version") if info.exists() else None


def problems(collections=COLLECTIONS, roles=ROLES):
    have, found = installed_collections(collections), []
    if not REQUIREMENTS[0].exists():  # the site's list is optional, ours is not
        found.append(f"{REQUIREMENTS[0].relative_to(ROOT)} is missing")
    for req in (r for r in REQUIREMENTS if r.exists()):
        data = yaml.safe_load(req.read_text()) or {}
        for c in data.get("collections") or []:
            want = str(c.get("version", have.get(c["name"], "any")))
            if c["name"] not in have or have[c["name"]] != want:
                found.append(f"collection {c['name']}: want {want}, have {have.get(c['name'], 'none')}")
        for r in data.get("roles") or []:
            name = r.get("name", r["src"])
            got = installed_role(name, roles)
            want = str(r.get("version", got or "any"))
            if got is None or got != want:
                found.append(f"role {name}: want {want}, have {got or 'none'}")
    return found


def check():
    found = problems()
    for p in found:
        print(p)
    if found:
        print("Install them with: uv run scripts/deepops/galaxy.py")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv[1:] else install())
