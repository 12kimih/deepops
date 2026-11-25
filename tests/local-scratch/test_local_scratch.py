"""Tests write on this machine, never under a home (tests/lib/local_scratch.py).

The checks run on a login node too, whose /home is the shared NFS disk. A test that kept
Grafana's database under ~/.cache took 35 s to start it there (6 s in memory) and past
two minutes beside a full check, while loading the disk everyone shares; another kept
Ansible's temporary files there. Every test and check script is scanned for a path it
builds from a home: Path.home(), expanduser, $HOME and the XDG_*_HOME directories.
"""

import ast
import os
import pwd
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import local_scratch  # noqa: E402
from repo_scan import Scan, shell_scripts, tracked  # noqa: E402

HOME_VARIABLES = {"HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME"}
SHELL_HOME = re.compile(r"\$\{?(HOME|XDG_(CACHE|DATA|STATE|CONFIG)_HOME)\b|(^|[\s\"'=:])~/")
# What the tests run on: the test trees, and the scripts that run them.
TESTS = ("tests", "scripts/validation/tests", "scripts/check.sh", "scripts/push.sh")
# path: why it names a home without writing under it.
ALLOWED = {
    # The prolog's expected value for a login setting written ~/...: a string compared,
    # nothing written there.
    "tests/user-environment/test_user_environment.py",
}


def _constant(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _dotted(node):
    """The dotted name of NODE (os.environ, Path.home), '' for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


def python_uses(text):
    """[(line, what)] of each place TEXT's code takes the home of the account running it:
    Path.home(), expanduser, and HOME or an XDG_*_HOME read from the process environment
    (a test's own environment dict, with a HOME it made, is its own business)."""
    out = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in ("Path.home", "pathlib.Path.home"):
                out.append((node.lineno, "Path.home()"))
            elif name.endswith("expanduser"):
                out.append((node.lineno, "expanduser"))
            elif name in ("os.environ.get", "os.getenv", "environ.get", "getenv") and node.args:
                if _constant(node.args[0]) in HOME_VARIABLES:
                    out.append((node.lineno, _constant(node.args[0])))
        elif isinstance(node, ast.Subscript) and _dotted(node.value) in ("os.environ", "environ"):
            if _constant(node.slice) in HOME_VARIABLES:
                out.append((node.lineno, _constant(node.slice)))
    return sorted(out)


def shell_uses(text):
    """[(line, what)] of each code line of TEXT (comments left out) naming a home."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        m = SHELL_HOME.search(code)
        if m:
            out.append((n, m.group(0).strip()))
    return out


class NoHomeInTests(unittest.TestCase):
    def test_no_test_builds_a_path_from_a_home(self):
        scan, found = Scan(ALLOWED, minimum=100), []
        files = [(p, python_uses(p.read_text())) for p in tracked(*TESTS) if p.suffix == ".py"]
        files += [
            (p, shell_uses(text))
            for p, text in shell_scripts()
            if p.relative_to(ROOT).as_posix().startswith(TESTS)
        ]
        for path, hits in files:
            rel = scan.seen(path).relative_to(ROOT).as_posix()
            if hits and not scan.excuse(rel):
                found += [f"{rel}:{line}: {what}" for line, what in hits]
        self.assertEqual(found, [], "keep test data in tests/lib/local_scratch.directory(), not a home")
        scan.check(self)

    def test_the_scan_finds_each_form(self):
        self.assertEqual(
            [w for _, w in python_uses(
                'from pathlib import Path\nimport os\nPath.home() / "x"\nos.path.expanduser("~/x")\n'
                'os.environ.get("XDG_CACHE_HOME")\nos.environ["HOME"]\nos.getenv("HOME")\n'
                '"$HOME in a string"\nos.environ.get("XDG_RUNTIME_DIR")\nblocks.home("alice")\n'
                'env = {"HOME": "/tmp/x"}\nenv["HOME"]\n'
            )],
            ["Path.home()", "expanduser", "XDG_CACHE_HOME", "HOME", "HOME"],
        )  # fmt: skip
        self.assertEqual(
            [
                w
                for _, w in shell_uses(
                    'd="$HOME/.cache"\n# ~/.cache in a comment\nx=${XDG_CACHE_HOME}\ncp a ~/b\ny=$XDG_RUNTIME_DIR\n'
                )
            ],
            ["$HOME", "${XDG_CACHE_HOME", "~/"],
        )


class Helper(unittest.TestCase):
    def test_a_directory_is_private_local_and_removed(self):
        path = local_scratch.directory("t")
        try:
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            home = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
            self.assertFalse(str(path).startswith(home + "/"))
        finally:
            local_scratch.remove(path)
        self.assertFalse(path.exists())

    def test_docker_ones_lie_under_the_runtime_directory(self):
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime:
            self.skipTest("no XDG_RUNTIME_DIR")
        self.assertEqual(local_scratch.base(docker=True), os.path.realpath(runtime))

    def test_a_shared_filesystem_is_refused(self):
        tmp = os.path.realpath(local_scratch.base())
        mounts = f"/dev/sda1 / ext4 rw 0 0\nserver:/x {tmp} nfs4 rw 0 0\n"
        self.assertEqual(local_scratch.filesystem(tmp + "/a", mounts), "nfs4")
        with self.assertRaises(ValueError):
            local_scratch.base(mounts=mounts)
        self.assertEqual(local_scratch.filesystem("/srv", mounts), "ext4")


if __name__ == "__main__":
    unittest.main()
