"""A role's include arguments mean in the included role what they mean where written.

Ansible evaluates the `vars` of an include_role lazily, inside the role it includes:
  - role_path and relative file lookups (file, fileglob, template) name the included
    role, not the one that wrote them (the slurm exporter's source glob found nothing
    in roles/managed_dir and its build context was emptied);
  - a value from the includer's dependencies (meta) is visible there only when the
    includer itself is public (prometheus_config_dir from prometheus_paths was
    undefined in managed_dir when a playbook included prometheus privately).
Patterns cannot see every way an argument reaches such a value (through a default that
refers to another, a filter, a lookup), so this asks Ansible itself: every task file
of every role is included as Ansible would include it, privately as Ansible does by
default, and each argument of each role include it reaches is evaluated in the
includer's context and in the included role's. They must agree.

An argument that differs only while its includer is private names a role that every
include site must include publicly (public: true); one that differs either way must be
resolved with a set_fact before the include.
"""

import atexit
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "lib"))
import local_scratch  # noqa: E402
from repo_scan import ansible_files, module, tasks, tracked  # noqa: E402

# Ansible's own defaults, not the repository's ansible.cfg (whose settings and
# fact cache would hide what a role does elsewhere): only the paths it adds are kept.
os.environ["ANSIBLE_CONFIG"] = str(Path(__file__).with_name("ansible.cfg"))
os.environ.update(unrecorded.env({}))  # no callbacks, no site vault password
os.environ["ANSIBLE_ROLES_PATH"] = str(ROOT / "roles")
os.environ["ANSIBLE_FILTER_PLUGINS"] = str(ROOT / "plugins" / "filter")
os.environ["ANSIBLE_ACTION_PLUGINS"] = str(ROOT / "plugins" / "action")
os.environ["ANSIBLE_COLLECTIONS_PATH"] = str(ROOT / "collections")
# Ansible's own default is ~/.ansible/tmp, on a login node the shared home.
_LOCAL_TEMP = local_scratch.directory("ansible-tmp")
atexit.register(local_scratch.remove, _LOCAL_TEMP)
os.environ["ANSIBLE_LOCAL_TEMP"] = str(_LOCAL_TEMP)

from ansible.inventory.manager import InventoryManager  # noqa: E402
from ansible.parsing.dataloader import DataLoader  # noqa: E402
from ansible.playbook.block import Block  # noqa: E402
from ansible.playbook.play import Play  # noqa: E402
from ansible.playbook.role_include import IncludeRole  # noqa: E402
from ansible.plugins.loader import init_plugin_loader  # noqa: E402
from ansible.template import Templar, trust_as_template  # noqa: E402
from ansible.vars.manager import VariableManager  # noqa: E402

init_plugin_loader()

# Lookups that only read the controller's files or environment; an argument using any
# other (pipe, url, ...) is not evaluated here.
SAFE_LOOKUPS = {"file", "fileglob", "template", "first_found", "env", "vars", "varnames", "indexed_items"}
LOOKUP = re.compile(r"\b(?:lookup|query|q)\(\s*['\"](?:ansible\.builtin\.)?(\w+)['\"]")
UNDEFINED = "<undefined>"
MINIMUM_INCLUDES = 30  # a check that evaluates fewer has lost its way


def task_files(role):
    base = ROOT / "roles" / role / "tasks"
    return [p.relative_to(base).as_posix() for p in tracked(f"roles/{role}/tasks/*.y*ml")]


def leaves(blocks):
    for item in blocks:
        if isinstance(item, Block):
            for part in (item.block, item.rescue, item.always):
                yield from leaves(part)
        else:
            yield item


