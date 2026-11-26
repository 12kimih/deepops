"""roles/alertmanager's alertmanager.yml: one Slack message per kind of alert, and the
Slack bot's transport when it is on.

Grouped by alertname and host, one fleet-wide event (a reboot of every node) posted a
message per host. Through the webhook a resolved alert can only be a second message;
through the bot (slack_bot_token) Alertmanager 0.35 edits the first one and threads
the rest. The rendered file is checked here; tests/monitoring-smoke runs amtool on it.
"""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import condition, load, render, role_variables, same_name  # noqa: E402

TEMPLATE = (ROOT / "roles/alertmanager/templates/alertmanager.yml.j2").read_text()


def config(**overrides):
    values = {**role_variables("alertmanager"), "ansible_managed": "", "grafana_root_url": "", **overrides}
    return yaml.safe_load(render(TEMPLATE, values))


def slack(cfg):
    return {r["name"]: r.get("slack_configs") for r in cfg["receivers"] if r["name"].startswith("slack")}


class Grouping(unittest.TestCase):
    def test_one_message_per_kind_of_alert(self):
        self.assertEqual(config()["route"]["group_by"], ["alertname"])


class Transport(unittest.TestCase):
    def test_webhook(self):
        cfg = config(alertmanager_slack_api_url="https://hooks.slack.example/x")
        self.assertEqual(cfg["global"]["slack_api_url"], "https://hooks.slack.example/x")
        self.assertNotIn("slack_app_token", cfg["global"])
        for name, configs in slack(cfg).items():
            with self.subTest(receiver=name):
                self.assertNotIn("update_message", configs[0])

    def test_the_bot_edits_its_message_and_threads_the_rest(self):
        cfg = config(alertmanager_slack_bot=True, slack_bot_token="xoxb-example",
                     alertmanager_slack_api_url="https://hooks.slack.example/x")  # fmt: skip
        self.assertEqual(cfg["global"]["slack_app_token"], "xoxb-example")
        self.assertEqual(cfg["global"]["slack_app_url"], "https://slack.com/api/chat.postMessage")
        self.assertNotIn("slack_api_url", cfg["global"], "one transport, not both")
        for name, configs in slack(cfg).items():
            with self.subTest(receiver=name):
                self.assertTrue(configs[0]["update_message"])
                self.assertTrue(configs[0]["post_updates_to_thread"])
                self.assertTrue(configs[0]["send_resolved"])

    def test_neither_leaves_slack_out(self):
        self.assertEqual(set(slack(config()).values()), {None})

    def test_the_bot_needs_its_token(self):
        check = next(
            t for t in load("roles/alertmanager/tasks/present.yml")
            if same_name(t.get("name"), "Check that alerts through the Slack bot have its token")
        )  # fmt: skip
        self.assertFalse(
            condition(check["assert"]["that"], {"alertmanager_slack_bot": True, "slack_bot_token": ""})
        )
        self.assertTrue(
            condition(check["assert"]["that"], {"alertmanager_slack_bot": True, "slack_bot_token": "x"})
        )
        self.assertTrue(
            condition(check["assert"]["that"], {"alertmanager_slack_bot": False, "slack_bot_token": ""})
        )


if __name__ == "__main__":
    unittest.main()
