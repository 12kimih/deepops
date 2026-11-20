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
        # In a throwaway repository, never the shared working tree: a probe left in
        # roles/ by a killed run would be seen by every other session's tools.
        import functools
        import os
        import subprocess
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            for role in ("kept", "stray"):
                (root / "roles" / role / "defaults").mkdir(parents=True)
                (root / "roles" / role / "defaults" / "main.yml").write_text(f"{role}_version: 1.0\n")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=env)
            subprocess.run(["git", "add", "roles/kept"], cwd=root, check=True, env=env)
            listing = functools.partial(gen.tracked, root=root)  # scripts/deepops/repo_files.py
            with (
                mock.patch.object(gen, "ROOT", root),
                mock.patch.object(gen, "tracked", listing),
                mock.patch.dict(os.environ, env, clear=True),
            ):
                self.assertEqual(gen.roles(), ["kept"])
                self.assertNotIn("stray", gen.gen_role_tree(""))


if __name__ == "__main__":
    unittest.main()
