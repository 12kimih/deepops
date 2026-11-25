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

    def test_a_tree_lists_directories_then_files_under_any_path(self):
        import functools
        import os
        import subprocess
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            for rel in (
                "scripts/setup.sh",
                "scripts/deepops/x.py",
                "config.example/group_vars/all.yml",
                "scripts/new.sh",
            ):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("x\n")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=env)
            subprocess.run(
                ["git", "add", "scripts/setup.sh", "scripts/deepops", "config.example"],
                cwd=root,
                check=True,
                env=env,
            )
            listing = functools.partial(gen.tracked, root=root)
            with (
                mock.patch.object(gen, "ROOT", root),
                mock.patch.object(gen, "tracked", listing),
                mock.patch.dict(os.environ, env, clear=True),
            ):
                self.assertEqual(
                    gen.gen_tree("scripts"), "```text\n├── scripts\n│   ├── deepops\n│   └── setup.sh\n```\n"
                )
                self.assertIn("│   └── all.yml", gen.gen_tree("config.example/group_vars"))


class InventoryTests(unittest.TestCase):
    """The exporters and dashboards the monitoring page lists come from what ships: by hand
    it listed four dashboards while five shipped, and ports a site could change."""

    def test_every_exporter_and_dashboard_is_listed(self):
        exporters = gen.gen_exporters("")
        for exporter in gen.defaults("prometheus_paths")["prometheus_exporters"]:
            self.assertIn(f"`{exporter['module']}`", exporters)
        self.assertNotRegex(exporters, r"\| \d+ \|", "a port is shown with its setting")
        boards = gen.gen_dashboards("")
        shipped = [
            f for files in gen.defaults("prometheus_paths")["grafana_dashboard_sets"].values() for f in files
        ]
        self.assertEqual(boards.count("\n") - 2, len(shipped))
        self.assertIn("| `slurm_metrics` |", boards)

    def test_the_monitoring_page_uses_them(self):
        page = (ROOT / "docs/slurm-cluster/slurm-monitor.md").read_text()
        for name in ("exporters", "dashboards"):
            self.assertIn(f"<!-- BEGIN GENERATED: {name} -->", page)

    def test_every_port_setting_is_listed(self):
        ports = gen.gen_ports("")
        for role in gen.roles():
            for name, value in gen.defaults(role).items():
                if name.endswith("_port") and str(value).isdigit():
                    self.assertIn(f"| {value}/", ports.split(f"`{name}`")[0].rsplit("\n", 1)[-1])
        page = (ROOT / "docs/misc/network-ports.md").read_text()
        self.assertIn("<!-- BEGIN GENERATED: ports -->", page)


if __name__ == "__main__":
    unittest.main()
