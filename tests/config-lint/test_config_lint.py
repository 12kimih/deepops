"""config_lint.py: each check, and --fix."""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402

spec = importlib.util.spec_from_file_location("config_lint", ROOT / "scripts" / "deepops" / "config_lint.py")
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)


def owned(**values):
    return {k: cl.settings.Owner([f"roles/r/defaults/main.yml#{k}"], v) for k, v in values.items()}


class Configs(unittest.TestCase):
    """A temporary config.example and site config, and fixed role defaults."""

    OWNERS = owned(x=1, y=[1, 2], z="a")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.example, self.site = base / "example", base / "site"
        for d in (self.example / "group_vars", self.site / "group_vars"):
            d.mkdir(parents=True)
        (self.site / "inventory").write_text("[g]\nh1\n")
        for patch in (
            mock.patch.multiple(cl, EXAMPLE=self.example, CONFIG=self.site),
            mock.patch.object(cl.settings, "owners", return_value=self.OWNERS),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def write(self, example=None, site=None):
        if example is not None:
            (self.example / "group_vars/all.yml").write_text(example)
        if site is not None:
            (self.site / "group_vars/all.yml").write_text(site)


class Commented(Configs):
    def test_defaults_and_examples_pass(self):
        self.write("#x: 1\n#y:\n#  - 1\n#  - 2\n#z: b  # example\n#free: 3  # example\n")
        self.assertEqual(cl.check_commented(), [])

    def test_a_stale_default_is_reported_and_fixed(self):
        self.write("# What x is.\n#x: 2\n#y: [1]\n")
        found = cl.check_commented()
        self.assertEqual(len(found), 2, found)
        self.assertIn("shows 2, its default", found[0])
        fix = cl.Fixes()
        cl.check_commented(fix)
        fix.apply()
        self.assertEqual(
            (self.example / "group_vars/all.yml").read_text(), "# What x is.\n#x: 1\n#y:\n#- 1\n#- 2\n"
        )
        self.assertEqual(cl.check_commented(), [])

    def test_an_unmarked_value_without_a_default_and_a_marked_default(self):
        found = (self.write("#free: 3\n#x: 1  # example\n"), cl.check_commented())[1]
        self.assertEqual(len(found), 2, found)
        self.assertIn("mark it # example", found[0])
        self.assertIn("drop the mark", found[1])


class Broadcast(Configs):
    def test_a_channel_wide_mention_is_reported_and_a_comment_is_not(self):
        self.write("x: '<!here> look'\n", "# not <!channel>\nx: \"<!channel>\"\nz: '<!subteam^S1>'\n")
        found = cl.check_broadcast()
        self.assertEqual(len(found), 2, found)
        self.assertIn("config.example/group_vars/all.yml:1: a value mentions a whole channel", found[0])
        self.assertEqual(found[1], "broadcast: config/group_vars/all.yml:2", "the site's only by where")

    def test_the_administrators_group_passes(self):
        self.write("x: '<!subteam^S1>'\n", "x: 1\n")
        self.assertEqual(cl.check_broadcast(), [])


class Retired(Configs):
    def test_a_config_setting_a_retired_name_is_reported(self):
        self.write("#x: 1\n#old_example: 1\n", "x: 2\nold_site: 3\n")
        with mock.patch.object(cl, "retired", return_value={"old_site": "x", "old_example": "x"}):
            found = cl.check_retired()
        self.assertEqual(found, ["retired: config/group_vars/all.yml:2: old_site is retired: set x instead"])


class MappingKeys(Configs):
    def test_a_key_the_mapping_lacks_is_reported(self):
        self.write(
            "#x: 1\n",
            "info: {name: c}\n"
            'links: ["{{ info.name }}", "{{ info.dashboard_url }}", "{{ inventory_dir ~ \'/people.yml\' }}"]\n',
        )
        with mock.patch.object(cl, "tracked", return_value=[]):  # no role defaults: the site's mapping
            keys = cl.mapping_keys([tree for _, tree, _ in cl.configs()])
        self.assertEqual(keys, {})  # only a mapping some role defaults counts
        with mock.patch.object(cl, "mapping_keys", return_value={"info": {"name"}}):
            found = cl.check_mapping_keys()
        # A private config is named by place only (found(): its values stay private).
        self.assertEqual(found, ["mapping-keys: config/group_vars/all.yml:2"])


class Inventory(unittest.TestCase):
    TEXT = (
        "[all]\nm ansible_host=10.0.0.1\nn1\n# n9 commented\n"
        "[kube_node]\n#n2\n[k8s_cluster:children]\nkube_node\n"
        "[slurm-node]\nn1\n[slurm-cluster:children]\nslurm-node\nslurm-master\n"
        "[slurm-master]\nm\n[all:vars]\nansible_user=x\n"
    )

    def test_hosts_through_child_groups_and_never_a_vars_line(self):
        self.assertEqual(cl.group_hosts(self.TEXT, "slurm-cluster"), {"m", "n1"})
        self.assertEqual(cl.group_hosts(self.TEXT, "k8s_cluster"), set())
        self.assertEqual(cl.group_hosts(self.TEXT, "all"), {"m", "n1"})
        self.assertEqual(cl.group_hosts(self.TEXT, "absent"), set())


class IdleVars(Configs):
    def test_vars_of_a_group_without_hosts_or_of_an_unknown_host_are_dead(self):
        (self.site / "inventory").write_text(Inventory.TEXT)
        for rel in (
            "group_vars/k8s_cluster.yml",
            "group_vars/slurm-node.yml",
            "host_vars/m",
            "host_vars/old",
        ):
            (self.site / rel).parent.mkdir(exist_ok=True)
            (self.site / rel).write_text("a_setting: 1\n")
        (self.site / "group_vars/kube_node").mkdir()
        (self.site / "group_vars/kube_node/x.yml").write_text("a_setting: 1\n")
        self.write(site="a_setting: 1\n")
        tree = [t for label, t, _ in cl.configs() if label == "config"][0]
        self.assertEqual(
            cl.idle_vars(tree),
            {
                "group_vars/k8s_cluster.yml": "group k8s_cluster has no hosts in the inventory",
                "group_vars/kube_node/x.yml": "group kube_node has no hosts in the inventory",
                "host_vars/old": "host old is not in the inventory",
            },
        )

    def test_another_config_is_no_reader(self):
        self.assertEqual(cl.config_of("config.example/group_vars/all.yml"), "config.example")
        self.assertEqual(cl.config_of(f"{cl.EXAMPLE}/inventory"), "config.example")
        self.assertEqual(cl.config_of("config/group_vars/all.yml"), "config")
        self.assertIsNone(cl.config_of("roles/r/defaults/main.yml"))


class Removals(Configs):
    def test_only_explicit_removal_components_may_be_named(self):
        self.write(
            "#deepops_component_removals: [ghost]\n", "deepops_component_removals: [drv, plain, ghost]\n"
        )
        registry = (
            "deepops_component_registry:\n  drv: {rules: [], removal: explicit}\n  plain: {rules: []}\n"
        )
        (Path(self.tmp.name) / "registry.yml").write_text(registry)
        with mock.patch.object(cl, "REGISTRY_FILE", Path(self.tmp.name) / "registry.yml"):
            found = cl.check_removals()
        self.assertEqual(found, [
            "removals: config/group_vars/all.yml:1: plain is not a component with removal: explicit",
            "removals: config/group_vars/all.yml:1: ghost is not a component with removal: explicit",
        ])  # fmt: skip


class Redundant(Configs):
    def test_a_restated_default_is_reported_and_fixed(self):
        self.write("x: 1\nz: b\n", "# site: kept on purpose\nx: 1\ny: [1, 2]\nz: b\n")
        found = cl.check_redundant()
        self.assertEqual(found[0], "redundant: config.example/group_vars/all.yml:1: x: equals what it would be "
                                   "without it; comment it out")  # fmt: skip
        self.assertEqual(found[1:], ["redundant: config/group_vars/all.yml:3: equals what it would be without "
                                     "it; delete it, or say why in # site:"])  # fmt: skip
        fix = cl.Fixes()
        cl.check_redundant(fix)
        fix.apply()
        self.assertEqual((self.example / "group_vars/all.yml").read_text(), "#x: 1\nz: b\n")
        self.assertEqual(
            (self.site / "group_vars/all.yml").read_text(), "# site: kept on purpose\nx: 1\nz: b\n"
        )
        self.assertEqual(cl.check_redundant(), [])

    def test_under_the_commit_hook_the_site_is_read_as_committed_and_never_fixed(self):
        # pre-commit stashes this repository's unstaged edits, never the site's: a
        # half-done site edit must not fail another commit, nor a committed shadow hide.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(self.site)]
        self.write("", "x: 2\n")
        subprocess.run(git[:-2] + ["init", "-q", str(self.site)], check=True, env=env)
        subprocess.run(git + ["add", "-A"], check=True, env=env)
        subprocess.run(git + ["commit", "-qm", "c"], check=True, env=env)
        self.write(site="x: 1\n")
        (self.site / "group_vars/all").mkdir()
        by_hand = {k: v for k, v in os.environ.items() if k != "PRE_COMMIT"}
        with mock.patch.dict(os.environ, by_hand, clear=True):
            self.assertTrue(cl.check_redundant())
            self.assertTrue(cl.check_shadow())
        with mock.patch.dict(os.environ, {"PRE_COMMIT": "1"}):
            self.assertEqual(cl.check_redundant(), [])
            self.assertEqual(cl.check_shadow(), [])
            fix = cl.Fixes()
            cl.check_redundant(fix)
            self.assertEqual(fix.edits, {})


class Copies(Configs):
    def test_a_copy_is_reported_and_deleted_but_the_inventory_and_its_variables_stay(self):
        for base in (self.example, self.site):
            (base / "helm").mkdir()
            (base / "helm/values.yml").write_text("a: 1\n")
            (base / "group_vars/all.yml").write_text("#x: 1\n")
            (base / "inventory").write_text("[g]\nh1\n")
        (self.example / "helm/other.yml").write_text("b: 1\n")
        (self.site / "helm/other.yml").write_text("b: 2\n")
        (self.site / "env.sh").write_text("export A=1\n")
        self.assertEqual(cl.check_copies(), ["copies: config/helm/values.yml:1"])
        fix = cl.Fixes()
        cl.check_copies(fix)
        fix.apply()
        self.assertFalse((self.site / "helm/values.yml").exists())
        self.assertTrue((self.site / "group_vars/all.yml").exists())
        self.assertTrue((self.site / "inventory").exists())
        self.assertEqual(cl.check_copies(), [])

    def test_setup_starts_a_site_config_with_what_the_check_leaves(self):
        setup = (ROOT / "scripts/setup.sh").read_text()
        parts = re.search(r"for part in ([\w ]+?) ; do", setup).group(1).split()
        self.assertEqual(tuple(parts), cl.INVENTORY_DIR)

    def test_a_tool_reads_the_site_file_else_config_example(self):
        with tempfile.TemporaryDirectory() as site:
            (Path(site) / "helm").mkdir()
            (Path(site) / "helm/mine.yml").write_text("a: 1\n")
            script = "source scripts/common.sh >/dev/null; deepops_config_file helm/mine.yml; deepops_config_file helm/metallb.yml"
            out = subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                env={**os.environ, "DEEPOPS_CONFIG_DIR": site, "DEEPOPS_VERSION": "test"},
                capture_output=True,
                text=True,
                check=True,
                timeout=bounds.CHILD,
            ).stdout.splitlines()
        self.assertEqual(out, [f"{site}/helm/mine.yml", f"{ROOT}/config.example/helm/metallb.yml"])


