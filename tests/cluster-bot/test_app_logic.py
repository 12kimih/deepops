"""The bot's Slack-free parts: the forms and messages carry what their handlers read,
reminders go once per stage and start over when the deadline moves, and state files
are replaced whole."""

import datetime
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROLE = Path(__file__).resolve().parents[2] / "roles/cluster_bot"
sys.path.insert(0, str(ROLE / "files"))
from clusterbot import accounts, accounts_blocks, app, reminders, scratch, scratch_blocks, state  # noqa: E402

DAY = 86400
ALLOC = {"id": 1000, "user": "alice", "bytes": 1024**4, "state": "active", "path": "/scratch/alice-1000",
         "expires": 10 * DAY, "extensions": 0}  # fmt: skip


class Client:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def chat_postMessage(self, **kw):
        if self.fail:
            raise RuntimeError("Slack is down")
        self.sent.append(kw)


def feature(allocs):
    return scratch.ScratchFeature(lambda *words: allocs, {})


SLACK_OF = {"alice": "UALICE"}.get


class LogicTests(unittest.TestCase):
    def test_home_offers_actions_per_allocation_and_a_new_request(self):
        blocks = scratch_blocks.home([ALLOC])
        actions = [e["action_id"] for b in blocks if b["type"] == "actions" for e in b["elements"]]
        self.assertEqual(actions, ["scratch.extend", "scratch.resize", "scratch.release", "scratch.new"])
        release = blocks[2]["elements"][2]
        self.assertIn("confirm", release, "giving space back asks first")

    def test_forms_name_the_fields_their_handlers_read(self):
        form = scratch_blocks.new_form({"auto_bytes": 2 * 1024**4, "auto_days": 30, "auto_extensions": 2})
        self.assertEqual([b.get("block_id") for b in form["blocks"][1:]], ["size", "days", "reason"])
        self.assertEqual(scratch_blocks.number_form("extend", 1000)["private_metadata"], "1000")
        self.assertEqual(app.size_bytes("2T"), 2 * 1024**4)

    def test_reminders_once_per_stage_and_again_after_an_extension(self):
        allocs = [dict(ALLOC)]
        sources = {"scratch": reminders.Source([7, 1], feature(allocs).deadlines, scratch_blocks.reminder)}
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
            [("UALICE", "스크래치 공간이 곧 만료됩니다")] * 2,
        )
        allocs[0]["expires"] = 13 * DAY  # extended by three days, inside the 7-day window
        reminders.remind(sources, client, SLACK_OF, sent, 9.6 * DAY)  # 3.4 days left: the 7-day stage again
        reminders.remind(sources, client, SLACK_OF, sent, 12.5 * DAY)  # and the 1-day one
        self.assertEqual(len(client.sent), 4, "a moved deadline starts its countdown over")
        allocs[0]["state"] = "frozen"
        reminders.remind(sources, client, SLACK_OF, sent, 12.6 * DAY)
        self.assertEqual(sent, {"scratch": {}}, "what is no longer listed leaves the record")

    def test_after_a_pause_only_the_nearest_stage_and_a_failed_message_again(self):
        due = reminders.due({"k": 10 * DAY}, [30, 14, 7, 1], {}, 9.5 * DAY)
        self.assertEqual(due, [("k", 1, 1)], "a bot that was down sends one reminder, not four")
        sources = {
            "scratch": reminders.Source([7, 1], feature([dict(ALLOC)]).deadlines, scratch_blocks.reminder)
        }
        sent = {}
        reminders.remind(sources, Client(fail=True), SLACK_OF, sent, 4 * DAY)
        self.assertEqual(sent, {"scratch": {}}, "nothing went out, so nothing is recorded")
        client = Client()
        reminders.remind(sources, client, SLACK_OF, sent, 4.1 * DAY)
        self.assertEqual(len(client.sent), 1)

    def test_one_failing_source_does_not_stop_the_others(self):
        def broken():
            raise RuntimeError("the scratch server is away")

        sources = {
            "scratch": reminders.Source([7, 1], broken, scratch_blocks.reminder),
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
            {"leave": {"remind_days": [30, 14, 7, 1], "leaving": {"alice": dates}}}, None
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

    def test_the_manifest_declares_every_command_the_app_handles(self):
        import yaml

        manifest = yaml.safe_load((ROLE / "files/slack-app-manifest.yml").read_text())
        declared = {c["command"] for c in manifest["features"]["slash_commands"]}
        handled = set(
            re.findall(r'@app\.command\("([^"]+)"\)', (ROLE / "files/clusterbot/app.py").read_text())
        )
        self.assertEqual(declared, handled)
        self.assertTrue(manifest["settings"]["socket_mode_enabled"])


if __name__ == "__main__":
    unittest.main()
