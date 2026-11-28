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
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/lib"))
from cluster_tools import rich, slack  # noqa: E402
from fake_slack import FakeChannel  # noqa: E402


class Fake(slack.Slack):
    def __init__(self, answers):
        super().__init__("xoxb-test")
        self.answers, self.calls = answers, []

    def call(self, method, **params):
        self.calls.append((method, params))
        return self.answers[method]


class GrantedScopes(unittest.TestCase):
    """The token's scopes as Slack granted them: auth.test's x-oauth-scopes header."""

    def answer(self, body, header):
        reply = mock.MagicMock()
        reply.__enter__.return_value = reply
        reply.headers = email.message.Message()
        if header is not None:
            reply.headers["x-oauth-scopes"] = header
        reply.read.return_value = json.dumps(body).encode()
        return mock.patch("urllib.request.urlopen", return_value=reply)

    def test_the_header_is_the_set(self):
        with self.answer({"ok": True}, "chat:write, commands,pins:write"):
            self.assertEqual(slack.Slack("xoxb-t").granted_scopes(), {"chat:write", "commands", "pins:write"})

    def test_a_refusal_or_no_header_is_an_error(self):
        with self.answer({"ok": False, "error": "invalid_auth"}, "x"), self.assertRaises(slack.Error):
            slack.Slack("xoxb-t").granted_scopes()
        with self.answer({"ok": True}, None), self.assertRaises(slack.Error):
            slack.Slack("xoxb-t").granted_scopes()


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
               "chat.getPermalink": {"ok": True, "permalink": "https://x/p1"}, "chat.update": {"ok": True},
               "conversations.list": {"ok": True, "channels": [{"name": "alerts", "id": "C42"}]},
               "auth.test": {"ok": True, "bot_id": "B1"}, "bots.info": {"ok": True, "bot": {"app_id": "A1"}},
               "conversations.history": {"ok": True, "messages": []}}  # fmt: skip

    def posts(self, client):
        return [(p["channel"], p["text"]) for m, p in client.calls if m == "chat.postMessage"]

    def test_a_message_to_a_person_is_refused_outside_dm(self):
        client = Fake(self.ANSWERS)
        with self.assertRaisesRegex(slack.Error, "a message to a person is a dm"):
            client.post("U1ABC", "x")
        self.assertEqual(client.calls, [])

    def test_dry_run_prints_and_posts_nothing(self):
        client = slack.Slack("", dry_run=True)
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("urllib.request.urlopen") as urlopen:
            self.assertEqual(client.post("#c", "hello", thread_ts="1.0"), "dry-run")
        urlopen.assert_not_called()
        self.assertIn("hello", out.getvalue())

    def test_a_refusal_names_the_method_and_reason(self):
        reply = io.BytesIO(json.dumps({"ok": False, "error": "channel_not_found"}).encode())
        with mock.patch("urllib.request.urlopen", return_value=reply):
            with self.assertRaisesRegex(slack.Error, "chat.postMessage failed: channel_not_found"):
                slack.Slack("xoxb-test").post("#nope", "x", thread_ts="1.0")

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
            self.assertEqual(slack.Slack("xoxb-test").post("#c", "x", thread_ts="1.0"), "1.2")
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
                    slack.Slack("xoxb-test").post("#c", "x", thread_ts="1.0")

    def test_an_answer_that_is_not_an_object_fails(self):
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(b"[1]")):
            with self.assertRaisesRegex(slack.Error, "not a JSON object"):
                slack.Slack("xoxb-test").post("#c", "x", thread_ts="1.0")


class Sender(slack.Slack):
    """One sender (the bot, a usage report) on the Slack of CHANNEL (a FakeChannel), which
    every sender of a test shares: dm() and the DM log are the real ones."""

    def __init__(self, channel, log="#alerts"):
        super().__init__("xoxb-test", dm_log=slack.DmLog(log) if log else None)
        self.ch, self.stale = channel, 0

    def call(self, method, **params):
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D" + params["users"]}}
        if method == "conversations.history" and self.stale:
            self.stale -= 1  # a read from before another sender's post
            return {"ok": True, "messages": []}
        return self.ch.call(method, **params)

    def _send(self, channel, msg, thread_ts=None, metadata=None):
        return self.ch.wire(channel, msg["text"], msg["blocks"], thread_ts, metadata)

    def update(self, channel, ts, text, blocks=None, metadata=None):
        return self.ch.update(channel, ts, text, blocks, metadata)


