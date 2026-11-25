"""push_range.push_revs: the commits a push sends, for every push hook alike."""

import io
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "deepops"))
import push_range  # noqa: E402

Z = "0" * 40
A, B = "a" * 40, "b" * 40


class PushRevs(unittest.TestCase):
    def revs(self, stdin="", **env):
        return push_range.push_revs(io.StringIO(stdin), env)

    def test_git_gives_every_ref_on_stdin(self):
        lines = f"refs/heads/main {A} refs/heads/main {B}\nrefs/heads/new {B} refs/heads/new {Z}\n"
        self.assertEqual(self.revs(lines), [[A, "--not", B], [B, "--not", "--remotes"]])
        self.assertEqual(self.revs(f"(delete) {Z} refs/heads/gone {B}\n"), [], "a deletion sends nothing")

    def test_pre_commit_gives_the_range_in_its_environment(self):
        self.assertEqual(self.revs(PRE_COMMIT_FROM_REF=B, PRE_COMMIT_TO_REF=A), [[A, "--not", B]])
        self.assertEqual(self.revs(PRE_COMMIT_REMOTE_NAME="origin"), [["HEAD", "--not", "--remotes"]])

    def test_no_refs_stops_instead_of_passing_unchecked(self):
        with self.assertRaises(SystemExit) as stop, unittest.mock.patch("sys.stderr", io.StringIO()) as err:
            self.revs()
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("no refs", err.getvalue())


if __name__ == "__main__":
    unittest.main()
