"""The component-state rules (docs/deepops/component-state.md) for every component in
the registry: a state setting, both paths, the record in both, no switch-gated import."""

import importlib.util
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("components", ROOT / "plugins/filter/components.py")
components = importlib.util.module_from_spec(spec)
spec.loader.exec_module(components)
REGISTRY = yaml.safe_load((ROOT / "roles/deploy_defaults/vars/main.yml").read_text())[
    "deepops_component_registry"
]
DEPLOYS = ("playbooks/slurm-cluster.yml", "playbooks/k8s-cluster.yml")


def switches(name):
    return {re.sub(r"[{} ]", "", rule["switch"]) for rule in REGISTRY[name]["rules"]}


class Registry(unittest.TestCase):
    def test_it_is_valid_and_acyclic(self):
        self.assertEqual(components.check(REGISTRY), [])
        self.assertEqual(sorted(components.components_order(REGISTRY)), sorted(REGISTRY))

    def test_rules_name_deploy_switches(self):
        defaults = yaml.safe_load((ROOT / "roles/deploy_defaults/defaults/main.yml").read_text())
        for name in REGISTRY:
            for switch in switches(name):
                with self.subTest(component=name):
                    self.assertIn(switch, defaults, "a rule's switch is a deploy switch")


class Roles(unittest.TestCase):
    def test_each_component_role_has_state_both_paths_and_the_record(self):
        for name in REGISTRY:
            role = ROOT / "roles" / name
            with self.subTest(role=name):
                defaults = yaml.safe_load((role / "defaults/main.yml").read_text())
                self.assertEqual(defaults.get(f"{name}_state"), "present")
                main = (role / "tasks/main.yml").read_text()
                self.assertIn(f"{name}_state in ['present', 'absent']", main)
                for path in ("present", "absent"):
                    text = (role / f"tasks/{path}.yml").read_text()
                    self.assertIn("name: component_record", text, f"{path}.yml records")
                    self.assertIn(f"component_record_name: {name}", text)
                self.assertIn(f"{name}_items", yaml.safe_load((role / "vars/main.yml").read_text()))


class Playbooks(unittest.TestCase):
    def test_no_deploy_skips_a_component_on_its_switch(self):
        for deploy in DEPLOYS:
            for play in yaml.safe_load((ROOT / deploy).read_text()):
                when = play.get("when") or []
                text = " ".join(when if isinstance(when, list) else [str(when)])
                for name in REGISTRY:
                    for switch in switches(name):
                        with self.subTest(deploy=deploy, play=play.get("name"), switch=switch):
                            self.assertNotRegex(
                                text, rf"\b{switch}\b", "the switch decides the state, not whether it runs"
                            )

    def test_component_plays_take_their_state_from_the_map(self):
        for name in REGISTRY:
            plays = [p for p in (ROOT / "playbooks").rglob("*.yml") if f"role: {name}\n" in p.read_text()]
            self.assertTrue(plays, name)
            for path in plays:
                with self.subTest(playbook=str(path.relative_to(ROOT))):
                    self.assertIn(f'{name}_state: "{{{{ deepops_components.{name} }}}}"', path.read_text())


class Desired(unittest.TestCase):
    GROUPS = {
        "slurm-master": ["m1", "m2"],
        "slurm-node": ["n1"],
        "kube_control_plane": ["k1"],
        "k8s_cluster": ["k1", "n1"],
    }
    REG = {
        "srv": {"rules": [{"switch": True, "group": "slurm-master", "first": True}]},
        "cli": {
            "rules": [{"switch": "False", "group": "slurm-node"}, {"switch": "true", "group": "k8s_cluster"}]
        },
        "dep": {"rules": [], "needs": []},
        "top": {"rules": [{"switch": True, "group": "slurm-node"}], "needs": ["dep"]},
    }

    def test_switch_group_first_and_needs(self):
        want = lambda host: components.components_desired(self.REG, self.GROUPS, host)  # noqa: E731
        self.assertEqual(want("m1")["srv"], "present")
        self.assertEqual(want("m2")["srv"], "absent", "only the group's first host")
        self.assertEqual(want("n1")["cli"], "present", "either deploy's rule keeps it")
        self.assertEqual(
            (want("n1")["dep"], want("k1")["dep"]), ("present", "absent"), "needed by a present one"
        )

    def test_order_puts_needs_first(self):
        order = components.components_order(self.REG)
        self.assertLess(order.index("dep"), order.index("top"))


if __name__ == "__main__":
    unittest.main()