class DmLogTests(unittest.TestCase):
    """Each DM is logged as the person got it, under the day's one message, whose count is
    every sender's DMs: read from Slack, never from a sender's own file."""

    def setUp(self):
        self.ch = FakeChannel()
        self.ch.add("[FIRING:1] DiskFull", "#alerts", bot=("BX", "AX"))  # the channel's alerts
        self.out = mock.patch("sys.stdout", io.StringIO())
        self.out.start()
        self.addCleanup(self.out.stop)

    def days(self):
        return [
            m for m in self.ch.posts("CALERTS") if (m.get("metadata") or {}).get("event_type") == slack.KEPT
        ]

    def test_the_dm_is_logged_whole_under_a_line_naming_it(self):
        text = "Your disk use is high.\n" + "y" * 500 + "\nMore."
        channel, ts = Sender(self.ch).dm("U1", text, kind="disk", login="kim")
        sent = self.ch.at(ts)
        [day] = self.days()
        [reply] = self.ch.thread(day["ts"])
        head = rich.blocks(
            slack.dm_log_line("disk", "kim", f"https://slack.example/archives/{channel}/p{ts}")
        )
        self.assertEqual(reply["blocks"], head + sent["blocks"], "the DM's own blocks, nothing cut")
        self.assertIn("y" * 500, rich.read(reply["blocks"]))
        self.assertEqual(day["text"], slack.dm_log_title(time.strftime("%Y-%m-%d"), 1))

    def test_a_dm_past_slacks_limits_is_split_in_order(self):
        blocks = [rich.section(f"line {i}") for i in range(70)]
        Sender(self.ch).dm("U1", "many", blocks, kind="io", login="kim")
        [day] = self.days()
        parts = self.ch.thread(day["ts"])
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(len(p["blocks"]) <= slack.MAX_BLOCKS for p in parts))
        self.assertEqual([b for p in parts for b in p["blocks"]][-70:], blocks)
        self.assertEqual([p["metadata"]["event_payload"]["part"] for p in parts], [0, 1])
        self.assertEqual(day["text"], slack.dm_log_title(time.strftime("%Y-%m-%d"), 1), "one DM, two replies")

    def test_two_senders_interleaving_share_one_day_message_and_its_count(self):
        bot, report = Sender(self.ch), Sender(self.ch)
        bot.dm("U1", "welcome", kind="welcome", login="kim")
        report.stale = 1  # it looked before the bot's day message was there
        report.dm("U2", "idle GPUs", kind="idle", login="lee")
        bot.dm("U3", "reminder", kind="data-reminder", login="park")
        [day] = self.days()
        self.assertEqual(len(self.ch.thread(day["ts"])), 3)
        self.assertEqual(day["text"], slack.dm_log_title(time.strftime("%Y-%m-%d"), 3))

    def test_a_count_written_over_by_another_sender_is_set_right(self):
        bot, report = Sender(self.ch), Sender(self.ch)
        bot.dm("U1", "first", kind="welcome", login="kim")
        update, raced = self.ch.update, []

        def late(channel, ts, text, blocks=None, metadata=None):
            if not raced:  # the report logs a DM between the bot's count and its edit
                raced.append(True)
                report.dm("U2", "second", kind="idle", login="lee")
            return update(channel, ts, text, blocks, metadata)

        self.ch.update = late
        bot.dm("U3", "third", kind="project", login="park")
        [day] = self.days()
        self.assertEqual(day["text"], slack.dm_log_title(time.strftime("%Y-%m-%d"), 3))

    def test_a_new_day_starts_a_new_message(self):
        log, sender = slack.DmLog("#alerts"), Sender(self.ch)
        for day in (24, 25):
            msg = rich.message("x")
            log.record(
                sender,
                slack.dm_log_entry("io", "kim", msg),
                now=time.mktime((2026, 11, day, 12, 0, 0, 0, 0, -1)),
            )
        self.assertEqual([m["text"] for m in self.days()],
                         [slack.dm_log_title("2026-11-24", 1), slack.dm_log_title("2026-11-25", 1)])  # fmt: skip

    def test_an_unlogged_dm_or_no_log_posts_the_dm_alone(self):
        for sender, log in ((Sender(self.ch), False), (Sender(self.ch, log=""), True)):
            with self.subTest(log=log):
                sender.dm("U1", "Job 42 ended.", kind="job", login="kim", log=log)
                self.assertEqual(self.days(), [])

    def test_a_failed_log_never_fails_the_sent_dm(self):
        sender = Sender(FakeChannel(no_history=True))
        sender.ch.add("x", "#alerts", bot=("BX", "AX"))
        with mock.patch("sys.stderr", io.StringIO()) as err:
            channel, ts = sender.dm("U1", "x", kind="io", login="kim")
        self.assertEqual(sender.ch.at(ts)["channel"], "DU1")
        self.assertIn("the DM to kim (io) was sent; logging it failed", err.getvalue())


