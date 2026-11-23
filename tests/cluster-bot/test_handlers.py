"""cluster_bot.app's Slack handlers, driven with Slack's payload shapes through a stand-in
for slack_bolt: each command opens its form, each form submission becomes the request the
engine receives, and only administrators may decide or act for others."""

import inspect
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))


class FakeApp:
    def __init__(self, token):
        self.handlers = {}

    def _register(self, kind, name):
        def deco(fn):
            self.handlers[(kind, name)] = fn
            return fn

        return deco

    def command(self, name):
        return self._register("command", name)

    def action(self, name):
        return self._register("action", name)

    def view(self, name):
        return self._register("view", name)

    def event(self, name):
        return self._register("event", name)


sys.modules["slack_bolt"] = types.SimpleNamespace(App=FakeApp)
sys.modules.setdefault("slack_sdk", types.ModuleType("slack_sdk"))
sys.modules.setdefault(
    "slack_sdk.errors", types.SimpleNamespace(SlackApiError=type("SlackApiError", (Exception,), {}))
)
from cluster_bot import app  # noqa: E402
from cluster_tools import job_notice  # noqa: E402

CFG = {"bot_token": "x", "members": {"UALICE": "alice", "UBOSS": "boss"}, "admins": ["UBOSS"],
       "channels": {"help": "#help", "admin": "#admin"}, "admin_group": "@admins",
       "admin_mention": "<!subteam^SADMINS>"}  # fmt: skip


def submitted(state, user, callback, metadata=""):
    return {"user": {"id": user}}, {
        "state": {"values": state},
        "callback_id": callback,
        "private_metadata": metadata,
    }


