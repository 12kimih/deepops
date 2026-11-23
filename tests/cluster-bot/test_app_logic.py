"""The bot's Slack-free parts: reminders go once per stage and start over when the deadline moves, and state files
are replaced whole."""

import datetime
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROLE = Path(__file__).resolve().parents[2] / "roles/cluster_bot"
sys.path.insert(0, str(ROLE / "files"))
from cluster_bot import accounts, accounts_blocks, app, reminders, state  # noqa: E402

DAY = 86400


class Client:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def chat_postMessage(self, **kw):
        if self.fail:
            raise RuntimeError("Slack is down")
        self.sent.append(kw)


def deadlines(items):
    """A feature's deadlines over ITEMS (a list of {user, ends, live}): {user: (ends, user,
    item)} for the live ones, as a feature's deadlines() gives them."""
    return lambda: {i["user"]: (i["ends"], i["user"], i) for i in items if i.get("live", True)}


def message(item, left):
    return "곧 기한입니다", []


SLACK_OF = {"alice": "UALICE"}.get


class LogicTests(unittest.TestCase):
    def test_reminders_once_per_stage_and_again_after_an_extension(self):
        items = [{"user": "alice", "ends": 10 * DAY}]
        sources = {"notice": reminders.Source([7, 1], deadlines(items), message)}
        client, sent = Client(), {}
        for now in (
            2,
            4,
            5,
            9.5,
        ):  # 8 days left: nothing; 6: the 7-day one; 5: same stage; 0.5: the 1-day one
            reminders.remind(sources, client, SLACK_OF, sent, now * DAY)
        self.assertEqual(
            [(m["channel"], m["text"]) for m in client.sent],
            [("UALICE", "곧 기한입니다")] * 2,
        )
        items[0]["ends"] = 13 * DAY  # moved by three days, inside the 7-day window
        reminders.remind(sources, client, SLACK_OF, sent, 9.6 * DAY)  # 3.4 days left: they just chose it
        self.assertEqual(len(client.sent), 2, "no 7-day reminder right after extending into it")
        reminders.remind(sources, client, SLACK_OF, sent, 12.5 * DAY)  # the 1-day one
        self.assertEqual(len(client.sent), 3, "a moved deadline starts its countdown over")
        items[0]["live"] = False
        reminders.remind(sources, client, SLACK_OF, sent, 12.6 * DAY)
        self.assertEqual(sent, {"notice": {}}, "what is no longer listed leaves the record")

    def test_a_stage_already_passed_when_first_seen_is_skipped_but_never_the_last(self):
        sent = {}
        self.assertEqual(
            reminders.due({"k": 10 * DAY}, [14, 7, 1], sent, 0), [], "10 days off: not the 14-day one"
        )
        self.assertEqual(reminders.due({"k": 10 * DAY}, [14, 7, 1], sent, 3.5 * DAY), [("k", 7, 7)])
        seen = {}
        self.assertEqual(reminders.due({"k": 30 * DAY}, [14, 7, 1], seen, 0), [], "seen 30 days off")
        self.assertEqual(reminders.due({"k": 30 * DAY}, [14, 7, 1], seen, 16.5 * DAY), [("k", 14, 14)])
        old = {"k": [10 * DAY, None]}  # a record kept from before first sight: every stage counts
        self.assertEqual(reminders.due({"k": 10 * DAY}, [14, 7, 1], old, 0), [("k", 10, 14)])

    def test_after_a_pause_only_the_nearest_stage_and_a_failed_message_again(self):
        due = reminders.due({"k": 10 * DAY}, [30, 14, 7, 1], {}, 9.5 * DAY)
        self.assertEqual(due, [("k", 1, 1)], "a bot that was down sends one reminder, not four")
        sources = {
            "notice": reminders.Source([7, 1], deadlines([{"user": "alice", "ends": 10 * DAY}]), message)
        }
        sent = {}
        reminders.remind(sources, Client(), SLACK_OF, sent, 0)  # first seen ten days off
        reminders.remind(sources, Client(fail=True), SLACK_OF, sent, 4 * DAY)
        self.assertIsNone(sent["notice"]["alice"][1], "nothing went out, so no stage is recorded")
        client = Client()
        reminders.remind(sources, client, SLACK_OF, sent, 4.1 * DAY)
        self.assertEqual(len(client.sent), 1)

    def test_one_failing_source_does_not_stop_the_others(self):
        def broken():
            raise RuntimeError("the server is away")

        sources = {
            "notice": reminders.Source([7, 1], broken, message),
            "leave": reminders.Source(
                [7], lambda: {"alice": (5 * DAY, "alice", {})}, lambda item, left: ("t", [])
            ),
        }
        client = Client()
        reminders.remind(sources, client, SLACK_OF, {}, 0)
        self.assertEqual(len(client.sent), 1)

    def test_leave_reminders_count_to_the_end_of_the_last_day_and_say_both_dates(self):
        dates = {"ends": "2027-02-28", "login_expires": "2027-03-30"}
        deadline, user, item = accounts.leaving_deadlines({"alice": dates})["alice"]
        self.assertEqual((user, item), ("alice", {"user": "alice", **dates}))
        self.assertEqual(datetime.datetime.fromtimestamp(deadline), datetime.datetime(2027, 3, 1))
        sources = app.reminder_sources(
            {"leave": {"remind_days": [30, 14, 7, 1], "leaving": {"alice": dates}}}
        )
        client, sent = Client(), {}
        day_before = time.mktime(datetime.datetime(2027, 2, 28, 9).timetuple())
        reminders.remind(sources, client, SLACK_OF, sent, day_before)
        text = client.sent[0]["blocks"][0]["text"]["text"]
        self.assertIn("내일이", text)
        self.assertIn("2027-02-28", text)
        self.assertIn("2027-03-30부터는 로그인이 막힙니다", text)
        self.assertEqual(accounts_blocks.leave_reminder(item, 14)[0], "클러스터 사용 종료 안내")

    def test_state_is_replaced_whole_with_its_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "state.json")
            self.assertEqual(state.read(path, {}), {})
            state.write(path, {"a": 1})
            self.assertEqual((state.read(path, {}), os.stat(path).st_mode & 0o777), ({"a": 1}, 0o600))
            state.write(path, {"a": 2}, mode=0o644)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
            self.assertEqual(
                sorted(os.listdir(os.path.dirname(path))), ["state.json"], "no temporary file left"
            )
            Path(path).write_text("{half")
            self.assertEqual(state.read(path, {"default": True}), {"default": True})


if __name__ == "__main__":
    unittest.main()
