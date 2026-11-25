#!/usr/bin/env python3
"""Bring the shared checkouts (this clone and its site config) up to their remote, never
under a running deploy.

A deploy reads roles, templates and config files from these checkouts while it runs,
some of them only when a task reaches them; moving the checkout under it can hand it a
mix of two commits. That happened twice in one evening (2026-11-23), each time from a
hand-written "is a run going?" check that was wrong. This takes the same per-inventory
locks a deploy holds for its whole life (scripts/deepops/deploy_lock.py), so it refuses
while any deploy runs, and no deploy can start while it moves the checkouts.

Each checkout moves only forward (git merge --ff-only) and only when its tracked files
are unchanged; anything else is said and left as it is. When one moves, it says which
hosts' expected component versions changed and which playbooks install them, prometheus.yml
among them so the ComponentVersionNotPinned alert expects them (component_pins.py): a pin
bump cannot pass unseen.

  sync.py            fast-forward this clone and its config to origin's branch
  sync.py --dry-run  say what would move, move nothing
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
import deploy_lock  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace", check=check
    )


def forward(repo: Path, dry_run: bool) -> tuple[str, str, str]:
    """Fast-forward REPO's checked-out branch to its upstream: (what happened, in words,
    the commit it was at, the commit it is at now or would be at: equal when it stays)."""
    name = repo.name if repo != ROOT else "deepops"
    head = git(repo, "rev-parse", "HEAD", check=False).stdout.strip()
    branch = git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    if not branch:
        return f"{name}: detached HEAD, left alone", head, head
    if git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return f"{name}: tracked files changed here, left alone (commit or discard them first)", head, head
    git(repo, "fetch", "--quiet", "origin")
    upstream = f"origin/{branch}"
    new = git(repo, "rev-parse", upstream).stdout.strip()
    if head == new:
        return f"{name}: up to date at {head[:8]}", head, head
    if git(repo, "merge-base", "--is-ancestor", "HEAD", upstream, check=False).returncode != 0:
        return f"{name}: {branch} has commits {upstream} lacks, left alone", head, head
    if dry_run:
        return f"{name}: would move {head[:8]} -> {new[:8]}", head, new
    git(repo, "merge", "--ff-only", "--quiet", upstream)
    return f"{name}: moved {head[:8]} -> {new[:8]}", head, new


def pins_report(config: Path, revs: dict, dry_run: bool) -> list[str]:
    """What the move changes in each host's expected component versions, and the
    playbooks that install them. REVS: {repo: (old, new)}. After a move the new side is
    the working trees; a dry run reads the commits it would move to."""
    try:
        import component_pins
        import component_playbooks
    except ImportError as e:  # run without the repository's environment (uv run)
        return [f"component versions: not worked out ({e}; run it as uv run {Path(__file__).name})"]
    (old, new), (cold, cnew) = revs[ROOT], revs.get(config, (None, None))
    before = component_pins.state(config, old, cold)
    after = component_pins.state(
        config, new if dry_run else None, (cnew if dry_run else None) if cold else None
    )
    return component_pins.describe(component_pins.changes(before, after), component_playbooks.playbooks())


def main(argv: list[str]) -> int:
    parser = cli.parser(__doc__)
    parser.add_argument("--dry-run", action="store_true", help="say what would move, move nothing")
    opts = parser.parse_args(argv[1:])

    held, busy = deploy_lock.hold_all(
        f"pid {os.getpid()}, scripts/deepops/sync.py moving the shared checkouts"
    )
    try:
        if busy:
            for by in busy:
                print(f"sync: a deploy is running ({by}); not moving the checkouts", file=sys.stderr)
            return 1
        repos = [ROOT] + ([ROOT / "config"] if (ROOT / "config" / ".git").exists() else [])
        revs = {}
        for repo in repos:
            said, old, new = forward(repo, opts.dry_run)
            print(said)
            revs[repo] = (old, new)
        if any(old != new for old, new in revs.values()):
            print("\n".join(pins_report(ROOT / "config", revs, opts.dry_run)))
        return 0
    finally:
        for fd in held:
            os.close(fd)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
