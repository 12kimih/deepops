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
LOOKUP = next(t for t in TASKS if "block" in t)
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


BASE = {"slack_admin_group_id": "", "slack_admin_group": "@sysadmin", "slack_bot_token": "xoxb-x"}


def facts(values):
    """VALUES with the facts the role's tasks before the lookup set."""
    out = dict(values)
    for task in TASKS[: TASKS.index(LOOKUP)]:
        for name, value in (task.get("set_fact") or {}).items():
            out[name] = render(value, out)
    return out


class Lookup(unittest.TestCase):
    def runs(self, **values):
        return condition(LOOKUP["when"], facts({**BASE, **values}))

    def reported(self, found):
        """The names of the block's tasks that run once the lookup found FOUND: each
        under the block's condition as well as its own, with the facts the block set."""
        values = facts(BASE)
        use = next(t for t in LOOKUP["block"] if "set_fact" in t)
        values.update({k: v for k, v in use["set_fact"].items() if k != "slack_admin_group_id"})
        values.update(slack_admin_group_id=found, _slack_usergroups={"json": GROUPS})
        return [
            t["name"] for t in LOOKUP["block"][LOOKUP["block"].index(use) + 1 :]
            if condition(LOOKUP["when"], values) and condition(t.get("when", []), values)
        ]  # fmt: skip

    def test_the_found_id_is_said_and_a_miss_warned(self):
        """2026-11-25 (ARA 315): the block's condition named the ID and the flag its own
        set_fact sets, so both reports were always skipped and the ID never shown."""
        self.assertEqual(self.reported("S2"), ["Say the group's ID, to pin as slack_admin_group_id"])
        self.assertEqual(self.reported(""), ["Warn that the group was not found"])

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