class Repository(unittest.TestCase):
    def test_this_repository_is_clean(self):
        with mock.patch.dict(os.environ, {}):
            self.assertEqual(
                cl.check_commented() + cl.check_redundant() + cl.check_dead() + cl.check_doc_paths()
                + cl.check_doc_vars(), []
            )  # fmt: skip


class DocVars(unittest.TestCase):
    """A page naming a renamed variable sends the reader to a setting nothing reads
    (AGENTS.md once said nvidia_driver_ubuntu_use_open_kernel_modules: false)."""

    def problems(self, text):
        with tempfile.TemporaryDirectory() as d:
            doc = Path(d) / "page.md"
            doc.write_text(text)
            with (
                mock.patch.object(cl, "ROOT", Path(d)),
                mock.patch.object(cl, "CONFIG", Path(d) / "no-site"),
                mock.patch.object(cl, "tracked", return_value=[doc]),
                mock.patch.object(cl, "read_words", return_value={"slurm_install_nhc"}),
            ):
                return cl.check_doc_vars()

    def test_a_setting_and_a_table_name_are_checked(self):
        found = self.problems(
            "set `old_flag_name: false` here\n| `gone_setting` | yes |\n| `slurm_install_nhc` | `x_y: 1` |\n"
        )
        self.assertEqual(
            found,
            [
                "doc-vars: page.md:1: nothing reads old_flag_name",
                "doc-vars: page.md:2: nothing reads gone_setting",
            ],
        )

    def test_read_settings_and_prose_words_pass(self):
        self.assertEqual(self.problems("set `slurm_install_nhc: true`; the `no_wdelay` option\n"), [])


