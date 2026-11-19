"""slurm_accounting plans exactly the sacctmgr commands that close the gap.

The module is tested through its pure functions: parse sacctmgr -nP output,
compare it with a spec, and plan commands. Data here is synthetic.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = spec_from_file_location(
    "slurm_accounting", ROOT / "roles" / "slurm" / "library" / "slurm_accounting.py"
)
acct = module_from_spec(spec)
spec.loader.exec_module(acct)

QOS = "\n".join(
    [
        "normal|0||cluster|||||1.000000|Normal QOS default",
        "low|0||cluster||||1-00:00:00|1.000000|low",
        "high_a|10000|low|cluster||gres/gpu:x=4,gres/gpu:y=2|||1.000000|high_a",
    ]
)
ASSOC = "\n".join(
    [
        "root|||1||normal||",
        "root|root||0||normal||",
        "lab||root|parent||low|low|500",  # account|user|parent|fairshare|priority|qos|defaultqos|maxsubmit
        "a||lab|parent||high_a,low|low|",
        "a|alice||1||high_a,low|low|",
        "a|bob||1||high_a,low|low|",
    ]
)
ACCOUNTS = "\n".join(["root|default root account|root", "lab|lab|lab", "a|team a|lab"])

SPEC = {
    "qos": {
        "normal": {"description": "Normal QOS default"},
        "low": {"max_wall": "1-00:00:00", "description": "low"},
        "high_a": {
            "priority": 10000,
            "preempt": ["low"],
            "grp_tres": {"gres/gpu:x": 4, "gres/gpu:y": 2},
            "description": "high_a",
        },
    },
    "accounts": {
        "lab": {
            "fairshare": "parent",
            "qos": ["low"],
            "default_qos": "low",
            "description": "lab",
            "max_submit_jobs": 500,
        },
        "a": {
            "parent": "lab",
            "fairshare": "parent",
            "qos": ["high_a", "low"],
            "description": "team a",
            "members": ["alice", "bob"],
        },
    },
}


def current():
    q = acct.current_qos(QOS)
    a, u = acct.current_assocs(ASSOC, ACCOUNTS)
    return q, a, u


def plan(spec, prune=False):
    cmds, unmanaged = acct.plan(spec, *current(), prune=prune)
    return [c for c in cmds if not isinstance(c, tuple)], unmanaged


def copy(data):
    import copy as _copy

    return _copy.deepcopy(data)


class Plan(unittest.TestCase):
    def test_matching_spec_plans_nothing(self):
        cmds, unmanaged = plan(SPEC)
        self.assertEqual(cmds, [])
        self.assertEqual(unmanaged, {"qos": [], "accounts": [], "associations": []})

    def test_new_qos_are_created_before_preempt_names_them(self):
        s = copy(SPEC)
        s["qos"]["urgent"] = {"priority": 20000, "preempt": ["high_a", "low"]}
        cmds, _ = plan(s)
        self.assertEqual(cmds[0], ["add", "qos", "urgent"])
        self.assertIn(
            [
                "modify",
                "qos",
                "where",
                "name=urgent",
                "set",
                "priority=20000",
                "preempt=high_a,low",
                "flags=-1",
            ],
            cmds,
        )

    def test_account_priority_and_tres_clearing(self):
        s = copy(SPEC)
        s["accounts"]["a"]["priority"] = 5000
        s["qos"]["high_a"]["grp_tres"] = {"gres/gpu:x": 6}
        cmds, _ = plan(s)
        self.assertIn(["modify", "account", "where", "name=a", "set", "priority=5000"], cmds)
        self.assertIn(
            ["modify", "qos", "where", "name=high_a", "set", "grptres=gres/gpu:x=6,gres/gpu:y=-1"], cmds
        )

    def test_member_inherits_unless_overridden(self):
        s = copy(SPEC)
        s["accounts"]["a"]["members"] = ["alice", {"user": "bob", "qos": ["low"]}]
        cmds, _ = plan(s)
        self.assertEqual(cmds, [["modify", "user", "where", "name=bob", "account=a", "set", "qos=low"]])

    def test_values_are_raw_arguments_not_shell_quoted(self):
        s = copy(SPEC)
        s["accounts"]["a"]["description"] = "team a's lab"
        s["qos"]["high_a"]["preempt"] = []
        cmds, _ = plan(s)
        self.assertIn(["modify", "account", "where", "name=a", "set", "description=team a's lab"], cmds)
        self.assertIn(["modify", "qos", "where", "name=high_a", "set", "preempt="], cmds)

    def test_new_member_gets_an_association(self):
        s = copy(SPEC)
        s["accounts"]["a"]["members"].append("carol")
        cmds, _ = plan(s)
        self.assertIn(["add", "user", "carol", "account=a"], cmds)

    def test_default_account_is_explicit_for_several_accounts(self):
        s = copy(SPEC)
        s["accounts"]["lab"]["members"] = ["alice"]
        with self.assertRaisesRegex(ValueError, "default_account"):
            plan(s)
        s["users"] = {"alice": {"default_account": "a"}}
        cmds, _ = acct.plan(s, *current())
        self.assertIn(("default", "alice", "a"), cmds)

    def test_members_given_per_user_plan_the_same(self):
        s = copy(SPEC)
        del s["accounts"]["a"]["members"]
        s["users"] = {"alice": {"accounts": {"a": None}}, "bob": {"accounts": {"a": {}}}}
        self.assertEqual(plan(s), plan(SPEC))
        s["users"]["bob"]["accounts"]["a"] = {"qos": ["low"]}
        cmds, _ = plan(s)
        self.assertEqual(cmds, [["modify", "user", "where", "name=bob", "account=a", "set", "qos=low"]])

    def test_per_user_member_of_an_undeclared_account_is_refused(self):
        s = copy(SPEC)
        s["users"] = {"carol": {"accounts": {"nope": {}}}}
        with self.assertRaisesRegex(ValueError, "account nope is not declared"):
            plan(s)

    def test_same_association_in_both_forms_is_refused(self):
        s = copy(SPEC)
        s["users"] = {"alice": {"accounts": {"a": {}}}}
        with self.assertRaisesRegex(ValueError, "listed twice"):
            plan(s)

    def test_undefined_qos_is_refused(self):
        s = copy(SPEC)
        s["accounts"]["a"]["qos"] = ["high_a", "low", "nope"]
        with self.assertRaisesRegex(ValueError, "nope"):
            plan(s)

    def test_unmanaged_is_reported_and_pruned_only_on_request(self):
        s = copy(SPEC)
        s["accounts"]["a"]["members"] = ["alice"]
        cmds, unmanaged = plan(s)
        self.assertEqual(unmanaged["associations"], ["bob@a"])
        self.assertFalse(any(c[0] == "delete" for c in cmds))
        cmds, _ = plan(s, prune=True)
        self.assertIn(["delete", "user", "where", "name=bob", "account=a"], cmds)

    def test_parents_are_created_before_children(self):
        s = copy(SPEC)
        s["accounts"]["b1"] = {"parent": "b", "fairshare": "parent"}
        s["accounts"]["b"] = {"parent": "lab", "fairshare": "parent"}
        cmds, _ = plan(s)
        adds = [c[2] for c in cmds if c[:2] == ["add", "account"]]
        self.assertEqual(adds, ["b", "b1"])

    def test_max_submit_jobs_is_set_and_cleared(self):
        s = copy(SPEC)
        s["accounts"]["lab"]["max_submit_jobs"] = 200
        cmds, _ = plan(s)
        self.assertIn(["modify", "account", "where", "name=lab", "set", "maxsubmitjobs=200"], cmds)
        del s["accounts"]["lab"]["max_submit_jobs"]
        cmds, _ = plan(s)
        self.assertIn(["modify", "account", "where", "name=lab", "set", "maxsubmitjobs=-1"], cmds)

    def test_qos_tiers_must_not_overlap(self):
        s = copy(SPEC)
        s["qos"]["urgent"] = {"priority": 20000, "preempt": ["high_a", "low"]}
        s["accounts"]["a"]["priority"] = 5000
        flags = ["NO_NORMAL_QOS", "NO_NORMAL_ASSOC"]
        weights = {"qos": 1, "assoc": 1, "fairshare": 1000, "age": 100}
        self.assertIsNone(acct.check_qos_tiers(s, weights, flags))
        s["accounts"]["a"]["priority"] = 9000
        self.assertRegex(acct.check_qos_tiers(s, weights, flags), "overlap")
        # The old setup: normalised QoS with hotfix at 100000, so high scored
        # 10000/100000 x 10000 = 1000, no more than fairshare could add.
        s["accounts"]["a"]["priority"] = None
        s["qos"]["urgent"]["priority"] = 100000
        self.assertRegex(acct.check_qos_tiers(s, {"qos": 10000, "fairshare": 1000}, []), "overlap")

    def test_descriptions_compare_without_case(self):
        # sacctmgr stores QoS descriptions lowercased; a capitalised spec is not a change.
        s = copy(SPEC)
        s["qos"]["high_a"]["description"] = "High_A"
        self.assertEqual(plan(s)[0], [])

    def test_inherited_limits_are_not_cleared(self):
        # Read with wopl, a child shows only limits set on it; an inherited one is blank
        # and must stay so, since the spec does not set it on the child either.
        cmds, _ = plan(SPEC)
        self.assertFalse(any("maxsubmitjobs=-1" in c for cmd in cmds for c in cmd))

    def test_root_account_is_converged_never_created(self):
        s = copy(SPEC)
        s["accounts"]["root"] = {"qos": ["normal"], "members": [{"user": "root", "fairshare": 1}]}
        cmds, unmanaged = plan(s)
        self.assertFalse(any(c[:2] == ["add", "account"] or "parent=" in " ".join(c) for c in cmds))
        self.assertIn(["modify", "user", "where", "name=root", "account=root", "set", "fairshare=1"], cmds)
        self.assertEqual(unmanaged["associations"], [])

    def test_time_parsing(self):
        self.assertEqual(acct.minutes("1-00:00:00"), 1440)
        self.assertEqual(acct.minutes("02:30:00"), 150)
        self.assertEqual(acct.minutes("UNLIMITED"), None)
        self.assertEqual(acct.fmt_minutes(1440), "1-00:00:00")
        # After a day count the first field is hours: 2-12 is two and a half days.
        self.assertEqual(acct.minutes("2-12"), 3600)
        self.assertEqual(acct.minutes("2-12:30"), 3630)
        self.assertEqual(acct.minutes("90"), 90)
        self.assertEqual(acct.minutes("90:30"), 91)  # MM:SS, seconds round up

    def test_a_new_qos_gets_its_preempt_mode_on_creation(self):
        s = copy(SPEC)
        s["qos"]["urgent"] = {"priority": 20000, "preempt_mode": "requeue"}
        cmds, _ = plan(s)
        self.assertTrue(any("preemptmode=requeue" in cmd for cmd in cmds))

    def test_prune_deletes_children_before_parents(self):
        s = copy(SPEC)
        del s["accounts"]["a"], s["accounts"]["lab"]
        cmds, _ = plan(s, prune=True)
        deleted = [c[3] for c in cmds if c[:2] == ["delete", "account"]]
        self.assertEqual(deleted, ["name=a", "name=lab"])


if __name__ == "__main__":
    unittest.main()
