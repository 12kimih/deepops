"""The repository's files as git tracks them: the one listing every tool and test reads.

    tracked(*pathspecs, root=ROOT)  -> sorted paths of tracked files present in the tree

What a commit holds, staged files included, never what git ignores or does not know:
Galaxy's roles under roles/galaxy, environments, caches, another session's unfinished
file in a shared tree. A tool that globs the tree sees those too and passes or fails on
what is not the repository. A file deleted but not yet staged is left out, so a check
never fails on a file that is gone. A pathspec's * also matches /, so roles/*/tasks/*.yml
includes subdirectories. Standard library only.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def tracked(*pathspecs, root=ROOT):
    # surrogateescape: a name that is not UTF-8 comes back as the same bytes (os.fsdecode).
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *pathspecs],
        cwd=root,
        capture_output=True,
        text=True,
        errors="surrogateescape",
        check=True,
    ).stdout
    return [root / name for name in sorted(filter(None, out.split("\0"))) if (root / name).is_file()]