class DocAnchors(unittest.TestCase):
    """A section link must name a heading the page has: a renamed heading left a table
    of contents (and the site guides' links to the usage policy) pointing nowhere."""

    def test_github_slugs(self):
        text = (
            "# Firewall\n## Host firewall (roles/egress_policy)\n## `sq` and **qos**\n"
            "## 7. AI 사용 규정 (필독)\n## Notes\n## Notes\n```\n## not a heading\n```\n"
            '<a id="custom"></a>\n'
        )
        self.assertEqual(
            cl.slugs(text),
            {"firewall", "host-firewall-rolesegress_policy", "sq-and-qos", "7-ai-사용-규정-필독", "notes",
             "notes-1", "custom"},
        )  # fmt: skip

    def test_links_are_checked_on_the_page_and_across_pages(self):
        pages = [
            ("docs/a.md", "# A\n[ok](#a) [bad](#gone) [there](b.md#b-part) [no](b.md#nope)\n", None),
            ("docs/b.md", "# B part\n```\n[in code](#ignored)\n```\n[web](https://x.org/p#q)\n", None),
        ]
        with mock.patch.object(cl, "documents", return_value=pages):
            self.assertEqual(
                cl.check_doc_anchors(),
                [
                    "doc-anchors: docs/a.md:2: docs/a.md has no section #gone",
                    "doc-anchors: docs/a.md:2: docs/b.md has no section #nope",
                ],
            )


