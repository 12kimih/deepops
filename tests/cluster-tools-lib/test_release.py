"""roles/cluster_tools_lib/files/release.py: the package as one release. The archive is
the same bytes for the same modules (its hash names the release), an unpack is whole or
absent, and a switch replaces the live path in one step and keeps one earlier release.
Temporary directories only."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

release = in_tree.load("roles/cluster_tools_lib/files/release.py")


class Release(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.d = Path(self.dir.name)
        self.src = self.d / "src"
        self.src.mkdir()
        for name, text in (("b.py", "B = 2\n"), ("a.py", "A = 1\n"), ("__init__.py", "")):
            (self.src / name).write_text(text)

    def modules(self):
        return [str(p) for p in self.src.iterdir()]

    def test_the_same_modules_give_the_same_bytes_whatever_their_order_or_times(self):
        first, digest = release.archive(self.modules())
        os.utime(self.src / "a.py", (1, 1))
        again, same = release.archive(list(reversed(self.modules())))
        self.assertEqual((first, digest), (again, same))
        (self.src / "a.py").write_text("A = 3\n")
        self.assertNotEqual(release.archive(self.modules())[1], digest)

    def test_unpack_then_switch_from_a_real_directory_then_between_releases(self):
        lib = self.d / "lib"
        (lib / "cluster_tools").mkdir(parents=True)
        (lib / "cluster_tools" / "old.py").write_text("")
        releases = lib / "releases"
        releases.mkdir()

        def publish():
            data, digest = release.archive(self.modules())
            archive = releases / f"cluster_tools-{digest}.tar.gz"
            archive.write_bytes(data)
            release.unpack(str(archive), str(releases / digest))
            live = lib / "cluster_tools"
            keep = os.path.basename(os.path.dirname(os.readlink(live))) if live.is_symlink() else ""
            release.switch(str(live), str(releases / digest), keep)
            return digest

        first = publish()
        self.assertEqual(os.readlink(lib / "cluster_tools"), str(releases / first / "cluster_tools"))
        self.assertEqual(sorted(p.name for p in (lib / "cluster_tools").iterdir() if p.suffix == ".py"),
                         ["__init__.py", "a.py", "b.py"])  # fmt: skip
        self.assertFalse((lib / "cluster_tools.switching").exists(), "the earlier directory is gone")
        (self.src / "a.py").write_text("A = 4\n")
        second = publish()
        (self.src / "a.py").write_text("A = 5\n")
        third = publish()
        self.assertEqual({p.name for p in releases.iterdir() if p.is_dir()}, {second, third},
                         "the new release and the one before it")  # fmt: skip

    def test_an_archive_with_more_than_the_package_is_refused(self):
        import io
        import tarfile

        bad = self.d / "bad.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            info = tarfile.TarInfo("etc/passwd")
            tar.addfile(info, io.BytesIO(b""))
        with self.assertRaises(SystemExit):
            release.unpack(str(bad), str(self.d / "r"))
        self.assertFalse((self.d / "r").exists())


if __name__ == "__main__":
    unittest.main()
