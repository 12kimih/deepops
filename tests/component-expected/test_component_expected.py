"""scripts/deepops/component_expected.py: each host's expected component versions, from
each role's own record expression resolved with the role defaults and a config
(placeholder configs here), and its source digests, written as the textfile the alerts
read."""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import component_expected  # noqa: E402
import component_playbooks  # noqa: E402
import local_scratch  # noqa: E402
from repo_scan import tracked  # noqa: E402
import settings  # noqa: E402


INVENTORY = """\
[slurm-master]
login1
[slurm-node]
node1
node2
[slurm-cluster:children]
slurm-master
slurm-node
"""


def config_dir(test, **group_vars):
    """A config directory: a placeholder inventory, and GROUP_VARS {group: {name: value}}."""
    path = local_scratch.directory("pins")
    test.addCleanup(local_scratch.remove, path)
    (path / "inventory").write_text(INVENTORY)
    (path / "group_vars").mkdir()
    for group, values in group_vars.items():
        (path / "group_vars" / f"{group}.yml").write_text(yaml.safe_dump(values))
    return path


class Expressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exprs = component_expected.expressions()

    def test_each_is_a_registered_components_own_record(self):
        self.assertGreater(len(self.exprs), 10)
        self.assertLessEqual(set(self.exprs), set(component_playbooks.registry()))
        for name, expr in self.exprs.items():
            with self.subTest(component=name):
                found = [
                    p
                    for p in tracked("roles/*/tasks/*.yml")
                    if f"component_record_name: {name}\n" in p.read_text() and expr in p.read_text()
                ]
                self.assertTrue(found, "the expression is read from the role's own task")

    def test_two_tasks_recording_one_component_differently_are_refused(self):
        class Two(settings.Tree):
            def files(self, prefix=""):
                return ["roles/a/tasks/main.yml", "roles/b/tasks/main.yml"]

            def text(self, rel):
                version = "{{ a_version }}" if "/a/" in rel else "{{ b_version }}"
                return (
                    "- ansible.builtin.include_role: {name: component_record}\n"
                    f'  vars: {{component_record_name: x, component_record_action: present, component_record_version: "{version}"}}\n'
                )

        with self.assertRaisesRegex(ValueError, "x: roles/a/tasks/main.yml and roles/b/tasks/main.yml"):
            component_expected.expressions(Two(ROOT))


class Expected(unittest.TestCase):
    def pins(self, **group_vars):
        return component_expected.state(config_dir(self, **group_vars)).versions

    def test_the_defaults_render_for_every_host(self):
        pins = self.pins()
        self.assertTrue(pins)
        for host, row in pins.items():
            for component, version in row.items():
                if not isinstance(version, component_expected.Unchecked):
                    self.assertNotIn("{{", version, (host, component))
        docker = settings.owners()["docker_engine_version"].value
        self.assertEqual({row["docker"] for row in pins.values()}, {docker})

    def test_the_config_and_its_groups_override(self):
        pins = self.pins(all={"nvidia_driver_branch": "999"}, **{"slurm-node": {"enroot_version": "9.9.9"}})
        self.assertEqual({row["nvidia_driver"] for row in pins.values()}, {"999"})
        nodes = {"node1", "node2"}
        release = settings.owners()["enroot_release"].value
        for host, row in pins.items():
            want = f"9.9.9-{release}" if host in nodes else None
            if want:
                self.assertEqual(row["enroot"], want, host)
            else:
                self.assertFalse(row["enroot"].startswith("9.9.9"), host)

    def test_what_only_the_host_knows_is_unchecked_with_the_reason(self):
        out = component_expected.render("{{ ansible_facts.distribution_version }}", {})
        self.assertIsInstance(out, component_expected.Unchecked)
        self.assertIn("ansible_facts", out)
        vaulted = component_expected.render("{{ v }}", {"v": settings.Vault("$ANSIBLE_VAULT;1.1;AES256\n00")})
        self.assertIsInstance(vaulted, component_expected.Unchecked)

    def test_a_setting_built_from_settings_renders_through_them(self):
        values = {"a": "1.{{ b }}", "b": "{{ c }}-x", "c": "2"}
        self.assertEqual(component_expected.render("{{ a | regex_replace('-x$', '') }}", values), "1.2")


