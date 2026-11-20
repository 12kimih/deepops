"""The component-state rules (docs/deepops/component-state.md) for every component in
the registry: a state setting, both paths, the record in both, no switch-gated import."""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

spec = importlib.util.spec_from_file_location("components", ROOT / "plugins/filter/components.py")
components = importlib.util.module_from_spec(spec)
spec.loader.exec_module(components)
REGISTRY = yaml.safe_load((ROOT / "roles/deploy_defaults/vars/main.yml").read_text())[
    "deepops_component_registry"
]
DEPLOYS = ("playbooks/slurm-cluster.yml", "playbooks/k8s-cluster.yml")
# Removing these reboots nodes, locks users out or hangs every NFS client: a switch or a
# group never removes them, only deepops_component_removals does.
EXPLICIT = {
    "nvidia_driver",
    "slurm",
    "nfs",
    "nfs_client",
    "nis_server",
    "nis_client",
    "kerberos_client",
    "people",
    "ldap_server",
    "sssd_client",
}


JINJA = {"not", "and", "or", "bool", "true", "false", "length", "in"}


def words(switch):
    """The variable names a switch expression reads (string literals are not names)."""
    return set(re.findall(r"[a-z_][a-z0-9_]*", re.sub(r"'[^']*'|\"[^\"]*\"", "", str(switch)))) - JINJA


def switches(name):
    """The deploy switches a component's rules read."""
    return {w for rule in REGISTRY[name].get("rules") or [] for w in words(rule["switch"])}


class Registry(unittest.TestCase):
    def test_it_is_valid_and_acyclic(self):
        self.assertEqual(components.check(REGISTRY, ROOT / "roles"), [])
        self.assertEqual(sorted(components.components_order(REGISTRY)), sorted(REGISTRY))

    def test_rules_name_deploy_switches(self):
        # A rule's switch is a deploy switch, or an expression of deploy switches.
        defaults = yaml.safe_load((ROOT / "roles/deploy_defaults/defaults/main.yml").read_text())
        for name in REGISTRY:
            for rule in REGISTRY[name].get("rules") or []:
                names = words(rule["switch"])
                with self.subTest(component=name, switch=rule["switch"]):
                    self.assertTrue(names and names <= set(defaults), "a rule's switch names deploy switches")


class Explicit(unittest.TestCase):
    def test_access_and_reboot_class_components_are_removed_only_explicitly(self):
        for name in sorted(EXPLICIT & set(REGISTRY)):
            with self.subTest(component=name):
                self.assertEqual(REGISTRY[name].get("removal"), "explicit")


# Every switch a deploy still skips an import on, and why it is not (yet) a component.
# A name here that no gate uses any more fails the test: the list only shrinks.
GATES = {
    # Policies: an enum or a runtime setting, not something installed.
    "deepops_disable_cloud_init": "policy (off re-enables cloud-init)",
    "allow_user_set_gpu_clocks": "policy (a runtime setting, gone at reboot)",
    # One-shot: nothing to remove.
    "slurm_configure_etc_hosts": "one-shot (hostname and /etc/hosts)",
}


class Gates(unittest.TestCase):
    def test_every_switch_gate_is_classified_and_the_list_only_shrinks(self):
        defaults = yaml.safe_load((ROOT / "roles/deploy_defaults/defaults/main.yml").read_text())
        used = set()
        for deploy in DEPLOYS:
            for play in yaml.safe_load((ROOT / deploy).read_text()):
                when = play.get("when") if "import_playbook" in play else None
                if not when:
                    continue
                text = " ".join(when if isinstance(when, list) else [str(when)])
                names = {n for n in re.findall(r"[a-z_][a-z0-9_]*", text) if n in defaults}
                used |= names
                for name in sorted(names):
                    with self.subTest(deploy=deploy, play=play.get("name"), switch=name):
                        self.assertIn(
                            name, GATES, "a component's state comes from the map: classify it or move it"
                        )
        self.assertEqual(sorted(set(GATES) - used), [], "no longer a gate: drop it from GATES")


