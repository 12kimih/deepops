"""The people roster filters and their single validation (plugins/filter/people.py)."""

import copy
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

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

    def test_uids_of_a_role_are_its_active_holders(self):
        self.assertEqual(people.people_uids(self.p, "admin"), [1001])
        self.assertEqual(people.people_uids(self.p, "missing"), [])

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

    def test_subids_follow_the_uid_alone(self):
        ranges = people.people_subids(self.p)
        self.assertEqual([r["name"] for r in ranges], ["alice", "bob", "dave"])
        self.assertEqual(ranges[0], {"name": "alice", "uid": 1001, "start": 165536, "count": 65536})
        # dave keeps his range when carol (1003, left) is gone from the active list
        self.assertEqual(ranges[2]["start"], 100000 + 4 * 65536)

    def test_subids_refuse_overlap_and_overflow(self):
        from ansible.errors import AnsibleFilterError

        with self.assertRaises(AnsibleFilterError):
            people.people_subids(self.p, base=1004)
        with self.assertRaises(AnsibleFilterError):
            people.people_subids({"zed": {"uid": 70000}})

    def test_left_entry_with_access_would_still_grant_nothing(self):
        p = copy.deepcopy(self.p)
        p["carol"].update(roles=["admin"], accounts={"team-a": {}})
        self.assertNotIn("carol", people.people_with_role(p, "admin"))
        self.assertNotIn("carol", people.people_accounting(p))

    def test_disk_quotas_are_the_default_under_each_persons_own(self):
        default = {"block_warn": "10G", "block_hard": "20G"}
        got = people.people_disk_quotas(self.p, default)
        self.assertEqual(got["alice"], {"block_warn": "50G", "block_hard": "100G"})
        self.assertEqual(got["bob"], default)
        self.assertNotIn("carol", got, "someone who left is limited no longer")
        # No default: only who has their own, so quotas only count for the rest.
        self.assertEqual(
            people.people_disk_quotas(self.p, {}), {"alice": {"block_warn": "50G", "block_hard": "100G"}}
        )


class Check(unittest.TestCase):
    def problems(self, mutate):
        r = copy.deepcopy(EXAMPLE)
        mutate(r)
        return people.check(r)

    def test_example_is_valid(self):
        self.assertEqual(people.check(EXAMPLE), [])

    def test_disk_quota_takes_only_its_limits(self):
        self.assertIn(
            "people.bob.disk_quota: a mapping of block_warn, block_hard, inode_warn, inode_hard",
            self.problems(lambda r: r["people"]["bob"].update(disk_quota={"size": "1G"})),
        )
        self.assertIn(
            "people.bob.disk_quota: a mapping of block_warn, block_hard, inode_warn, inode_hard",
            self.problems(lambda r: r["people"]["bob"].update(disk_quota="20G")),
        )

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

    def test_several_accounts_only_for_the_named_roles(self):
        # alice (admin) is in two accounts; bob gets a second one.
        def two_for_bob(r):
            r["several_accounts"] = ["admin"]
            r["people"]["bob"].update(accounts={"team-a": {}, "admin": {}}, default_account="team-a")

        got = self.problems(two_for_bob)
        self.assertIn(
            "people.bob.accounts: several accounts are for admin (several_accounts): "
            "each association is its own fair share",
            got,
        )
        self.assertFalse([p for p in got if p.startswith("people.alice")], "an admin may")
        self.assertIn(
            "several_accounts: unknown role root",
            self.problems(lambda r: r.update(several_accounts=["root"])),
        )
        self.assertFalse(self.problems(lambda r: None), "absent: no limit")

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
        acct = in_tree.load("roles/slurm/library/slurm_accounting.py")
        spec = yaml.safe_load((ROOT / "config.example" / "files" / "slurm" / "accounting.yml").read_text())
        spec["users"] = {**(spec.get("users") or {}), **people.people_accounting(EXAMPLE["people"])}
        users = acct.desired_users(spec, acct.desired_accounts(spec), {})
        self.assertEqual(sorted(users), ["alice", "bob", "dave"])
        self.assertEqual(users["alice"]["default_account"], "team-a")
        self.assertEqual(users["alice"]["associations"]["admin"]["qos"], ["high_a", "low", "urgent"])


