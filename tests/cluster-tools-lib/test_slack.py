"""cluster_tools.slack: the one Slack client. A member is found from the map, by email, or
not at all, and an answer is reused for a day; a member ID becomes a DM channel; a
dry run prints instead of posting; a refusal names the method and Slack's reason."""

import email.message
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import slack  # noqa: E402


class Fake(slack.Slack):
    def __init__(self, answers):
        super().__init__("xoxb-test")
        self.answers, self.calls = answers, []

    def call(self, method, **params):
        self.calls.append((method, params))
        return self.answers[method]


class SlackTests(unittest.TestCase):
    def test_what_users_named_is_shown_as_text_and_columns_line_up(self):
        self.assertEqual(slack.escape("<!channel> & <@U1>"), "&lt;!channel&gt; &amp; &lt;@U1&gt;")
        out = slack.table(("PATH", "USER"), [("/scratch/x/<!here>", "alice"), ("/scratch/y", "bob")])
        self.assertNotIn("<!here>", out)
        lines = out.splitlines()[1:-1]
        shown = [line.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&") for line in lines]
        self.assertEqual(
            {line.index("alice") if "alice" in line else line.index("bob") for line in shown[1:]},
            {len("/scratch/x/<!here>") + 2},
        )

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

    def http_error(self, code, retry_after=None):
        headers = email.message.Message()
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return urllib.error.HTTPError(slack.API + "chat.postMessage", code, "x", headers, io.BytesIO(b"{}"))

    def test_a_rate_limited_call_is_sent_again_after_retry_after(self):
        ok = io.BytesIO(json.dumps({"ok": True, "ts": "1.2"}).encode())
        with (
            mock.patch("urllib.request.urlopen", side_effect=[self.http_error(429, "7"), ok]) as urlopen,
            mock.patch("time.sleep") as sleep,
        ):
            self.assertEqual(slack.Slack("xoxb-test").post("#c", "x"), "1.2")
        sleep.assert_called_once_with(7)
        self.assertEqual(urlopen.call_count, 2)

    def test_a_second_429_or_another_error_fails_once(self):
        for errors in (
            [self.http_error(429, "1"), self.http_error(429, "1")],
            [self.http_error(429)],
            [self.http_error(429, "3600")],
            [self.http_error(500)],
        ):
            with self.subTest(errors=[(e.code, e.headers.get("Retry-After")) for e in errors]):
                with (
                    mock.patch("urllib.request.urlopen", side_effect=errors),
                    mock.patch("time.sleep"),
                    self.assertRaisesRegex(slack.Error, "chat.postMessage failed: HTTP Error"),
                ):
                    slack.Slack("xoxb-test").post("#c", "x")

    def test_an_answer_that_is_not_an_object_fails(self):
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(b"[1]")):
            with self.assertRaisesRegex(slack.Error, "not a JSON object"):
                slack.Slack("xoxb-test").post("#c", "x")


class BlockTests(unittest.TestCase):
    def test_a_long_text_is_split_at_lines_and_nothing_is_lost(self):
        # Every line reaches Slack: a dropped one could be the mention that tells an owner.
        rows = [f"row {i:04d} " + "x" * 40 for i in range(200)]
        text = "head\n```\n" + "\n".join(rows) + "\n```"
        parts = [b["text"]["text"] for b in slack.sections(text)]
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p), 3000)
            self.assertEqual(p.count("```") % 2, 0, "each part closes the code block it opens")
        lines = [ln for p in parts for ln in p.splitlines() if ln.startswith("row ")]
        self.assertEqual(lines, rows)
        self.assertEqual([b["text"]["text"] for b in slack.sections("short")], ["short"])
        long = slack.sections("y" * 7000)
        self.assertTrue(all(len(b["text"]["text"]) <= 3000 for b in long))
        self.assertEqual(sum(len(b["text"]["text"]) for b in long), 7000, "a long line is split, not cut")

    def test_bar(self):
        self.assertEqual(slack.bar(100), "█" * 10 + " 100%")
        self.assertEqual(slack.bar(0), "░" * 10 + "   0%")
        self.assertTrue(slack.bar(None).startswith("n/a"))

    def test_table_pads_columns_in_a_code_block(self):
        self.assertEqual(slack.table(("A", "BB"), [("xyz", 1)]), "```\nA    BB\nxyz  1\n```")


if __name__ == "__main__":
    unittest.main()