class Ansible:
    def __init__(self):
        self.loader = DataLoader()
        self.loader.set_basedir(str(ROOT / "playbooks"))
        inventory = InventoryManager(loader=self.loader, sources=["localhost,"])
        self.vm = VariableManager(loader=self.loader, inventory=inventory)
        self.host = inventory.get_host("localhost")

    def entry(self, role, tasks_from, public):
        args = {"name": role, "tasks_from": tasks_from, "public": public}
        play = Play.load(
            {"hosts": "localhost", "gather_facts": False, "tasks": [{"ansible.builtin.include_role": args}]},
            variable_manager=self.vm,
            loader=self.loader,
        )
        return play, next(t for t in leaves(play.tasks) if isinstance(t, IncludeRole))

    def children(self, play, include):
        if include.public is None:
            include.public = False  # what post_validate makes of it at run time
        blocks, _ = include.get_block_list(play=play, variable_manager=self.vm, loader=self.loader)
        return list(leaves(blocks))

    def value(self, play, task, raw):
        variables = self.vm.get_vars(play=play, host=self.host, task=task)
        try:
            text = trust_as_template(raw) if isinstance(raw, str) else raw
            return repr(Templar(loader=self.loader, variables=variables).template(text))
        except Exception as e:  # undefined, or a lookup that found nothing to read
            return UNDEFINED if "undefined" in str(e) else f"<{type(e).__name__}>"


def mismatches(ansible, role, tasks_from, public, counter):
    """(include name, argument, value where written, value in the included role) for every
    role include reached from ROLE's TASKS_FROM, two levels deep."""
    found = []
    play, top = ansible.entry(role, tasks_from, public)
    pending = [(top, 0)]
    while pending:
        include, depth = pending.pop()
        for task in ansible.children(play, include):
            if not isinstance(task, IncludeRole) or "{{" in str(task._role_name):
                continue
            if depth < 2:
                pending.append((task, depth + 1))
            if not task.vars:
                continue
            inside = next(iter(ansible.children(play, task)), None)
            if inside is None:
                continue
            for name, raw in task.vars.items():
                if set(LOOKUP.findall(str(raw))) - SAFE_LOOKUPS:
                    continue
                counter[0] += 1
                here, there = ansible.value(play, task, raw), ansible.value(play, inside, raw)
                if here != UNDEFINED and here != there:
                    found.append((task.get_name(), task._role_name, name, here, there))
    return found


class IncludeScope(unittest.TestCase):
    def test_include_arguments_mean_the_same_in_the_included_role(self):
        ansible, counter = Ansible(), [0]
        broken, needs_public = [], set()
        for role in sorted({p.relative_to(ROOT).parts[1] for p in tracked("roles/*/tasks/*")}):
            for tasks_from in task_files(role):
                private = mismatches(ansible, role, tasks_from, False, counter)
                if not private:
                    continue
                public = {m[:3] for m in mismatches(ansible, role, tasks_from, True, [0])}
                for include, target, name, here, there in private:
                    where = f"roles/{role}/tasks/{tasks_from}: {include}: {target} {name}: {here[:70]} became {there[:70]}"
                    if (include, target, name) in public:
                        broken.append(where)
                    else:
                        needs_public.add(role)
        private_sites = []
        # Molecule's playbooks include roles too, privately unless they say otherwise: a
        # role that works only included publicly broke there first (nvidia_dcgm).
        molecule = sorted(
            path
            for stage in ("converge", "prepare", "side_effect", "verify", "cleanup")
            for path in tracked(f"roles/*/molecule/*/{stage}.yml")
        )
        for path in list(ansible_files()) + molecule:
            for task in tasks(path):
                name, args = module(task)
                if name == "include_role" and args.get("name") in needs_public and not args.get("public"):
                    private_sites.append(f"{path.relative_to(ROOT)}: {task.get('name')}: {args['name']}")
        self.assertGreater(counter[0], MINIMUM_INCLUDES)
        self.maxDiff = None
        self.assertEqual(
            [f"set_fact first: {b}" for b in broken] + [f"public: true: {p}" for p in private_sites],
            [],
            "an include argument must mean in the included role what it means where written",
        )


if __name__ == "__main__":
    unittest.main()
