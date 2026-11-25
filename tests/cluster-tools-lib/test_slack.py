"""cluster_tools.slack: the one Slack client. A member is found from the map, by email, or
not at all, and an answer is reused for a day; every DM goes through dm(),
logged for the administrators unless asked not to; a dry run prints instead of posting; a refusal names the method and Slack's reason."""

import email.message
import io
import json
import sys
import tempfile
import time
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

    ANSWERS = {"conversations.open": {"ok": True, "channel": {"id": "D9"}}, "chat.postMessage": {"ok": True, "ts": "1.2", "channel": "C42"},
               "chat.getPermalink": {"ok": True, "permalink": "https://x/p1"}, "chat.update": {"ok": True}}  # fmt: skip

    def posts(self, client):
        return [(p["channel"], p["text"]) for m, p in client.calls if m == "chat.postMessage"]

    def logged(self, d):
        client = Fake(self.ANSWERS)
        client.dm_log = slack.DmLog("#alerts", f"{d}/dm-log.json")
        return client

    def test_a_dm_is_logged_under_the_days_message_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            client = self.logged(d)
            with mock.patch("sys.stdout", io.StringIO()) as out:
                self.assertEqual(client.dm("U1ABC", "Your disk use is high.\nMore.", kind="disk", login="kim"),
                                 ("D9", "1.2"))  # fmt: skip
                client.dm("U2", "Idle GPUs.", kind="idle", login="lee")
            posts = [
                (m, p["channel"], p["text"], p.get("thread_ts"))
                for m, p in client.calls
                if m in ("chat.postMessage", "chat.update")
            ]
            day = time.strftime("%Y-%m-%d")
            self.assertEqual(posts[0][:2], ("chat.postMessage", "D9"))
            self.assertIn(slack.DM_LOG_NOTE, posts[0][2])
            self.assertEqual(posts[1], ("chat.postMessage", "#alerts", slack.dm_log_title(day, 0), None))
            # Then by the ID Slack gave: chat.update takes no channel name.
            self.assertEqual(posts[2], ("chat.postMessage", "C42",
                                        "`disk` to *kim*: Your disk use is high.  <https://x/p1|open>", "1.2"))  # fmt: skip
            self.assertEqual(posts[3][:3], ("chat.update", "C42", slack.dm_log_title(day, 1)))
            # The second DM of the day goes under the same message: no new one.
            self.assertEqual([p[1] for p in posts[4:]], ["D9", "C42", "C42"])
            self.assertEqual(posts[5][3], "1.2")
            self.assertEqual(posts[6][2], slack.dm_log_title(day, 2))
            self.assertIn("dm disk kim D9 1.2", out.getvalue())

    def test_a_failed_count_update_keeps_the_days_message(self):
        with tempfile.TemporaryDirectory() as d:
            log = slack.DmLog("#alerts", f"{d}/dm-log.json")
            client = Fake(self.ANSWERS)
            sent = client.call

            def call(method, **params):
                if method == "chat.update":
                    raise slack.Error("Slack chat.update failed: channel_not_found")
                return sent(method, **params)

            client.call = call
            with mock.patch("sys.stderr", io.StringIO()) as err:
                log.record(client, "a")
                log.record(client, "b")
            titles = [p for m, p in client.calls if m == "chat.postMessage" and "thread_ts" not in p]
            self.assertEqual(len(titles), 1, "one message a day, the count failing or not")
            self.assertIn("count was not updated", err.getvalue())

    def test_a_new_day_starts_a_new_message(self):
        with tempfile.TemporaryDirectory() as d:
            log = slack.DmLog("#alerts", f"{d}/dm-log.json")
            client = Fake(self.ANSWERS)
            log.record(client, "a", now=time.mktime((2026, 11, 24, 12, 0, 0, 0, 0, -1)))
            log.record(client, "b", now=time.mktime((2026, 11, 25, 9, 0, 0, 0, 0, -1)))
            titles = [p["text"] for m, p in client.calls if m == "chat.postMessage" and "thread_ts" not in p]
            self.assertEqual(
                titles, [slack.dm_log_title("2026-11-24", 0), slack.dm_log_title("2026-11-25", 0)]
            )

    def test_an_unlogged_dm_or_no_log_posts_the_dm_alone(self):
        with tempfile.TemporaryDirectory() as d:
            for client, log in ((self.logged(d), False), (Fake(self.ANSWERS), True)):
                with self.subTest(log=log):
                    with mock.patch("sys.stdout", io.StringIO()):
                        client.dm("U1ABC", "Job 42 ended.", kind="job", login="kim", log=log)
                    self.assertEqual(self.posts(client), [("D9", "Job 42 ended.")])

    def test_a_failed_log_line_never_fails_the_sent_dm(self):
        with tempfile.TemporaryDirectory() as d:
            client = self.logged(d)
            sent = client.call

            def call(method, **params):
                if method == "chat.postMessage" and params["channel"] == "#alerts":
                    raise slack.Error("Slack chat.postMessage failed: channel_not_found")
                return sent(method, **params)

            client.call = call
            with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(client.dm("U1ABC", "x", kind="io", login="kim"), ("D9", "1.2"))
            self.assertIn("the DM to kim (io) was sent; logging it failed", err.getvalue())

    def test_a_message_to_a_person_is_refused_outside_dm(self):
        client = Fake(self.ANSWERS)
        with self.assertRaisesRegex(slack.Error, "a message to a person is a dm"):
            client.post("U1ABC", "x")
        self.assertEqual(client.calls, [])

    def test_the_log_line_keeps_a_long_first_line_short(self):
        line = slack.dm_log_line("io", "kim", "y" * 500)
        self.assertLess(len(line), slack.DM_LOG_FIRST + 40)
        self.assertTrue(line.endswith("..."))

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
        self.assertTrue(slack.bar(None).startswith("-"))

    def test_a_korean_cell_takes_two_columns_per_character(self):
        """Padded by len(), a Korean header pushed every column after it out of line."""
        lines = slack.table(("사용자", "GPU"), [("alice", "8")]).splitlines()[1:3]
        self.assertEqual(lines, ["사용자  GPU", "alice   8"])

    def test_table_pads_columns_in_a_code_block(self):
        self.assertEqual(slack.table(("A", "BB"), [("xyz", 1)]), "```\nA    BB\nxyz  1\n```")


if __name__ == "__main__":
    unittest.main()
