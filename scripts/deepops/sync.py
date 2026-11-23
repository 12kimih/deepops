#!/usr/bin/env python3
"""Bring the shared checkouts (this clone and its site config) up to their remote, never
under a running deploy.

A deploy reads roles, templates and config files from these checkouts while it runs,
some of them only when a task reaches them; moving the checkout under it can hand it a
mix of two commits. That happened twice in one evening (2026-11-23), each time from a
hand-written "is a run going?" check that was wrong. This takes the same per-inventory
locks a deploy holds for its whole life (plugins/callback/run_lock.py), so it refuses
while any deploy runs, and no deploy can start while it moves the checkouts.

Each checkout moves only forward (git merge --ff-only) and only when its tracked files
are unchanged; anything else is said and left as it is.

  sync.py            fast-forward this clone and its config to origin's branch
  sync.py --dry-run  say what would move, move nothing
"""

import importlib.util
import os
import subprocess
import sys
from glob import glob
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def run_lock():
    """plugins/callback/run_lock.py, the one implementation of the deploy lock."""
    spec = importlib.util.spec_from_file_location("run_lock", ROOT / "plugins" / "callback" / "run_lock.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace", check=check
    )


def hold_deploy_locks(lock) -> tuple[list[int], list[str]]:
    """Every deploy lock there is, held; or the runs holding some."""
    paths = set(glob(os.path.join(lock.LOCKS, "*.lock")))
    held, busy = [], []
    holder = f"pid {os.getpid()}, scripts/deepops/sync.py moving the shared checkouts"
    for path in sorted(paths):
        fd, by = lock.acquire(path, holder)
        if fd is None:
            busy.append(by)
        else:
            held.append(fd)
    return held, busy


def forward(repo: Path, dry_run: bool) -> str:
    """Fast-forward REPO's checked-out branch to its upstream; what happened, in words."""
    name = repo.name if repo != ROOT else "deepops"
    branch = git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    if not branch:
        return f"{name}: detached HEAD, left alone"
    if git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return f"{name}: tracked files changed here, left alone (commit or discard them first)"
    git(repo, "fetch", "--quiet", "origin")
    upstream = f"origin/{branch}"
    old = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    new = git(repo, "rev-parse", "--short", upstream).stdout.strip()
    if old == new:
        return f"{name}: up to date at {old}"
    if git(repo, "merge-base", "--is-ancestor", "HEAD", upstream, check=False).returncode != 0:
        return f"{name}: {branch} has commits {upstream} lacks, left alone"
    if dry_run:
        return f"{name}: would move {old} -> {new}"
    git(repo, "merge", "--ff-only", "--quiet", upstream)
    return f"{name}: moved {old} -> {new}"


def main(argv: list[str]) -> int:
    parser = cli.parser(__doc__)
    parser.add_argument("--dry-run", action="store_true", help="say what would move, move nothing")
    opts = parser.parse_args(argv[1:])

    held, busy = hold_deploy_locks(run_lock())
    try:
        if busy:
            for by in busy:
                print(f"sync: a deploy is running ({by}); not moving the checkouts", file=sys.stderr)
            return 1
        repos = [ROOT] + ([ROOT / "config"] if (ROOT / "config" / ".git").exists() else [])
        for repo in repos:
            print(forward(repo, opts.dry_run))
        return 0
    finally:
        for fd in held:
            os.close(fd)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