class Roles(unittest.TestCase):
    def test_each_component_role_has_state_both_paths_and_the_record(self):
        for name in REGISTRY:
            role = ROOT / "roles" / components.component_role(REGISTRY, name)
            with self.subTest(component=name):
                defaults = yaml.safe_load((role / "defaults/main.yml").read_text())
                self.assertEqual(defaults.get(f"{name}_state"), "present")
                main = (role / "tasks/main.yml").read_text()
                self.assertIn(f"{name}_state in ['present', 'absent', 'kept']", main)
                self.assertIn(f"{name}_state != 'kept'", main, "kept does nothing")
                for path in ("present", "absent"):
                    text = (role / f"tasks/{path}.yml").read_text()
                    self.assertIn("name: component_record", text, f"{path}.yml records")
                    self.assertIn(f"component_record_name: {name}", text)
                self.assertIn(f"{name}_items", yaml.safe_load((role / "vars/main.yml").read_text()))

    def test_absent_knows_every_item_present_records(self):
        # absent adopts by the same <name>_items as present. An item list reading a fact
        # only present sets is shorter in absent: the Slurm exporter's sources stayed on
        # every host deployed before component records.
        def facts(role, path):
            text = (role / f"tasks/{path}.yml").read_text()
            for included in re.findall(r"include_tasks: (\S+\.yml)", text):
                if (role / "tasks" / included).is_file():
                    text += (role / "tasks" / included).read_text()
            found = set()
            for block in re.findall(r"set_fact:\n((?:\s{4,}.*\n)+)", text):
                found |= set(re.findall(r"^\s{4}([a-z_][a-z0-9_]*):", block, re.M))
            return found

        for name in REGISTRY:
            role = ROOT / "roles" / components.component_role(REGISTRY, name)
            variables = yaml.safe_load((role / "vars/main.yml").read_text()) or {}
            text = str(variables.get(f"{name}_items", ""))
            text += "".join(str(v) for k, v in variables.items() if k in text)
            read = set(re.findall(r"\b([a-z_][a-z0-9_]*)\b", text))
            with self.subTest(component=name):
                self.assertEqual(sorted((read & facts(role, "present")) - facts(role, "absent")), [])


class Playbooks(unittest.TestCase):
    def test_no_deploy_skips_a_component_on_its_switch(self):
        for deploy in DEPLOYS:
            for play in yaml.safe_load((ROOT / deploy).read_text()):
                when = play.get("when") or []
                text = " ".join(when if isinstance(when, list) else [str(when)])
                for name in REGISTRY:
                    # A switch still gating a component yet to move is classified in GATES.
                    for switch in switches(name) - set(GATES):
                        with self.subTest(deploy=deploy, play=play.get("name"), switch=switch):
                            self.assertNotRegex(
                                text, rf"\b{switch}\b", "the switch decides the state, not whether it runs"
                            )

    def test_component_plays_take_their_state_from_the_map(self):
        # Every play that applies a component role, as a role entry (a mapping or a bare
        # name) or an include_role task, passes its state from the map, or kept where it
        # applies the role for something else (the NFS client part).
        uses = {}
        for path in tracked("playbooks/*.yml", "playbooks/*.yaml"):
            for play in yaml.safe_load(path.read_text()) or []:
                for entry in (play or {}).get("roles") or []:
                    entry = entry if isinstance(entry, dict) else {"role": entry}
                    uses.setdefault(entry.get("role"), []).append((path, entry))
                for task in (play or {}).get("tasks") or []:
                    include = (task or {}).get("ansible.builtin.include_role") or (task or {}).get(
                        "include_role"
                    )
                    # tasks_from runs a part of the role, not the component.
                    if isinstance(include, dict) and "tasks_from" not in include:
                        uses.setdefault(include.get("name"), []).append((path, task.get("vars") or {}))
        for name in REGISTRY:
            role = components.component_role(REGISTRY, name)
            self.assertIn(role, uses, name)
            want = f"{{{{ deepops_components.{name} | component_phase(deepops_phase) }}}}"
            for path, entry in uses[role]:
                with self.subTest(playbook=str(path.relative_to(ROOT)), component=name):
                    self.assertIn(" ".join(str(entry.get(f"{name}_state")).split()), (want, "kept"))

    def test_deploys_install_needs_first_and_remove_in_reverse(self):
        for deploy in DEPLOYS:
            imports = []

            def walk(path, phase):
                # An imported playbook's own imports run with the phase it was given; a
                # component a file's plays name more than once counts once.
                seen = set()
                for play in yaml.safe_load(path.read_text()) or []:
                    target = (play or {}).get("import_playbook")
                    # An import only a playbook run alone makes (the Docker a component
                    # brings with it) is not part of a deploy.
                    if target and "deepops_phase == 'converge'" not in str(play.get("when")):
                        walk(path.parent / target, (play.get("vars") or {}).get("deepops_phase") or phase)
                    if target:
                        continue
                    for name in re.findall(r"deepops_components\.(\w+)", yaml.safe_dump(play)):
                        if name not in seen:
                            seen.add(name)
                            imports.append((name, phase))

            walk(ROOT / deploy, None)
            # require installs a component for one playbook whatever the map says (the
            # Slurm build host's Docker): not the deploy's install or removal.
            imports = [(name, phase) for name, phase in imports if phase != "require"]
            with self.subTest(deploy=deploy):
                found = {name for name, _ in imports}
                self.assertTrue(found <= set(REGISTRY))
                install = [n for n, phase in imports if phase == "install"]
                remove = [n for n, phase in imports if phase == "remove"]
                retired = {n for n in found if "retired" in REGISTRY[n]}
                self.assertEqual(sorted(install), sorted(found - retired), "each component installs once")
                self.assertEqual(sorted(remove), sorted(found), "and is removed once")
                self.assertTrue(
                    all(phase in ("install", "remove") for _, phase in imports), "a deploy sets the phase"
                )
                last_install = max(i for i, (_, phase) in enumerate(imports) if phase == "install")
                first_remove = min(i for i, (_, phase) in enumerate(imports) if phase == "remove")
                self.assertLess(last_install, first_remove, "removal comes after every install")
                for name in found:
                    for need in REGISTRY[name].get("needs") or []:
                        if need in found:
                            self.assertLess(
                                install.index(need), install.index(name), f"{need} installs before {name}"
                            )
                            self.assertLess(
                                remove.index(name), remove.index(need), f"{name} goes before {need}"
                            )


