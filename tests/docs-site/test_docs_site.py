"""scripts/docs/docs_site.py: the docs site against the code and the site config, read
from the docs repository's main alone, on a throwaway docs repository and site config.
2026-11-27: disk-usage shipped and stayed missing from the docs; the one check ran only
when an environment variable nobody set named the docs checkout."""

import hashlib
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import local_scratch  # noqa: E402

spec = importlib.util.spec_from_file_location("docs_site", ROOT / "scripts/docs/docs_site.py")
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

AGENTS = "# rules\n"
EXPORTER = f"import sys\nsys.stdout.write({AGENTS!r})\n"


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


class DocsSite(unittest.TestCase):
    def setUp(self):
        self.base = local_scratch.directory("docs-site")
        self.addCleanup(local_scratch.remove, self.base)
        self.config = self.base / "config"
        (self.config / "group_vars").mkdir(parents=True)
        (self.config / "group_vars" / "all.yml").write_text(
            "cluster_info: {docs_url: 'https://docs.example/guide/'}\n"
            "cluster_tools_commands: [sq, disk-usage]\n"
            f"ai_agent_rules_sha256: {hashlib.sha256(AGENTS.encode()).hexdigest()}\n"
        )
        self.docs = self.base / "docs-site"
        command = ds.site_facts.settings(str(self.config)).get("cluster_bot_command") or "/cluster"
        subs = ds.site_facts.facts(str(self.config))["bot"]["subcommands"]
        files = {
            "data/site.json": ds.site_facts.render(
                ds.site_facts.public(ds.site_facts.facts(str(self.config)))
            ),
            "zensical.toml": '[project]\nsite_url = "https://docs.example/guide/"\n',
            "scripts/agent_rules.py": EXPORTER,
            "slack/help.md": "[[commands]]\n",
            "docs/reference.md": "".join(
                f"<!-- BEGIN GENERATED: options:{n} -->x<!-- END GENERATED: options:{n} -->\n"
                for n in ("sq", "disk-usage")
            )
            + "<!-- BEGIN GENERATED: commands:table -->x<!-- END GENERATED: commands:table -->\n"
            + "".join(f"`{command} {s['name']}`\n" for s in subs),
        }
        for rel, text in files.items():
            (self.docs / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.docs / rel).write_text(text)
        git(self.base, "init", "-q", "-b", "main", str(self.docs))
        git(self.docs, "add", "-A")
        git(self.docs, "commit", "-q", "-m", "start")

    def found(self):
        return ds.problems(ds.Docs(self.docs, "main"), str(self.config))

    def commit(self, rel, text):
        (self.docs / rel).write_text(text)
        git(self.docs, "commit", "-q", "-am", rel)

    def test_a_current_site_passes(self):
        self.assertEqual(self.found(), [])

    def test_main_counts_never_the_working_tree(self):
        (self.docs / "data/site.json").write_text("{}\n")  # someone's work in progress
        self.assertEqual(self.found(), [])
        git(self.docs, "checkout", "-q", "-b", "other")
        self.commit("data/site.json", "{}\n")
        self.assertEqual(self.found(), [], "a branch checked out there is not main")

    def test_a_stale_data_file_fails(self):
        self.commit("data/site.json", "{}\n")
        self.assertEqual([p.split(":")[0] for p in self.found()], ["data"])

    def test_another_address_fails(self):
        self.commit("zensical.toml", '[project]\nsite_url = "https://elsewhere.example/"\n')
        self.assertEqual([p.split(":")[0] for p in self.found()], ["url"])

    def test_agent_rules_that_changed_since_the_pin_fail(self):
        self.commit("scripts/agent_rules.py", "import sys\nsys.stdout.write('# new rules\\n')\n")
        self.assertEqual([p.split(":")[0] for p in self.found()], ["agents"])

    def test_a_published_command_without_its_table_or_banner_line_fails(self):
        # the new command: in cluster_tools_commands, nowhere on the pages
        (self.config / "group_vars" / "slurm-cluster.yml").write_text(
            "cluster_tools_commands: [sq, disk-usage, gpu-usage]\ncluster_tools_motd_commands: [sq]\n"
        )
        found = self.found()
        self.assertIn(
            "commands: gpu-usage has no options table on a page at main (options:gpu-usage or options:all)",
            found,
        )
        self.assertIn("commands: disk-usage is not in the login banner (cluster_tools_motd_commands)", found)

    def test_blocks_that_list_every_command_cover_each(self):
        (self.config / "group_vars" / "slurm-cluster.yml").write_text(
            "cluster_tools_commands: [sq, disk-usage, gpu-usage]\n"
        )
        self.commit(
            "docs/reference.md",
            (self.docs / "docs/reference.md").read_text()
            + "<!-- BEGIN GENERATED: options:all -->x<!-- END GENERATED: options:all -->\n"
            + "<!-- BEGIN GENERATED: commands:table -->x<!-- END GENERATED: commands:table -->\n",
        )
        self.assertEqual([p for p in self.found() if "gpu-usage" in p], [])

    def test_a_bot_subcommand_no_page_names_fails(self):
        self.commit("docs/reference.md", "<!-- BEGIN GENERATED: options:sq -->x<!-- END GENERATED: options:sq -->\n"
                    "<!-- BEGIN GENERATED: options:disk-usage -->x<!-- END GENERATED: options:disk-usage -->\n")  # fmt: skip
        found = self.found()
        self.assertTrue(any(p.startswith("commands: no page at main names /cluster ") for p in found))
        self.assertIn("commands: no page at main names sq (or lists them all: commands:table)", found)

    def test_no_docs_checkout_is_nothing_to_compare(self):
        self.assertEqual(
            ds.main(["--docs", str(self.base / "none"), "check"]), 1, "named: missing is an error"
        )


if __name__ == "__main__":
    unittest.main()