class Textfile(unittest.TestCase):
    def test_series_per_host_and_component_escaped_and_unchecked_named(self):
        current = component_expected.State(
            {"n1": {"a": 'v "1": two', "b": component_expected.Unchecked("needs a fact")}, "n2": {"a": "v2"}},
            {"n1": {"a": "d1", "b": "d2"}, "n2": {"a": "d3"}},
        )
        text = component_expected.textfile(current, {"a": "playbooks/a.yml"})
        self.assertIn("# Not checked: b: needs a fact", text)
        lines = [line for line in text.splitlines() if not line.startswith("#")]
        self.assertEqual(
            lines,
            [
                'deepops_component_expected_version_info{for_host="n1",component="a",version="v \\"1\\": two"} 1',
                'deepops_component_expected_version_info{for_host="n2",component="a",version="v2"} 1',
                'deepops_component_expected_source_info{for_host="n1",component="a",source="d1",playbook="playbooks/a.yml"} 1',
                'deepops_component_expected_source_info{for_host="n1",component="b",source="d2"} 1',
                'deepops_component_expected_source_info{for_host="n2",component="a",source="d3",playbook="playbooks/a.yml"} 1',
            ],
        )
        self.assertEqual(
            text, component_expected.textfile(current, {"a": "playbooks/a.yml"}), "the same each run"
        )

    def test_the_real_checkout_is_one_group_per_metric_as_node_exporter_reads_it(self):
        current = component_expected.state(config_dir(self))
        text = component_expected.textfile(current, {})
        typed, order, hosts = set(), [], set()
        for line in text.splitlines():
            if line.startswith("# TYPE "):
                typed.add(line.split()[2])
            elif line and not line.startswith("#"):
                name = line.split("{", 1)[0]
                self.assertIn(name, typed, "TYPE before the samples")
                if not order or order[-1] != name:
                    order.append(name)
                hosts.add(line.split('for_host="', 1)[1].split('"', 1)[0])
        self.assertEqual(
            order, ["deepops_component_expected_version_info", "deepops_component_expected_source_info"]
        )
        self.assertEqual(hosts, {"login1", "node1", "node2"})

    def test_it_is_written_whole(self):
        out = local_scratch.directory("expected-write")
        self.addCleanup(local_scratch.remove, out)
        component_expected.write(out / "x.prom", "a 1\n")
        component_expected.write(out / "x.prom", "b 1\n")
        self.assertEqual([p.name for p in out.iterdir()], ["x.prom"], "no temporary file left")
        self.assertEqual((out / "x.prom").read_text(), "b 1\n")


class Changes(unittest.TestCase):
    def test_moved_versions_and_sources_name_their_hosts_and_playbooks(self):
        before = {"n1": {"enroot": "1"}, "n2": {"enroot": "1"}, "n3": {"lmod": "8"}}
        after = {"n1": {"enroot": "2"}, "n2": {"enroot": "2"}, "n3": {"lmod": "8"}}
        found = component_expected.changes(before, after)
        self.assertEqual(found, {"n1": [("enroot", "1", "2")], "n2": [("enroot", "1", "2")]})
        sources = component_expected.moved({"n3": {"lmod": "a"}}, {"n3": {"lmod": "b"}})
        self.assertEqual(sources, {"n3": ["lmod"]})
        lines = component_expected.describe(
            found, sources, {"enroot": "playbooks/e.yml", "lmod": "playbooks/l.yml"}
        )
        self.assertIn("  n1, n2:", lines)
        self.assertIn("    enroot: 1 -> 2", lines)
        self.assertIn("  n3: lmod", lines)
        self.assertIn("    uv run ansible-playbook -K playbooks/e.yml", lines)
        self.assertIn("    uv run ansible-playbook -K playbooks/l.yml", lines)

    def test_nothing_moved_says_so(self):
        same = {"n1": {"a": "1"}}
        self.assertEqual(
            component_expected.describe(
                component_expected.changes(same, same), component_expected.moved(same, same), {}
            ),
            ["components: no host's expected version or source changed"],
        )

    def test_a_version_that_became_unchecked_is_not_a_change(self):
        found = component_expected.changes(
            {"n1": {"a": "1"}}, {"n1": {"a": component_expected.Unchecked("fact")}}
        )
        self.assertEqual(found, {})


if __name__ == "__main__":
    unittest.main()