class DocRepeats(unittest.TestCase):
    """A section pasted in twice, the copies already disagreeing."""

    def test_a_run_of_text_seen_before_is_found(self):
        block = "".join(f"line {i}\n" for i in range(cl.DOC_REPEAT))
        self.assertEqual(cl.repeats("# T\n" + block + "middle\n" + block), (cl.DOC_REPEAT + 3, 2))

    def test_code_blocks_and_short_repeats_pass(self):
        code = "```\n" + "".join(f"out {i}\n" for i in range(cl.DOC_REPEAT)) + "```\n"
        short = "".join(f"line {i}\n" for i in range(cl.DOC_REPEAT - 1))
        self.assertIsNone(cl.repeats(code + "text\n" + code + short + "x\n" + short))


class CodeComments(unittest.TestCase):
    """2026-11-27: a comment named a script that never existed; only the
    *.md pages were checked."""

    def test_comments_and_docstrings_are_read_and_code_is_not(self):
        text = '"""Mod: roles/a/x.py."""\nX = "roles/not/a/comment"  # see plugins/b.py\ndef f():\n    """roles/c"""\n'
        self.assertEqual(
            {n: t.strip() for n, t in cl.comment_lines("m.py", text).items()},
            {1: "Mod: roles/a/x.py.", 2: "# see plugins/b.py", 4: "roles/c"},
        )
        self.assertEqual(
            cl.comment_lines("t.yml", "k: roles/v  # roles/w\n# roles/z\n"), {1: " roles/w", 2: " roles/z"}
        )

    def test_placeholders_stand_for_any_path(self):
        for path in ("roles/ROLE/defaults/main.yml", "tests/x/test_y.py", "roles/ROLE-x"):
            self.assertTrue(cl.placeholder(path), path)
        self.assertFalse(cl.placeholder("roles/slurm/tasks/main.yml"))

    def test_the_repository_names_only_what_it_has(self):
        self.assertEqual([p for p in cl.check_doc_paths() if "config/" not in p], [])


