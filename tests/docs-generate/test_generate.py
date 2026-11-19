"""scripts/docs/generate.py: blocks it cannot read fail, and it reads what git tracks."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("generate", ROOT / "scripts" / "docs" / "generate.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

GOOD = "<!-- BEGIN GENERATED: role-tree -->\nx\n<!-- END GENERATED: role-tree -->\n"


class MarkerTests(unittest.TestCase):
    def test_a_block_it_cannot_read_is_malformed(self):
        self.assertFalse(gen.malformed(GOOD))
        for bad in (
            GOOD.replace("END GENERATED: role-tree", "END GENERATED: role-tre"),
            GOOD.replace("<!-- END GENERATED: role-tree -->\n", ""),
            GOOD.replace("\n", "\r\n"),
            GOOD.replace("role-tree -->\nx", "role-tree --> \nx"),
        ):
            with self.subTest(bad=bad):
                self.assertTrue(gen.malformed(bad))


class TrackedTests(unittest.TestCase):
    def test_only_tracked_files_are_read(self):
        stray = ROOT / "roles" / "zz-untracked-probe" / "defaults" / "main.yml"
        stray.parent.mkdir(parents=True)
        try:
            stray.write_text("zz_version: 1.0\n")
            self.assertNotIn("zz-untracked-probe", gen.roles())
            self.assertNotIn("zz-untracked-probe", gen.gen_role_tree(""))
        finally:
            stray.unlink()
            stray.parent.rmdir()
            stray.parent.parent.rmdir()


if __name__ == "__main__":
    unittest.main()
