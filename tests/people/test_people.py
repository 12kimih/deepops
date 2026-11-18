"""The people roster filters and their single validation (plugins/filter/people.py)."""

import copy
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("people_filter", ROOT / "plugins" / "filter" / "people.py")
people = importlib.util.module_from_spec(spec)
spec.loader.exec_module(people)
EXAMPLE = yaml.safe_load((ROOT / "config.example" / "people.yml").read_text())


class Derive(unittest.TestCase):
    def setUp(self):
        self.p = EXAMPLE["people"]

    def test_only_active_people_hold_roles(self):
        self.assertEqual(people.people_with_role(self.p, "admin"), ["alice"])
        self.assertEqual(people.people_with_role(self.p, "missing"), [])

    def test_left_people_are_listed_apart(self):
        self.assertEqual(people.people_left(self.p), ["carol"])
        self.assertNotIn("carol", people.people_active(self.p))

    def test_attribute_map(self):
        self.assertEqual(
            people.people_attr(self.p, "slack"),
            {"alice": "U0ALICE01", "bob": "U0BOB0002", "dave": "U0DAVE004"},
        )

    def test_accounting_users(self):
        self.assertEqual(
            people.people_accounting(self.p),
            {
                "alice": {
                    "accounts": {"admin": {"qos": ["high_a", "low", "urgent"]}, "team-a": {}},
                    "default_account": "team-a",
                },
                "bob": {"accounts": {"team-a": {}}},
                "dave": {"accounts": {"team-b": {}}},
            },
        )

    def test_left_entry_with_access_would_still_grant_nothing(self):
        p = copy.deepcopy(self.p)
        p["carol"].update(roles=["admin"], accounts={"team-a": {}})
        self.assertNotIn("carol", people.people_with_role(p, "admin"))
        self.assertNotIn("carol", people.people_accounting(p))


class Check(unittest.TestCase):
    def problems(self, mutate):
        r = copy.deepcopy(EXAMPLE)
        mutate(r)
        return people.check(r)

    def test_example_is_valid(self):
        self.assertEqual(people.check(EXAMPLE), [])

    def test_duplicate_uid(self):
        self.assertIn(
            "people.bob.uid: 1001 is also alice's",
            self.problems(lambda r: r["people"]["bob"].update(uid=1001)),
        )

    def test_uid_below_minimum_and_not_integer(self):
        self.assertTrue(self.problems(lambda r: r["people"]["bob"].update(uid=999)))
        self.assertTrue(self.problems(lambda r: r["people"]["bob"].update(uid="1002")))

    def test_bad_login_name(self):
        self.assertIn(
            "people.Bad Name: not a valid login name",
            self.problems(lambda r: r["people"].update({"Bad Name": {"uid": 1009}})),
        )

    def test_unknown_key_and_role(self):
        got = self.problems(lambda r: r["people"]["bob"].update(rol=["admin"], roles=["root"]))
        self.assertIn("people.bob.rol: unknown key", got)
        self.assertIn("people.bob.roles: unknown role root", got)

    def test_role_requirement(self):
        self.assertIn(
            "people.bob.roles: docker requires admin",
            self.problems(lambda r: r["people"]["bob"].update(roles=["docker"])),
        )

    def test_left_person_keeps_no_access(self):
        got = self.problems(lambda r: r["people"]["carol"].update(roles=["admin"], accounts={"team-a": {}}))
        self.assertIn("people.carol.roles: must be empty for someone who has left", got)
        self.assertIn("people.carol.accounts: must be empty for someone who has left", got)

    def test_default_account(self):
        self.assertIn(
            "people.alice.default_account: required with several accounts",
            self.problems(lambda r: r["people"]["alice"].pop("default_account")),
        )
        self.assertTrue(self.problems(lambda r: r["people"]["bob"].update(default_account="admin")))

    def test_member_keys_required_for_slurm_users_only(self):
        self.assertIn(
            "people.bob.slack: required for an active Slurm user",
            self.problems(lambda r: r["people"]["bob"].pop("slack")),
        )
        self.assertEqual(self.problems(lambda r: r["people"]["carol"].pop("slack", None)), [])

    def test_undeclared_attribute_and_requirement(self):
        self.assertIn(
            "people.bob.slack: unknown key",
            self.problems(lambda r: (r.pop("attributes"), r.pop("member_keys"))),
        )
        self.assertIn(
            "roles.docker.requires: unknown role root",
            self.problems(lambda r: r["roles"]["docker"].update(requires="root")),
        )

    def test_not_a_roster(self):
        self.assertTrue(people.check({"alice": {"uid": 1001}}))


class ExampleConfig(unittest.TestCase):
    """config.example's roster and accounting file make one consistent spec."""

    def test_roster_fills_the_accounting_spec(self):
        aspec = importlib.util.spec_from_file_location(
            "slurm_accounting", ROOT / "roles" / "slurm" / "library" / "slurm_accounting.py"
        )
        acct = importlib.util.module_from_spec(aspec)
        aspec.loader.exec_module(acct)
        spec = yaml.safe_load((ROOT / "config.example" / "files" / "slurm" / "accounting.yml").read_text())
        spec["users"] = {**(spec.get("users") or {}), **people.people_accounting(EXAMPLE["people"])}
        users = acct.desired_users(spec, acct.desired_accounts(spec), {})
        self.assertEqual(sorted(users), ["alice", "bob", "dave"])
        self.assertEqual(users["alice"]["default_account"], "team-a")
        self.assertEqual(users["alice"]["associations"]["admin"]["qos"], ["high_a", "low", "urgent"])


class Cli(unittest.TestCase):
    def test_exit_status(self):
        script = ROOT / "scripts" / "deepops" / "people.py"
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp, "good.yml"), Path(tmp, "bad.yml")
            good.write_text(yaml.safe_dump(EXAMPLE))
            broken = copy.deepcopy(EXAMPLE)
            broken["people"]["bob"]["uid"] = 1001
            bad.write_text(yaml.safe_dump(broken))

            def run(f):
                return subprocess.run(
                    [sys.executable, str(script), "check", str(f)], capture_output=True, text=True
                )

            self.assertEqual(run(good).returncode, 0)
            result = run(bad)
            self.assertEqual(result.returncode, 1)
            self.assertIn("1001 is also alice's", result.stderr)


if __name__ == "__main__":
    unittest.main()