class Handlers(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.prefs = os.path.join(tmp.name, "prefs.json")
        CFG["prefs_file"] = self.prefs
        self.engine = mock.Mock(features={"join": object()})
        self.engine.approvers.return_value = []  # held for the administrators
        self.engine.submit.return_value = mock.Mock(status="done", text="ok", request={})
        self.join = mock.Mock(teams=mock.Mock(return_value=["team-a"]))
        self.client = mock.MagicMock()
        self.app = app.build(CFG, self.engine, self.join, unban=mock.Mock())

    def call(self, kind, name, **kw):
        """As Bolt calls a listener: only the arguments it declares."""
        fn = self.app.handlers[(kind, name)]
        offered = {"ack": mock.Mock(), "client": self.client, **kw}
        fn(**{k: v for k, v in offered.items() if k in inspect.signature(fn).parameters})
        return offered["ack"]

    def test_join_opens_its_form_and_a_submission_becomes_a_join_request(self):
        self.call("command", "/join", body={"user_id": "UNEW", "channel_id": "C", "trigger_id": "T"})
        self.assertEqual(self.client.views_open.call_args.kwargs["view"]["callback_id"], "join.submit")
        state = {"login": {"v": {"value": " carl "}}, "name": {"v": {"value": "Carl"}},
                 "team": {"v": {"selected_option": {"value": "team-a"}}}, "key": {"v": {"value": "ssh-ed25519 AAA"}}}  # fmt: skip
        body, view = submitted(state, "UNEW", "join.submit")
        self.call("view", "join.submit", body=body, view=view)
        req = self.engine.submit.call_args.args[0]
        self.assertEqual(
            (req["kind"], req["user"], req["account"], req["slack"]), ("join", "carl", "team-a", "UNEW")
        )

    def test_unban_checks_the_address_in_the_form_and_submits_it_written_one_way(self):
        self.call(
            "command", "/unban-request", body={"user_id": "UALICE", "channel_id": "C", "trigger_id": "T"}
        )
        self.assertEqual(self.client.views_open.call_args.kwargs["view"]["callback_id"], "unban.submit")
        body, view = submitted({"ip": {"v": {"value": "not-an-ip"}}, "reason": {"v": {"value": None}}},
                               "UALICE", "unban.submit")  # fmt: skip
        ack = self.call("view", "unban.submit", body=body, view=view)
        self.assertEqual(ack.call_args.kwargs["response_action"], "errors", "the form says what is wrong")
        self.engine.submit.assert_not_called()
        body, view = submitted({"ip": {"v": {"value": " 2001:DB8::1 "}}, "reason": {"v": {"value": "typo"}}},
                               "UALICE", "unban.submit")  # fmt: skip
        self.call("view", "unban.submit", body=body, view=view)
        req = self.engine.submit.call_args.args[0]
        self.assertEqual(
            (req["kind"], req["user"], req["ip"], req["reason"]), ("unban", "alice", "2001:db8::1", "typo")
        )

    def test_a_request_held_for_the_administrators_mentions_them_there_only(self):
        self.engine.submit.return_value = mock.Mock(status="held", text="held", key="k1", request={})
        self.client.chat_postMessage.return_value = {"ts": "1.0", "channel": "C"}
        body, view = submitted({"ip": {"v": {"value": "192.0.2.1"}}, "reason": {"v": {"value": None}}},
                               "UALICE", "unban.submit")  # fmt: skip
        self.call("view", "unban.submit", body=body, view=view)
        posts = {c.kwargs["channel"]: c.kwargs for c in self.client.chat_postMessage.call_args_list}
        admin = posts["#admin"]
        self.assertTrue(admin["text"].startswith("<!subteam^SADMINS> "))
        self.assertEqual(
            admin["blocks"][0]["text"]["text"], "<!subteam^SADMINS>", "the blocks notify, not the text"
        )
        self.assertNotIn("SADMINS", posts["#help"]["text"])

    def test_ban_watch_notices_mention_the_administrators_the_pinned_list_not(self):
        client = mock.MagicMock()
        client.chat_postMessage.return_value = {"ts": "1.0", "channel": "C"}
        post, _ = app.admin_messages(client, "#admin", "<!subteam^SADMINS>")
        post("new ban", [{"type": "section", "text": {"type": "mrkdwn", "text": "new ban"}}])
        post("the list", [{"type": "section", "text": {"type": "mrkdwn", "text": "the list"}}], pin=True)
        notice, pinned = (c.kwargs for c in client.chat_postMessage.call_args_list)
        self.assertTrue(notice["text"].startswith("<!subteam^SADMINS> "))
        self.assertEqual(notice["blocks"][0]["text"]["text"], "<!subteam^SADMINS>")
        self.assertEqual(pinned["text"], "the list")
        self.assertEqual(len(pinned["blocks"]), 1)

    def test_unban_needs_a_person_of_the_roster(self):
        self.call(
            "command", "/unban-request", body={"user_id": "USTRANGER", "channel_id": "C", "trigger_id": "T"}
        )
        self.client.views_open.assert_not_called()
        self.client.chat_postEphemeral.assert_called_once()

    def test_someone_with_an_account_cannot_join_again(self):
        self.call("command", "/join", body={"user_id": "UALICE", "channel_id": "C", "trigger_id": "T"})
        self.client.views_open.assert_not_called()

    def test_leave_gives_back_only_ones_own_account_unless_admin(self):
        state = {"ends": {"v": {"selected_date": "2027-02-28"}}, "login": {"v": {"value": "boss"}}}
        body, view = submitted(state, "UALICE", "leave.submit", "alice")
        self.call("view", "leave.submit", body=body, view=view)
        self.assertEqual(self.engine.submit.call_args.args[0]["user"], "alice", "not the login typed")
        body, view = submitted(state, "UBOSS", "leave.submit", "boss")
        self.call("view", "leave.submit", body=body, view=view)
        req = self.engine.submit.call_args.args[0]
        self.assertEqual((req["user"], req["ends"]), ("boss", "2027-02-28"))

    def test_only_an_admin_decides_a_held_request(self):
        body = {"user": {"id": "UALICE"}, "channel": {"id": "C"}, "message": {"ts": "1"}}
        self.call(
            "action", "request.approve", body=body, action={"action_id": "request.approve", "value": "k"}
        )
        self.engine.approve.assert_not_called()
        self.engine.approve.return_value = mock.Mock(text="done", request={})
        body["user"]["id"] = "UBOSS"
        self.call(
            "action", "request.approve", body=body, action={"action_id": "request.approve", "value": "k"}
        )
        self.engine.approve.assert_called_once_with("k", "<@UBOSS>")
        # One of the people a request is held for (a project's owner) decides it too.
        self.engine.approve.reset_mock()
        self.engine.approvers.return_value = ["alice"]
        body["user"]["id"] = "UALICE"
        self.call(
            "action", "request.approve", body=body, action={"action_id": "request.approve", "value": "k"}
        )
        self.engine.approve.assert_called_once_with("k", "<@UALICE>")

    def test_the_home_tab_turns_job_notices_off_and_job_notice_obeys(self):
        self.call("event", "app_home_opened", event={"tab": "home", "user": "UALICE"})
        view = self.client.views_publish.call_args.kwargs["view"]
        toggle = next(b for b in view["blocks"] if b.get("accessory"))["accessory"]
        self.assertEqual((toggle["action_id"], toggle["value"]), ("home.notices", "off"))
        self.call("action", "home.notices", body={"user": {"id": "UALICE"}}, action={"value": "off"})
        self.assertEqual(json.loads(Path(self.prefs).read_text()), {"muted": ["alice"]})
        self.assertTrue(
            job_notice.muted({"prefs_file": self.prefs}, "alice"), "the notifier reads the same file"
        )
        self.call("action", "home.notices", body={"user": {"id": "UALICE"}}, action={"value": "on"})
        self.assertFalse(job_notice.muted({"prefs_file": self.prefs}, "alice"))

    def test_the_home_tab_says_how_to_ask_for_help(self):
        self.call("event", "app_home_opened", event={"tab": "home", "user": "UALICE"})
        view = self.client.views_publish.call_args.kwargs["view"]
        text = "\n".join(b["text"]["text"] for b in view["blocks"] if b.get("type") == "section")
        self.assertIn("#help", text)
        self.assertIn("스레드", text)
        self.assertIn("@admins 잡이", text, "a ready-made example that tags the group")


if __name__ == "__main__":
    unittest.main()