class DmCountTests(unittest.TestCase):
    """Each DM counts as sent or failed, a failed log as log_failed, per sender and kind,
    in the sender's textfile (cluster_tools.files.count_in_textfile: read back with
    prom.parse, written atomically): what SlackDmFailing and SlackDmLogFailing read."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = str(Path(d.name) / "slack-dm-cluster-bot.prom")
        self.out = mock.patch("sys.stdout", io.StringIO())
        self.out.start()
        self.addCleanup(self.out.stop)

    def counts(self):
        from cluster_tools import prom

        return {(dict(k[1])["kind"], dict(k[1])["result"]): v for k, v in prom.parse(Path(self.path).read_text()).items()
                if k[0] == "slack_dm_messages_total"}  # fmt: skip

    def test_sent_failed_and_log_failed_add_up_across_senders_runs(self):
        ch = FakeChannel()
        ch.add("x", "#alerts", bot=("BX", "AX"))
        for _ in range(2):  # two runs of one sender, each its own client
            sender = Sender(ch)
            sender.counts = slack.DmCounts(self.path, "cluster-bot")
            sender.dm("U1", "hi", kind="welcome", login="kim")
        broken = Sender(FakeChannel(no_history=True))
        broken.ch.add("x", "#alerts", bot=("BX", "AX"))
        broken.counts = slack.DmCounts(self.path, "cluster-bot")
        with mock.patch("sys.stderr", io.StringIO()):
            broken.dm("U2", "hi", kind="welcome", login="lee")
        failing = Sender(ch)
        failing.counts = slack.DmCounts(self.path, "cluster-bot")

        def refuse(channel, msg, thread_ts=None, metadata=None):
            raise slack.Error("Slack chat.postMessage failed: channel_not_found")

        failing._send = refuse
        with self.assertRaises(slack.Error):
            failing.dm("U3", "hi", kind="idle", login="park")
        self.assertEqual(
            self.counts(), {("welcome", "sent"): "3", ("welcome", "log_failed"): "1", ("idle", "failed"): "1"}
        )
        self.assertIn('sender="cluster-bot"', Path(self.path).read_text())

    def test_a_dry_run_counts_nothing_and_a_run_is_stamped(self):
        client = slack.Slack("", dry_run=True, counts=slack.DmCounts(self.path, "slurm-usage-report"))
        with mock.patch("urllib.request.urlopen"):
            client.dm("U1", "x", kind="io", login="kim")
        self.assertFalse(Path(self.path).exists())
        client.counts.ran(1700000000)
        self.assertIn(
            'slack_dm_last_success_timestamp_seconds{sender="slurm-usage-report"} 1700000000',
            Path(self.path).read_text(),
        )

    def test_counts_that_cannot_be_written_never_stop_the_dm(self):
        ch = FakeChannel()
        ch.add("x", "#alerts", bot=("BX", "AX"))
        sender = Sender(ch)
        sender.counts = slack.DmCounts("/nonexistent-dir/x.prom", "cluster-bot")
        with mock.patch("sys.stderr", io.StringIO()) as err:
            sender.dm("U1", "hi", kind="welcome", login="kim")
        self.assertIn("the DM counts were not written", err.getvalue())


class BlockTests(unittest.TestCase):
    def test_a_long_text_is_split_at_lines_and_nothing_is_lost(self):
        # Every line reaches Slack: a dropped one could be the mention that tells an owner.
        rows = [f"row {i:04d} " + "x" * 40 for i in range(200)]
        text = "head\n```\n" + "\n".join(rows) + "\n```"
        parts = [rich.read([b]) for b in slack.sections(text)]
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p), 3000)
        lines = [ln for p in parts for ln in p.splitlines() if ln.startswith("row ")]
        self.assertEqual(lines, rows)
        self.assertTrue(
            all(
                e["type"] == "rich_text_preformatted" for b in slack.sections(text)[1:] for e in b["elements"]
            ),
            "a table split in two is a code block in each part",
        )
        self.assertEqual([rich.read([b]) for b in slack.sections("short")], ["short"])
        long = slack.sections("y" * 7000)
        self.assertTrue(all(len(rich.read([b])) <= 3000 for b in long))
        self.assertEqual(
            sum(len(rich.read([b]).replace("\n", "")) for b in long), 7000, "a long line is split, not cut"
        )

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