MARKS_SPEC = importlib.util.spec_from_file_location(
    "deepops_marks", ROOT / "plugins/module_utils/deepops_marks.py"
)
marks = importlib.util.module_from_spec(MARKS_SPEC)
MARKS_SPEC.loader.exec_module(marks)
# Files a component writes that cannot carry a mark, and why leaving them on a host
# deployed before component records is right (absent adopts only what shows deepops
# made it; plugins/module_utils/deepops_marks.py).
UNMARKABLE = {
    "/etc/defaultdomain": "the NIS domain name alone; NIS is removed only explicitly",
}


def _tasks(path):
    stack = list(yaml.safe_load(path.read_text()) or [])
    while stack:
        task = stack.pop()
        if isinstance(task, dict):
            yield task
            for key in ("block", "rescue", "always"):
                stack.extend(task.get(key) or [])


class Marks(unittest.TestCase):
    def test_every_file_a_component_writes_shows_deepops_made_it(self):
        # Without a mark, absent on a host deployed before component records leaves the
        # file (a site's own of the same name must never go), so each file a role writes
        # carries one: ansible_managed or roles/<name> in its text, or deepops in its path.
        def norm(text):
            return " ".join(str(text).replace('"', "").replace("'", "").split())

        used = set()
        for name in REGISTRY:
            role = ROOT / "roles" / components.component_role(REGISTRY, name)
            items = str((yaml.safe_load((role / "vars/main.yml").read_text()) or {}).get(f"{name}_items", ""))
            # file:PATH literals, and 'file:' ~ setting in an expression.
            files = {norm(f) for f in re.findall(r"file:([^'\",\]\n]+)", items)} | {
                norm(f"{{{{ {v} }}}}")
                for v in re.findall(r"'file:'\s*~\s*([a-z_][a-z0-9_]*)\b(?!\s*~)", items)
            }
            for path in tracked(
                f"{role.relative_to(ROOT)}/tasks/*.yml", f"{role.relative_to(ROOT)}/tasks/*.yaml"
            ):
                for task in _tasks(path):
                    for module in ("ansible.builtin.template", "ansible.builtin.copy"):
                        args = task.get(module)
                        if not isinstance(args, dict) or norm(args.get("dest", "")) not in files:
                            continue
                        dest = norm(args["dest"])
                        if marks.named_deepops(dest) or args.get("remote_src"):
                            continue
                        if dest in UNMARKABLE:
                            used.add(dest)
                            continue
                        if "content" in args:
                            text = str(args["content"])
                            if not re.sub(r"\{\{.*?\}\}", "", text).strip():
                                continue  # all of it rendered at run time
                        else:
                            folder = "templates" if module.endswith("template") else "files"
                            defaults = yaml.safe_load((role / "defaults/main.yml").read_text()) or {}
                            # A src named by a setting reads that setting's default.
                            src = re.sub(
                                r"\{\{\s*(\w+)\s*\}\}",
                                lambda m: str(defaults.get(m[1], m[0])),
                                str(args["src"]),
                            )
                            src = src.removeprefix(f"{folder}/")
                            text = (role / folder / src).read_text(errors="replace")
                        with self.subTest(component=name, file=dest):
                            self.assertTrue(
                                marks.MARK.search(text) or "ansible_managed" in text,
                                "write ansible_managed (or roles/<name>) into it",
                            )
        self.assertEqual(sorted(set(UNMARKABLE) - used), [], "no longer written: drop it from UNMARKABLE")


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

    def test_explicit_removal_keeps_until_named(self):
        reg = {"drv": {"rules": [{"switch": True, "group": "slurm-node"}], "removal": "explicit"}}
        want = components.components_desired
        self.assertEqual(want(reg, self.GROUPS, "n1")["drv"], "present")
        self.assertEqual(want(reg, self.GROUPS, "k1")["drv"], "kept", "a rule that stops holding keeps it")
        self.assertEqual(want(reg, self.GROUPS, "k1", ["drv"])["drv"], "absent", "named: removed")
        self.assertEqual(want(reg, self.GROUPS, "n1", ["drv"])["drv"], "present", "named: kept where wanted")
        for wrong in (["typo"], ["plain"]):
            with self.subTest(removals=wrong), self.assertRaises(Exception):
                want({**reg, "plain": {"rules": []}}, self.GROUPS, "n1", wrong)

    def test_a_site_managed_component_is_left_alone_in_every_phase(self):
        reg = {
            "eng": {"rules": [], "removal": "explicit", "managed": "False"},
            "app": {"rules": [{"switch": True, "group": "slurm-node"}], "needs": ["eng"]},
        }
        state = components.components_desired(reg, self.GROUPS, "n1")["eng"]
        self.assertEqual(state, "unmanaged", "even where a present component needs it")
        for phase in components.PHASES:
            self.assertEqual(components.component_phase(state, phase), "kept", phase)

    def test_a_retired_component_is_absent_everywhere_unless_the_site_manages_it(self):
        reg = {
            "old": {"retired": "replaced by new, 2026-11"},
            "drv": {"retired": "gone, 2026-11", "removal": "explicit"},
            "own": {"retired": "gone, 2026-11", "managed": False},
        }
        for host in ("m1", "n1", "k1"):
            want = components.components_desired(reg, self.GROUPS, host)
            self.assertEqual((want["old"], want["drv"], want["own"]), ("absent", "absent", "unmanaged"))
        self.assertEqual(components.component_hosts(reg, self.GROUPS, "old"), [])
        self.assertEqual(components.component_phase("absent", "install"), "kept", "installs nothing")
        rule = [{"switch": True, "group": "slurm-node"}]
        for bad in (
            {"old": {"retired": ""}},
            {"old": {"retired": "x", "rules": rule}},
            {"old": {"retired": "x", "needs": ["dep"]}, "dep": {"rules": []}},
            {"old": {"retired": "x"}, "app": {"rules": rule, "needs": ["old"]}},
        ):
            with self.subTest(registry=bad):
                self.assertTrue(components.check(bad))

    def test_require_installs_unless_the_site_manages_it(self):
        self.assertEqual([components.component_phase(s, "require") for s in ("absent", "kept", "present")],
                         ["present"] * 3)  # fmt: skip

    def test_phases(self):
        phase = components.component_phase
        self.assertEqual([phase(s) for s in ("present", "absent", "kept")], ["present", "absent", "kept"])
        self.assertEqual([phase(s, "install") for s in ("present", "absent")], ["present", "kept"])
        self.assertEqual(
            [phase(s, "remove") for s in ("present", "absent", "kept")], ["kept", "absent", "kept"]
        )
        with self.assertRaises(Exception):
            phase("present", "later")

    def test_component_hosts_follow_the_map(self):
        hosts = components.component_hosts
        self.assertEqual(hosts(self.REG, self.GROUPS, "srv"), ["m1"])
        self.assertEqual(hosts(self.REG, self.GROUPS, "dep"), hosts(self.REG, self.GROUPS, "top"))
        self.assertEqual(hosts(self.REG, self.GROUPS, "cli"), ["n1", "k1"], "inventory order, either deploy")

    def test_order_puts_needs_first(self):
        order = components.components_order(self.REG)
        self.assertLess(order.index("dep"), order.index("top"))


if __name__ == "__main__":
    unittest.main()
