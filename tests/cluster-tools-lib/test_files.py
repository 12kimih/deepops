"""Tests for cluster_tools.files."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools.files import read_textfile, write_atomic, write_textfile  # noqa: E402


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


class TextfileTests(unittest.TestCase):
    """write_textfile writes the live textfile and its kept copy; read_textfile falls back
    to the copy when the live one is gone (after a boot, before roles/textfile_keep's
    restore)."""

    def test_both_written_directories_made(self):
        with tempfile.TemporaryDirectory() as d:
            live, kept = Path(d, "prom", "x.prom"), Path(d, "keep", "x.prom")
            write_textfile(str(live), "a 1\n", str(kept))
            self.assertEqual((live.read_text(), kept.read_text()), ("a 1\n", "a 1\n"))

    def test_without_a_copy_only_the_live_file(self):
        with tempfile.TemporaryDirectory() as d:
            live = Path(d, "x.prom")
            write_textfile(str(live), "a 1\n")
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["x.prom"])

    def test_reading_falls_back_to_the_copy(self):
        with tempfile.TemporaryDirectory() as d:
            live, kept = Path(d, "x.prom"), Path(d, "k.prom")
            self.assertEqual(read_textfile(str(live), str(kept)), "")
            kept.write_text("old 1\n")
            self.assertEqual(read_textfile(str(live), str(kept)), "old 1\n")
            live.write_text("new 2\n")
            self.assertEqual(read_textfile(str(live), str(kept)), "new 2\n")


if __name__ == "__main__":
    unittest.main()
