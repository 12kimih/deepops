"""clustertools.slack: the one Slack client. A member is found from the map, by email, or
not at all, and an answer is reused for a day; a member ID becomes a DM channel; a
dry run prints instead of posting; a refusal names the method and Slack's reason."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import slack  # noqa: E402


class Fake(slack.Slack):
    def __init__(self, answers):
        super().__init__("xoxb-test")
        self.answers, self.calls = answers, []

    def call(self, method, **params):
        self.calls.append((method, params))
        return self.answers[method]


class SlackTests(unittest.TestCase):
    def test_member_from_map_email_or_nothing_and_cached(self):
        client = Fake({"users.lookupByEmail": {"ok": True, "user": {"id": "U2"}}})
        cache = {}
        self.assertEqual(slack.member(client, "a", {"a": "U1"}, "", cache), "U1")
        self.assertEqual(slack.member(client, "b", {}, "example.org", cache), "U2")
        self.assertEqual(client.calls, [("users.lookupByEmail", {"email": "b@example.org"})])
        self.assertEqual(slack.member(client, "b", {}, "example.org", cache), "U2")
        self.assertEqual(len(client.calls), 1, "a cached answer is not looked up again")
        self.assertEqual(slack.member(client, "c", {}, "", cache), "")

    def test_a_member_id_becomes_a_dm_channel(self):
        client = Fake({"conversations.open": {"ok": True, "channel": {"id": "D9"}}})
        self.assertEqual(client.channel("U1ABC"), "D9")
        self.assertEqual(client.channel("#infra-status"), "#infra-status")

    def test_dry_run_prints_and_posts_nothing(self):
        client = slack.Slack("", dry_run=True)
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("urllib.request.urlopen") as urlopen:
            self.assertEqual(client.post("#c", "hello"), "dry-run")
        urlopen.assert_not_called()
        self.assertIn("hello", out.getvalue())

    def test_a_refusal_names_the_method_and_reason(self):
        reply = io.BytesIO(json.dumps({"ok": False, "error": "channel_not_found"}).encode())
        with mock.patch("urllib.request.urlopen", return_value=reply):
            with self.assertRaisesRegex(slack.Error, "chat.postMessage failed: channel_not_found"):
                slack.Slack("xoxb-test").post("#nope", "x")


if __name__ == "__main__":
    unittest.main()
