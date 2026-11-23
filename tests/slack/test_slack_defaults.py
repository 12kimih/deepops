"""roles/slack_defaults: the administrators' user group's ID, looked up by its handle
when the site gives none (a configured ID wins), read by every role that mentions the
administrators through slack_admin_mention, which run this role first."""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/lib"))
import in_tree  # noqa: E402
from ansible_yaml import condition, load, render, role_defaults  # noqa: E402

slack = in_tree.load("plugins/filter/slack.py")
TASKS = load("roles/slack_defaults/tasks/main.yml")
LOOKUP = TASKS[0]
GROUPS = {"ok": True, "usergroups": [{"id": "S1", "handle": "gpu"}, {"id": "S2", "handle": "sysadmin"}]}


class Filter(unittest.TestCase):
    def test_the_handle_with_or_without_its_at(self):
        self.assertEqual(slack.slack_usergroup_id(GROUPS, "@sysadmin"), "S2")
        self.assertEqual(slack.slack_usergroup_id(GROUPS, "sysadmin"), "S2")

    def test_not_found_or_slack_refusing_is_empty(self):
        self.assertEqual(slack.slack_usergroup_id(GROUPS, "@nobody"), "")
        self.assertEqual(slack.slack_usergroup_id({"ok": False, "error": "missing_scope"}, "@sysadmin"), "")
        self.assertEqual(slack.slack_usergroup_id({}, "@sysadmin"), "")
        self.assertEqual(slack.slack_usergroup_id(GROUPS, ""), "")


class Lookup(unittest.TestCase):
    def runs(self, **values):
        base = {"slack_admin_group_id": "", "slack_admin_group": "@sysadmin", "slack_bot_token": "xoxb-x"}
        return condition(LOOKUP["when"], {**base, **values})

    def test_only_without_a_configured_id(self):
        self.assertTrue(self.runs())
        self.assertFalse(self.runs(slack_admin_group_id="S9"), "a configured ID wins")
        self.assertFalse(self.runs(slack_admin_group=""))
        self.assertFalse(self.runs(slack_bot_token=""))
        self.assertFalse(self.runs(_slack_admin_group_looked=True), "once a run")

    def test_the_mention_follows_the_found_id(self):
        use = next(t for t in LOOKUP["block"] if "set_fact" in t)
        values = {"_slack_usergroups": {"json": GROUPS}, "slack_admin_group": "@sysadmin"}
        found = render(use["set_fact"]["slack_admin_group_id"], values)
        self.assertEqual(found, "S2")
        defaults = role_defaults("slack_defaults")
        mention = render(defaults["slack_admin_mention"], {"slack_admin_group_id": found})
        self.assertEqual(mention, "<!subteam^S2>")
        self.assertEqual(
            render(defaults["slack_admin_mention"], {"slack_admin_group_id": ""}),
            "",
            "none found: no mention",
        )


class Order(unittest.TestCase):
    def test_every_role_that_mentions_the_administrators_runs_this_role_first(self):
        for role in ("alertmanager", "slurm_usage_report", "cluster_bot"):
            deps = yaml.safe_load((ROOT / "roles" / role / "meta/main.yml").read_text())["dependencies"]
            names = [d["role"] if isinstance(d, dict) else d for d in deps]
            self.assertIn("slack_defaults", names, role)
        for path in ("roles/alertmanager/defaults/main.yml", "roles/slurm_usage_report/defaults/main.yml",
                     "roles/cluster_bot/vars/main.yml"):  # fmt: skip
            self.assertIn("slack_admin_mention", (ROOT / path).read_text(), path)


if __name__ == "__main__":
    unittest.main()