class Edit(unittest.TestCase):
    """people.py add and leave: the one way the roster is edited, by a person or the bot.
    The file keeps its comments and order; nothing is written unless check passes."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.roster = self.dir / "people.yml"
        shutil.copy(ROOT / "config.example" / "people.yml", self.roster)
        (self.dir / "files" / "slurm").mkdir(parents=True)
        shutil.copy(
            ROOT / "config.example" / "files" / "slurm" / "accounting.yml", self.dir / "files" / "slurm"
        )
        self.before = self.roster.read_text()

    def run_people(self, *args):
        script = ROOT / "scripts" / "deepops" / "people.py"
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)

    def test_add_gives_the_next_uid_in_order_and_only_adds_lines(self):
        done = self.run_people("add", str(self.roster), "carl", "--account", "team-a", "--slack", "U0CARL001")
        self.assertEqual(done.returncode, 0, done.stderr)
        after = yaml.safe_load(self.roster.read_text())["people"]
        uids = [p["uid"] for n, p in after.items() if n != "carl"]
        self.assertEqual(
            after["carl"], {"uid": max(uids) + 1, "slack": "U0CARL001", "accounts": {"team-a": {}}}
        )
        names = list(after)
        i = names.index("carl")
        self.assertTrue(names[i - 1] < "carl" < names[i + 1], names)  # beside its neighbours
        removed = [
            line for line in self.before.splitlines() if line not in self.roster.read_text().splitlines()
        ]
        self.assertEqual(removed, [], "comments and every other line stay")

    def test_add_refuses_a_duplicate_an_undeclared_account_and_a_failing_check(self):
        for args in (["bob", "--account", "team-a", "--slack", "U0X"], ["eve", "--account", "nosuch", "--slack", "U0X"],
                     ["eve", "--account", "team-a"]):  # fmt: skip
            self.assertEqual(self.run_people("add", str(self.roster), *args).returncode, 1, args)
        self.assertEqual(self.roster.read_text(), self.before)

    def test_leave_keeps_the_uid_and_drops_access(self):
        self.assertEqual(self.run_people("leave", str(self.roster), "alice").returncode, 0)
        alice = yaml.safe_load(self.roster.read_text())["people"]["alice"]
        self.assertEqual(alice["status"], "left")
        self.assertFalse({"roles", "accounts", "default_account", "disk_quota"} & set(alice))
        self.assertEqual(alice["uid"], 1001)


class Leaving(unittest.TestCase):
    """Past `ends` a person keeps the account (login until ends + grace) but no Slurm
    association and no role; people_login_expires is the one date the directory and the
    local accounts apply."""

    ROSTER = {
        "now": {"uid": 1001, "roles": ["admin"], "accounts": {"a": {}}},
        "soon": {"uid": 1002, "roles": ["admin"], "accounts": {"a": {}}, "ends": "2027-02-28"},
        "past": {"uid": 1003, "roles": ["admin"], "accounts": {"a": {}}, "ends": "2026-10-31"},
        "gone": {"uid": 1004, "status": "left"},
    }

    def test_the_login_expiry_of_each_kind_of_person(self):
        expires = {n: people.people_login_expires(p, 30) for n, p in self.ROSTER.items()}
        self.assertEqual(
            expires, {"now": "", "soon": "2027-03-30", "past": "2026-11-30", "gone": "1970-01-02"}
        )

    def test_a_person_past_their_end_has_no_association_and_no_role(self):
        today = "2026-11-22"
        self.assertEqual(sorted(people.people_accounting(self.ROSTER, today)), ["now", "soon"])
        self.assertEqual(people.people_with_role(self.ROSTER, "admin", today), ["now", "soon"])
        self.assertEqual(people.people_uids(self.ROSTER, "admin", today), [1001, 1002])
        self.assertIn("past", people.people_active(self.ROSTER), "the account itself stays")

    def test_a_date_yaml_reads_or_a_string_and_nothing_else(self):
        import datetime

        self.assertEqual(people.people_login_expires({"ends": datetime.date(2027, 1, 1)}, 30), "2027-01-31")
        problems = people.check({"people": {"x": {"uid": 1001, "ends": "next spring"}}})
        self.assertIn("people.x.ends: a date, YYYY-MM-DD", problems)

    def test_leave_with_an_end_date_keeps_the_person_active(self):
        text = (ROOT / "config.example" / "people.yml").read_text()
        out = yaml.safe_load(people.roster_leave(text, "alice", ends="2027-02-28"))["people"]["alice"]
        self.assertEqual((out.get("status", "active"), str(out["ends"])), ("active", "2027-02-28"))
        self.assertIn("accounts", out)
        with self.assertRaises(people.EditError):
            people.roster_leave(text, "alice", ends="soon")


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

    def test_nothing_to_check_is_a_failure(self):
        from unittest import mock

        spec = importlib.util.spec_from_file_location(
            "people_cli", ROOT / "scripts" / "deepops" / "people.py"
        )
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with mock.patch.object(cli, "default_files", return_value=[]):
            self.assertEqual(cli.main(["check"]), 1)


if __name__ == "__main__":
    unittest.main()


class ShadowDay(unittest.TestCase):
    """people_shadow_day counts days in UTC: day 1 is the lock marker the people role
    reads back, and the user module's expires is the day times 86400 seconds."""

    def test_days_since_the_epoch(self):
        self.assertEqual(people.people_shadow_day("1970-01-02"), 1)
        self.assertEqual(people.people_shadow_day("2027-01-31"), 20849)
        self.assertEqual(people.people_shadow_day(""), "")

    def test_left_is_day_one(self):
        self.assertEqual(people.people_shadow_day(people.people_login_expires({"status": "left"}, 30)), 1)
