"""Tests for clustertools.files."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools.files import write_atomic  # noqa: E402


class WriteTests(unittest.TestCase):
    def test_atomic_replace_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "out.prom"
            path.write_text("old\n")
            write_atomic(str(path), "new\n")
            self.assertEqual(path.read_text(), "new\n")
            self.assertEqual([p.name for p in Path(d).iterdir()], ["out.prom"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            write_atomic(str(path), "x", mode=0o600)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_an_error_names_the_file_asked_for(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "missing-dir" / "out.prom")
            with self.assertRaises(FileNotFoundError) as ctx:
                write_atomic(path, "x")
            self.assertEqual(ctx.exception.filename, path)


if __name__ == "__main__":
    unittest.main()
