"""Local accounts with fixed IDs: the validation (plugins/filter/accounts.py), the
service-account registry (roles/system_accounts), and the people roster's share."""

import importlib.util
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import Scan, ansible_files, module, tasks  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "plugins" / "filter" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


accounts, people = load("accounts"), load("people")
SYSTEM = yaml.safe_load((ROOT / "roles/system_accounts/defaults/main.yml").read_text())["system_accounts"]
EXAMPLE = yaml.safe_load((ROOT / "config.example" / "people.yml").read_text())["people"]
# Tasks that use the user or group module outside roles/local_accounts, each with why:
# none creates an account of its own (left to the OS, its number differs per host).
OTHER_ACCOUNT_TASKS = {
    ("roles/move-home-dirs/tasks/move_user.yml", "Move home directory for {{ user }}"): "moves an existing home",
    ("roles/move-home-dirs/tasks/move_users.yml", "Create temporary user"): "a probe account, removed in the same run",
    ("roles/move-home-dirs/tasks/move_users.yml", "Delete temporary user"): "removes the probe account",
    ("roles/people/tasks/main.yml", "Lock the accounts of people who have left"): "locks existing accounts",
    # The NIS master's copy of the cluster groups, from directory_groups.
    ("roles/nis_server/tasks/present.yml", "Create the NIS-exported cluster groups"): "IDs from directory_groups",
    ("roles/nis_server/tasks/present.yml", "Add members to the NIS-exported cluster groups"): "membership only",
}  # fmt: skip


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

    def test_only_local_accounts_creates_accounts(self):
        # The IDs live in roles/system_accounts (and the roster) alone; a role creates its
        # account through roles/local_accounts, blocks included.
        scan, offenders = Scan(OTHER_ACCOUNT_TASKS, minimum=200), []
        for path in map(scan.seen, ansible_files()):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("roles/local_accounts/"):
                continue
            for task in tasks(path):
                if module(task)[0] in ("user", "group") and not scan.excuse((rel, task.get("name"))):
                    offenders.append(f"{rel}: {task.get('name')}")
        scan.check(self)
        self.assertEqual(offenders, [], "create the account through roles/local_accounts")


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

    def test_shells_are_the_rosters_and_the_default(self):
        roster = {
            **EXAMPLE,
            "erin": {"uid": 1005, "shell": "/usr/bin/zsh"},
            "gone": {"uid": 1006, "shell": "/bin/ksh", "status": "left"},
        }
        self.assertEqual(people.people_shells(roster), ["/bin/bash", "/usr/bin/zsh"])


if __name__ == "__main__":
    unittest.main()
