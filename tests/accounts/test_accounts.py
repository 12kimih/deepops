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
_SYSTEM = yaml.safe_load((ROOT / "roles/system_accounts/defaults/main.yml").read_text())
SYSTEM, GROUPS = _SYSTEM["system_accounts"], _SYSTEM["system_groups"]
EXAMPLE = yaml.safe_load((ROOT / "config.example" / "people.yml").read_text())["people"]
# Tasks that use the user or group module outside roles/local_accounts, each with why:
# none creates an account of its own (left to the OS, its number differs per host).
OTHER_ACCOUNT_TASKS = {
    ("roles/move-home-dirs/tasks/move_user.yml", "Move home directory for {{ user }}"): "moves an existing home",
    ("roles/move-home-dirs/tasks/move_users.yml", "Create temporary user"): "a probe account, removed in the same run",
    ("roles/move-home-dirs/tasks/move_users.yml", "Delete temporary user"): "removes the probe account",
    ("roles/people/tasks/main.yml", "Lock the accounts of people who have left"): "locks existing accounts",
    ("roles/people/tasks/main.yml", "Set when each active person's login ends"): "expiry of existing accounts, from people_login_ends",
    # The NIS master's copy of the cluster groups, from directory_groups.
    ("roles/nis_server/tasks/present.yml", "Create the NIS-exported cluster groups"): "IDs from directory_groups",
    ("roles/nis_server/tasks/present.yml", "Add members to the NIS-exported cluster groups"): "membership only",
}  # fmt: skip


class Check(unittest.TestCase):
    def test_defaults_are_filled_in(self):
        got = accounts.local_accounts_check({"svc": {"uid": 900}}, "/bin/false")
        self.assertEqual(
            got,
            {
                "svc": {
                    "uid": 900,
                    "gid": 900,
                    "shell": "/bin/false",
                    "system": True,
                    "create_home": True,
                    "groups": [],
                }
            },  # fmt: skip
        )

    def test_an_account_joins_only_a_declared_group_and_no_person_takes_its_id(self):
        groups = {"cluster-metrics": {"gid": 900}}
        self.assertEqual(accounts.check({"svc": {"uid": 901, "groups": ["cluster-metrics"]}}, groups), [])
        self.assertEqual(accounts.check({"svc": {"uid": 901, "groups": ["wheel"]}}, groups),
                         ["svc.groups: wheel is not a declared group"])  # fmt: skip
        self.assertEqual(accounts.check({"svc": {"uid": 901, "groups": "cluster-metrics"}}, groups),
                         ["svc.groups: must be a list of group names"])  # fmt: skip
        self.assertEqual(accounts.overlap({"kim": {"uid": 900}}, {}), [])
        with self.assertRaises(Exception):
            accounts.accounts_disjoint({"kim": {"uid": 1000, "gid": 900}}, {}, groups)


class PrivateGroup(unittest.TestCase):
    """Only a user's own group may write what stands for root's word (the expected
    component state the alerts read)."""

    def test_the_users_own_group_with_no_other_member(self):
        for members in ("", "kim"):
            self.assertTrue(accounts.private_group(["x", "1001", members], "kim", "kim"), members)

    def test_a_shared_or_foreign_or_missing_group_is_not(self):
        self.assertFalse(accounts.private_group(["x", "100", ""], "users", "kim"), "a shared primary group")
        self.assertFalse(accounts.private_group(["x", "1001", "kim,lee"], "kim", "kim"), "another member")
        self.assertFalse(accounts.private_group(None, "kim", "kim"), "no such group on the host")


class Registry(unittest.TestCase):
    def test_the_service_accounts_are_valid(self):
        self.assertEqual(accounts.check(SYSTEM, GROUPS), [])
        taken = {a["uid"] for a in SYSTEM.values()} | {a.get("gid", a["uid"]) for a in SYSTEM.values()}
        self.assertFalse(taken & {g["gid"] for g in GROUPS.values()}, "a shared group's GID is its own")

    def test_no_example_person_takes_a_service_name_or_id(self):
        self.assertEqual(accounts.overlap(people.people_local_accounts(EXAMPLE), SYSTEM), [])
        accounts.accounts_disjoint(people.people_local_accounts(EXAMPLE), SYSTEM, GROUPS)

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
