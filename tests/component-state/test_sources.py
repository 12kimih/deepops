"""Every role a play applies whole that changes a host has a registry entry, and every
play that applies one records what it deployed at its true end (docs/deepops/
component-state.md, What a deploy records). 2026-11-27: the epilog fix in roles/slurm was
never listed, because only components recorded a digest."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import component_source as cs  # noqa: E402
import settings  # noqa: E402

KINDS = ("policy", "oneshot", "permanent", "operation")
# Roles that are the record itself: component_record has its own digest, deploy_record is
# the deploys' own bookkeeping.
BOOKKEEPING = ("component_record", "deploy_record")


def whole(play):
    """(the roles PLAY applies in roles:, the roles its tasks include whole, in order)."""
    roles = set()
    for entry in play.get("roles") or []:
        if isinstance(entry, dict):
            entry = entry.get("role") or entry.get("name")
        if cs._static(entry):
            roles.add(entry)
    tasks = []
    for key in ("pre_tasks", "tasks", "post_tasks"):
        for task in cs._tasks(play.get(key)):
            for inc in cs.INCLUDES:
                args = task.get(inc)
                if isinstance(args, dict) and cs._static(args.get("name")) and not args.get("tasks_from"):
                    if args["name"] not in tasks:
                        tasks.append(args["name"])
    return roles, tasks


class Sources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = settings.Tree(ROOT)
        cls.closure = cs.Closure(cls.tree)
        cls.managed = cs._load(cls.tree.text(cs.MANAGED_FILE))[cs.MANAGED]
        cls.recorded = set(cs.recorded(cls.tree).values())
        cls.plays = []
        for rel in sorted(cls.tree.files("playbooks")):
            if not rel.endswith((".yml", ".yaml")) or rel in cs.DEPLOYS or "molecule" in rel:
                continue
            data = cls.closure.parsed(rel)
            for play in data if isinstance(data, list) else []:
                if isinstance(play, dict) and "hosts" in play:
                    cls.plays.append((rel, play))

    def changes_a_host(self, role):
        return not (role.endswith(("_defaults", "_paths")) or role in BOOKKEEPING or self.closure.inert(role))

    def test_every_role_a_play_applies_whole_has_an_entry(self):
        components = set(cs.component_roles(self.tree).values())
        missing = set()
        for rel, play in self.plays:
            roles, tasks = whole(play)
            for role in roles | set(tasks):
                if self.closure.role_files(role) and self.changes_a_host(role):
                    if role not in components and role not in self.managed:
                        missing.add(f"{role} ({rel})")
        self.assertEqual(sorted(missing), [], "add each to deepops_managed_roles with its kind")

    def test_each_managed_entry_names_a_kind_and_its_playbook(self):
        components = set(cs._load(self.tree.text(cs.MANAGED_FILE))[cs.REGISTRY])
        applied = {}
        for rel, play in self.plays:
            roles, tasks = whole(play)
            for role in roles | set(tasks):
                applied.setdefault(role, set()).add(rel)
        for name, entry in self.managed.items():
            with self.subTest(role=name):
                self.assertTrue(self.closure.role_files(name), "no such role")
                self.assertNotIn(name, components, "a component of the registry is not managed too")
                self.assertIn(entry.get("kind"), KINDS)
                if entry["kind"] == "operation":
                    self.assertNotIn("playbook", entry, "an operation records nothing")
                else:
                    self.assertIn(
                        entry.get("playbook"), applied.get(name, ()), "a playbook applying it whole"
                    )

    def test_everything_recorded_has_its_playbook(self):
        """A digest the alert and sync.py compare names the playbook that brings its hosts
        level: a role that records present (bmc did) with no entry had none."""
        import component_playbooks

        playbooks = component_playbooks.playbooks(ROOT)
        self.assertEqual(sorted(n for n in cs.recorded(self.tree) if n not in playbooks), [])

    def test_every_play_that_applies_one_records_at_its_end(self):
        """The record is the play's last post_task, so it runs after its roles, tasks and
        (flushed first) handlers; it names the roles the tasks include whole, which
        ansible_play_role_names does not."""
        for rel, play in self.plays:
            roles, tasks = whole(play)
            also = [r for r in tasks if r in self.recorded]
            if not (roles & self.recorded or also):
                continue
            with self.subTest(playbook=rel, play=play.get("name") or play.get("hosts")):
                last = (play.get("post_tasks") or [{}])[-1]
                args = last.get("ansible.builtin.include_role") or {}
                self.assertEqual(
                    (args.get("name"), args.get("tasks_from")), ("component_record", "sources"),
                    "end the play with the record (post_tasks, last)",
                )  # fmt: skip
                said = (last.get("vars") or {}).get("component_record_sources_also", [])
                self.assertEqual(sorted(said), sorted(also), "component_record_sources_also")


if __name__ == "__main__":
    unittest.main()
