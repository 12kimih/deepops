"""scripts/docs/generate.py: blocks it cannot read fail, it reads what git tracks, and
the site config's pages are filled from the site's settings and facts."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("generate", ROOT / "scripts" / "docs" / "generate.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

GOOD = "<!-- BEGIN GENERATED: role-tree -->\nx\n<!-- END GENERATED: role-tree -->\n"


class Contract(unittest.TestCase):
    def test_every_generator_takes_its_argument_and_the_page(self):
        # body() calls each as GENERATORS[kind](arg, page): one with another signature
        # fails only when a page first uses it.
        import inspect

        for kind, fn in gen.GENERATORS.items():
            params = list(inspect.signature(fn).parameters)
            self.assertEqual(len(params), 2, f"{kind}: {fn.__name__}(arg, page)")


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


def site(root):
    """A site config with placeholder values: two nodes, one of them overriding a setting."""
    config = Path(root) / "config"
    for rel, text in {
        "inventory": "[all]\nn1\nn2\n[head]\nn1\n[compute]\nn2\n",
        "group_vars/all.yml": (
            "cluster_user_stale_days: 30\nsshd_ports: [22]\nsubmit_holds_interval: 5min\n"
            'slurm_usage_report_io_schedule: "*:0/10"\nfail2ban_settings: {bantime: -1, findtime: 2d}\n'
        ),
        "host_vars/n1": "sshd_ports: [22, 2222]\n",
        "files/slurm/slurm.conf": (
            "HealthCheckInterval=300\nResumeTimeout=600\n"
            "NodeName=n1 CPUs=8 RealMemory=1000 Gres=gpu:t:2\nNodeName=n2 CPUs=8 RealMemory=1000 Gres=gpu:t:4\n"
            "PartitionName=p Nodes=n[1-2] MaxTime=1-00:00:00\n"
        ),
    }.items():
        (config / rel).parent.mkdir(parents=True, exist_ok=True)
        (config / rel).write_text(text)
    return config


def inline(name, body=""):
    return f"<!-- BEGIN GENERATED: {name} -->{body}<!-- END GENERATED: {name} -->"


class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.config = site(cls.tmp.name)
        cls.site = gen.Site(cls.config)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def value(self, name, lang="ko"):
        return gen.body(name, gen.Page(lang, self.site)).strip()

    def test_an_inline_block_fills_on_its_line(self):
        text = f"| 기간 | {inline('setting:cluster_user_stale_days', '1')}일 |\n"
        self.assertFalse(gen.malformed(text))
        self.assertEqual(
            gen.fill(text, gen.Page("ko", self.site)),
            f"| 기간 | {inline('setting:cluster_user_stale_days', '30')}일 |\n",
        )
        self.assertTrue(
            gen.malformed(text.replace("END GENERATED: setting:cluster", "END GENERATED: setting:c"))
        )

    def test_a_setting_resolves_through_defaults_and_templates(self):
        # people_leave_grace_days defaults to "{{ cluster_user_stale_days }}".
        self.assertEqual(self.value("setting:people_leave_grace_days"), "30")
        self.assertEqual(self.value("setting:fail2ban_settings.findtime:span"), "2일")
        self.assertEqual(self.value("setting:fail2ban_settings.bantime:span", "en"), "forever")

    def test_hosts_that_differ_need_a_host_or_group(self):
        with self.assertRaisesRegex(SystemExit, "hosts differ"):
            self.value("setting:sshd_ports")
        self.assertEqual(self.value("setting:sshd_ports@n1"), "`22`, `2222`")
        self.assertEqual(self.value("setting:sshd_ports.1@head"), "2222")
        self.assertEqual(self.value("setting:sshd_ports@compute:code"), "`22`")
        with self.assertRaisesRegex(SystemExit, "no such host"):
            self.value("setting:sshd_ports@nowhere")

    def test_timers_and_facts_in_words(self):
        self.assertEqual(self.value("setting:submit_holds_interval:every"), "5분마다")
        self.assertEqual(self.value("setting:slurm_usage_report_io_schedule:every", "en"), "every 10 minutes")
        self.assertEqual(self.value("fact:slurm.health_check_interval_seconds:every"), "5분마다")
        self.assertEqual(self.value("fact:slurm.resume_timeout_seconds:span", "en"), "10 minutes")
        self.assertEqual(self.value("group:head"), "`n1`")

    def test_partitions_with_their_nodes_and_gpus(self):
        table = gen.body("partitions", gen.Page("en", self.site))
        self.assertIn("| `p` | `n1` (2), `n2` (4) | `t` 6 | 1-00:00:00 |", table)
        self.assertIn("GPUs in all: `t` 6", table)

    def test_a_deepops_page_takes_no_site_generator(self):
        with self.assertRaisesRegex(SystemExit, "site config's pages"):
            gen.body("setting:sshd_ports", gen.Page("en"))

    def test_main_fills_the_site_pages_and_checks_them(self):
        page = self.config / "admin-docs" / "page.md"
        page.parent.mkdir(exist_ok=True)
        page.write_text(f"매 {inline('setting:submit_holds_interval:every', 'x')}\n")
        env = {"DEEPOPS_CONFIG_DIR": str(self.config), "DEEPOPS_SITE_REV": "", "PRE_COMMIT": ""}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(gen.main(["--site", "--check"]), 1)
            self.assertEqual(gen.main(["--site"]), 0)
            self.assertIn("5분마다", page.read_text())
            self.assertEqual(gen.main(["--site", "--check"]), 0)
        page.unlink()


class SiteRevTests(unittest.TestCase):
    def test_a_revision_is_checked_as_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            (config / "README.md").write_text(f"{inline('setting:cluster_user_stale_days', '30')}\n")
            page = config / "admin-docs" / "page.md"
            page.parent.mkdir()
            page.write_text(f"{inline('setting:cluster_user_stale_days', '30')}\n")
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
            subprocess.run(["git", "init", "-q"], cwd=config, check=True, env=env)
            subprocess.run(["git", "add", "-A"], cwd=config, check=True, env=env)
            subprocess.run([*git, "commit", "-qm", "x"], cwd=config, check=True, env=env)
            page.write_text(f"{inline('setting:cluster_user_stale_days', 'stale')}\n")  # not committed
            with mock.patch.dict(os.environ, {"DEEPOPS_CONFIG_DIR": str(config), "DEEPOPS_SITE_REV": "HEAD"}):
                self.assertEqual(gen.main(["--site", "--check"]), 0)
            subprocess.run(["git", "add", "-A"], cwd=config, check=True, env=env)
            subprocess.run([*git, "commit", "-qm", "stale"], cwd=config, check=True, env=env)
            with mock.patch.dict(os.environ, {"DEEPOPS_CONFIG_DIR": str(config), "DEEPOPS_SITE_REV": "HEAD"}):
                self.assertEqual(gen.main(["--site", "--check"]), 1, "a page under admin-docs/ is read")
            with mock.patch.dict(
                os.environ, {"DEEPOPS_CONFIG_DIR": str(config), "DEEPOPS_SITE_REV": "", "PRE_COMMIT": ""}
            ):
                self.assertEqual(gen.main(["--site", "--check"]), 1)


class FormatTests(unittest.TestCase):
    def test_spans_and_calendars_in_both_languages(self):
        for value, ko, en in (
            (1800, "30분", "30 minutes"),
            ("3d", "3일", "3 days"),
            ("1h 30min", "1시간 30분", "1 hour 30 minutes"),
            (90, "1분 30초", "1 minute 30 seconds"),
            (-1, "영구", "forever"),
        ):
            self.assertEqual((gen.span(value, "ko"), gen.span(value, "en")), (ko, en))
        for value, ko, en in (
            ("*:0/5", "5분마다", "every 5 minutes"),
            ("1min", "1분마다", "every minute"),
            ("hourly", "매시간", "every hour"),
            ("*-*-* 04:00:00", "매일 04:00", "daily at 04:00"),
        ):
            self.assertEqual((gen.every(value, "ko"), gen.every(value, "en")), (ko, en))
        with self.assertRaises(SystemExit):
            gen.span("soon", "en")
        self.assertEqual(gen.show(["a", "b"], "", "en", "x"), "`a`, `b`")
        with self.assertRaisesRegex(SystemExit, "no single value"):
            gen.show({"a": 1}, "", "en", "x")


if __name__ == "__main__":
    unittest.main()
