"""scripts/deepops/component_source.py: the digest of what decides each component on each
host, on a throwaway repository: a role (app) with a template, a filter and a config
file it names; a shared role (helper) it runs one tasks file of; an inert role (check)
it depends on; a second component (other); and the component map."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import component_source  # noqa: E402
import local_scratch  # noqa: E402
import settings  # noqa: E402

FILES = {
    "roles/app/meta/main.yml": "dependencies:\n  - role: check\n",
    "roles/app/defaults/main.yml": (
        'app_version: "1"\napp_setting: plain\napp_key_file: "{{ inventory_dir }}/files/app/key"\n'
    ),
    "roles/app/templates/app.conf.j2": "{{ app_setting | myfilter }} {{ helper_used }}\n",
    "roles/app/tasks/main.yml": """\
- ansible.builtin.include_role:
    name: helper
    tasks_from: setup
- ansible.builtin.template:
    src: app.conf.j2
    dest: /etc/app.conf
  when: inventory_hostname in deepops_component_registry | component_hosts(groups, 'app')
- ansible.builtin.include_role:
    name: component_record
  vars:
    component_record_name: app
    component_record_action: present
    component_record_version: "{{ app_version }}"
""",
    "roles/check/tasks/main.yml": "- ansible.builtin.assert:\n    that: check_list | length > 0\n",
    "roles/check/vars/main.yml": "check_list: [a]\n",
    "roles/helper/defaults/main.yml": 'helper_used: "{{ helper_inner }}"\nhelper_inner: "1"\nhelper_unused: "2"\n',
    "roles/helper/vars/main.yml": "deepops_component_registry:\n  app: {group: all}\n  other: {group: all}\n",
    "roles/helper/tasks/setup.yml": "- ansible.builtin.copy:\n    src: setup.txt\n    dest: /tmp/x\n",
    "roles/helper/tasks/other.yml": "- ansible.builtin.copy:\n    src: other.txt\n    dest: /tmp/y\n",
    "roles/helper/files/setup.txt": "setup\n",
    "roles/helper/files/other.txt": "other\n",
    "roles/other/tasks/main.yml": """\
- ansible.builtin.include_role:
    name: component_record
  vars:
    component_record_name: other
    component_record_action: present
""",
    "roles/component_record/tasks/main.yml": "- ansible.builtin.debug:\n    msg: record\n",
    "plugins/filter/used.py": "class FilterModule:\n    def filters(self):\n        return {'myfilter': str}\n",
    "plugins/filter/unused.py": "class FilterModule:\n    def filters(self):\n        return {'notused': str}\n",
    "playbooks/app.yml": "- hosts: all\n  roles:\n    - app\n",
    "playbooks/other.yml": "- hosts: all\n  roles:\n    - other\n",
}
CONFIG = {
    "inventory": "[g]\nn1\n[rest]\nn2\n",
    "group_vars/g.yml": "app_setting: special\n",
    "files/app/key": "k1\n",
}


def git(cwd, *args):
    env = {**settings.CLEAN_ENV, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, env=env, check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


class Digest(unittest.TestCase):
    def setUp(self):
        self.base = local_scratch.directory("component-source")
        self.addCleanup(local_scratch.remove, self.base)
        self.repo, self.config = self.base / "repo", self.base / "config"
        for root, files in ((self.repo, FILES), (self.config, CONFIG)):
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
        git(self.base, "init", "-q", str(self.repo))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "start")

    def digests(self, rev=None):
        tree = settings.Tree(self.repo, rev=rev)
        config = settings.Config(settings.site_tree(self.config))
        return component_source.Sources(tree, config, settings.owners(tree, private=True)).all()

    def edit(self, rel, old, new, root=None):
        path = (root or self.repo) / rel
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def moved(self, before, after):
        return {(h, c) for h in after for c in after[h] if before[h][c] != after[h][c]}

    def test_the_working_tree_and_its_commit_agree(self):
        self.assertEqual(self.digests(), self.digests("HEAD"))
        self.assertEqual(set(self.digests()["n1"]), {"app", "other", "component_record"})

    def test_a_role_fix_moves_that_component_on_every_host_and_nothing_else(self):
        # 2026-11-26: the same version, newer code; only the digest tells the hosts apart
        before = self.digests()
        self.edit("roles/app/tasks/main.yml", "dest: /etc/app.conf", "dest: /etc/app.conf\n    mode: '0644'")
        self.assertEqual(self.moved(before, self.digests()), {("n1", "app"), ("n2", "app")})

    def test_what_the_component_runs_counts_and_what_it_does_not_never(self):
        cases = [
            (
                "roles/app/templates/app.conf.j2",
                "{{ helper_used }}",
                "{{ helper_used }}!",
                True,
            ),  # its own template
            (
                "roles/helper/defaults/main.yml",
                'helper_inner: "1"',
                'helper_inner: "9"',
                True,
            ),  # read, at depth
            ("roles/helper/files/setup.txt", "setup", "setup2", True),  # a file the tasks it runs copy
            ("plugins/filter/used.py", "str}", "repr}", True),  # a filter it uses
            ("roles/helper/defaults/main.yml", 'helper_unused: "2"', 'helper_unused: "3"', False),
            ("roles/helper/tasks/other.yml", "/tmp/y", "/tmp/z", False),  # a tasks file it never runs
            ("roles/helper/files/other.txt", "other", "other2", False),
            ("roles/check/vars/main.yml", "[a]", "[a, b]", False),  # an inert role: changes no host
            ("plugins/filter/unused.py", "str}", "repr}", False),
            (
                "roles/helper/vars/main.yml",
                "other: {group: all}",
                "other: {group: g}",
                False,
            ),  # another's entry
            ("roles/helper/vars/main.yml", "app: {group: all}", "app: {group: g}", True),  # its own entry
            ("roles/component_record/tasks/main.yml", "record", "record2", False),  # its own digest
        ]
        for rel, old, new, counts in cases:
            with self.subTest(rel=rel, change=new):
                before = self.digests()
                self.edit(rel, old, new)
                self.assertEqual(("n1", "app") in self.moved(before, self.digests()), counts)
                self.edit(rel, new, old)

    def test_component_record_has_its_own_digest(self):
        before = self.digests()
        self.edit("roles/component_record/tasks/main.yml", "record", "record2")
        self.assertEqual(
            self.moved(before, self.digests()), {("n1", "component_record"), ("n2", "component_record")}
        )

    def test_the_config_counts_per_host(self):
        before = self.digests()
        self.assertNotEqual(before["n1"]["app"], before["n2"]["app"], "n1's group sets app_setting")
        self.edit("group_vars/g.yml", "special", "special2", root=self.config)
        self.assertEqual(self.moved(before, self.digests()), {("n1", "app")})
        before = self.digests()
        self.edit("files/app/key", "k1", "k2", root=self.config)  # named through inventory_dir
        self.assertEqual(self.moved(before, self.digests()), {("n1", "app"), ("n2", "app")})

    def test_an_unrelated_commit_moves_nothing(self):
        before = self.digests("HEAD")
        (self.repo / "README.md").write_text("words\n")
        self.edit("roles/other/tasks/main.yml", "present\n", "present\n    component_record_version: '2'\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "unrelated")
        self.assertEqual({c for _, c in self.moved(before, self.digests("HEAD"))}, {"other"})


if __name__ == "__main__":
    unittest.main()