class SiteDocs(Configs):
    """The site's own guides hand out deepops' playbooks: a page there kept sending
    admins to playbooks deepops had removed, since only deepops' pages were checked."""

    def page(self, text):
        (self.site / "docs").mkdir()
        (self.site / "docs" / "own.md").write_text("x\n")
        (self.site / "admin-docs").mkdir()
        (self.site / "admin-docs" / "page.md").write_text(text)

    def test_a_gone_path_is_reported_by_position_only(self):
        self.page(
            "run playbooks/utilities/no-such-playbook.yml\nsee docs/own.md and playbooks/utilities/power.yml\n"
        )
        self.assertEqual(cl.check_doc_paths(), ["doc-paths: config/admin-docs/page.md:1"])

    def test_a_korean_particle_against_a_path_is_not_part_of_it(self):
        self.page("scripts/check.sh가 둘 다 합니다\nplaybooks/utilities/power.yml처럼 씁니다\n")
        self.assertEqual(cl.check_doc_paths(), [])

    def test_a_limit_to_a_site_host_passes_and_an_unknown_name_does_not(self):
        (self.example / "inventory").write_text((ROOT / "config.example" / "inventory").read_text())
        self.page(
            "`ansible-playbook -l h1 playbooks/utilities/stale-files.yml`\n"
            "`ansible-playbook -l nosuchhost playbooks/utilities/stale-files.yml`\n"
        )
        self.assertEqual(cl.check_doc_limits(), ["doc-limits: config/admin-docs/page.md:2"])

    def test_a_gpu_tool_over_ssh_needs_a_transient_unit(self):
        # Six such commands in one site guide printed "No devices were found".
        self.page(
            'ansible gpu1 -b -m shell -a "nvidia-smi -L"\n'
            'ansible gpu1 -b -a "systemd-run --wait --pipe --collect --quiet -- nvidia-smi -L"\n'
            'ansible gpu1 -b -m shell -a "dcgmi diag -r 1"\n'
            "ssh gpu1 nvidia-smi\n"
            "test with `srun --gpus=1 nvidia-smi`, never with bare SSH `nvidia-smi`\n"
        )
        self.assertEqual(
            cl.check_doc_gpu(),
            ["doc-gpu: config/admin-docs/page.md:1", "doc-gpu: config/admin-docs/page.md:4"],
        )


class Shadow(unittest.TestCase):
    """Ansible 2.22 checked: all/ beside all.yml, all.yml beside all.yaml, and
    host_vars/h/ beside h.yml each load only one of the two, silently."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.example, self.site = base / "example", base / "site"
        for d in (self.example / "group_vars", self.site / "group_vars", self.site / "host_vars"):
            d.mkdir(parents=True)
        patch = mock.patch.multiple(cl, EXAMPLE=self.example, CONFIG=self.site)
        patch.start()
        self.addCleanup(patch.stop)

    def test_one_place_each_is_clean(self):
        (self.site / "group_vars/all.yml").write_text("a: 1\n")
        (self.site / "group_vars/slurm").mkdir()
        (self.site / "host_vars/gpu1.yml").write_text("b: 1\n")
        self.assertEqual(cl.check_shadow(), [])

    def test_two_places_are_reported(self):
        (self.site / "group_vars/all.yml").write_text("a: 1\n")
        (self.site / "group_vars/all").mkdir()
        (self.example / "group_vars/k8s.yml").write_text("")
        (self.example / "group_vars/k8s.yaml").write_text("")
        (self.site / "host_vars/gpu1").mkdir()
        (self.site / "host_vars/gpu1.yml").write_text("")
        found = cl.check_shadow()
        self.assertEqual(len(found), 3, found)
        self.assertTrue(any("config/group_vars: all/, all.yml" in f for f in found), found)
        self.assertTrue(any("config.example/group_vars: k8s.yaml, k8s.yml" in f for f in found), found)

    def test_this_repository_is_clean(self):
        with mock.patch.multiple(cl, EXAMPLE=ROOT / "config.example", CONFIG=ROOT / "config"):
            self.assertEqual(cl.check_shadow(), [])


if __name__ == "__main__":
    unittest.main()
