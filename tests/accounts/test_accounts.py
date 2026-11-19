"""Local accounts with fixed IDs: the validation (plugins/filter/accounts.py), the
service-account registry (roles/system_accounts), and the people roster's share."""

import importlib.util
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "plugins" / "filter" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


accounts, people = load("accounts"), load("people")
SYSTEM = yaml.safe_load((ROOT / "roles/system_accounts/defaults/main.yml").read_text())["system_accounts"]
EXAMPLE = yaml.safe_load((ROOT / "config.example" / "people.yml").read_text())["people"]
# Tasks that still set IDs themselves, each to leave this list when it moves over:
# the slurm user (deepops' Slurm work moves it onto system_accounts.slurm) and the NIS
# cluster groups (the directory work, item 6, gives groups one registry too).
PENDING = {
    "roles/slurm/tasks/setup-user.yml",
    "roles/nis_server/tasks/main.yml",
}


class Check(unittest.TestCase):
    def test_defaults_are_filled_in(self):
        got = accounts.local_accounts_check({"svc": {"uid": 900}}, "/bin/false")
        self.assertEqual(
            got, {"svc": {"uid": 900, "gid": 900, "shell": "/bin/false", "system": True, "create_home": True}}
        )

    def test_every_problem_is_listed(self):
        found = accounts.check({
            "Bad Name": {"uid": 1},
            "a": {"uid": "1"},
            "b": {"uid": 2, "gid": 3, "colour": "red"},
            "c": {"uid": 3, "gid": 3},
            "d": {"uid": True},
        })  # fmt: skip
        self.assertEqual(found, [
            "Bad Name: not a valid account name",
            "a.uid: must be a non-negative integer",
            "a.gid: must be a non-negative integer",
            "b.colour: unknown key",
            "c.gid: 3 is also b's",
            "d.uid: must be a non-negative integer",
            "d.gid: must be a non-negative integer",
        ])  # fmt: skip

    def test_overlap_counts_uids_and_gids(self):
        self.assertEqual(
            accounts.overlap({"p": {"uid": 5}}, {"s": {"uid": 6, "gid": 5}}), ["p and s both use ID 5"]
        )
        self.assertEqual(accounts.overlap({"p": {"uid": 5}}, {"s": {"uid": 6}}), [])


class Registry(unittest.TestCase):
    def test_the_service_accounts_are_valid(self):
        self.assertEqual(accounts.check(SYSTEM), [])

    def test_no_example_person_takes_a_service_id(self):
        self.assertEqual(accounts.overlap(people.people_local_accounts(EXAMPLE), SYSTEM), [])

    def test_no_role_sets_its_own_service_ids(self):
        # The IDs live in roles/system_accounts alone; a role creates its account there.
        for path in sorted((ROOT / "roles").glob("*/tasks/**/*.yml")):
            for task in yaml.safe_load(path.read_text()) or []:
                for module in ("ansible.builtin.user", "ansible.builtin.group"):
                    args = task.get(module) if isinstance(task, dict) else None
                    rel = str(path.relative_to(ROOT))
                    if isinstance(args, dict) and "local_accounts" not in rel and rel not in PENDING:
                        with self.subTest(path=rel, task=task.get("name")):
                            self.assertNotIn("uid", args)
                            self.assertNotIn("gid", args)

    def test_pending_entries_still_need_it(self):
        for rel in sorted(PENDING):
            with self.subTest(path=rel):
                self.assertRegex(
                    (ROOT / rel).read_text(), r"\n\s+(uid|gid): ", "moved over: drop it from PENDING"
                )


class People(unittest.TestCase):
    def test_active_people_become_accounts(self):
        got = people.people_local_accounts(EXAMPLE, "/home", "/bin/zsh")
        self.assertEqual(sorted(got), ["alice", "bob", "dave"])
        self.assertEqual(
            got["alice"],
            {
                "uid": 1001,
                "shell": "/bin/bash",
                "home": "/home/alice",
                "system": False,
                "comment": "Alice Example",
            },
        )
        self.assertEqual(got["bob"], {"uid": 1002, "shell": "/bin/zsh", "home": "/home/bob", "system": False})
        self.assertEqual(accounts.check(got), [])


if __name__ == "__main__":
    unittest.main()
