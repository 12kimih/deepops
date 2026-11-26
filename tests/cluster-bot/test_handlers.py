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
sys.path.insert(0, str(ROOT / "tests/lib"))
from fake_slack import FakeDM  # noqa: E402


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
from cluster_tools import rich  # noqa: E402
from cluster_bot import app  # noqa: E402
from cluster_tools import job_notice  # noqa: E402
from cluster_tools.slack import MEMBER  # noqa: E402


class DMs(FakeDM):
    """The bot's DM sender: each DM as (member, text, blocks, kind, login, log)."""

    def __init__(self):
        self.sent = []

    def dm(self, member, text, blocks=None, *, kind, login, log=True):
        self.sent.append((member, text, blocks, kind, login, log))
        return super().dm(member, text, blocks, kind=kind, login=login, log=log)

    def post(self, channel, text, blocks=None):
        return "1.0"

    def to(self, member):
        return [s for s in self.sent if s[0] == member]


CFG = {"bot_token": "x", "command": "/cluster", "members": {"UALICE": "alice", "UBOSS": "boss"}, "admins": ["UBOSS"],
       "channels": {"help": "#help", "admin": "#admin"}, "admin_group": "@admins",
       "admin_mention": "<!subteam^SADMINS>", "connect": {"login_host": "login.example.org", "ssh_port": 2222},
       "dm_log": {"channel": "#admin", "state": "/nonexistent/dm-log.json"}}  # fmt: skip


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
        self.dms = DMs()
        self.app = app.build(CFG, self.engine, self.join, unban=mock.Mock(), dms=self.dms)
        self.addCleanup(self.no_dm_bypasses_the_sender)

    def no_dm_bypasses_the_sender(self):
        """The bot's own client posts to channels only: every DM is cluster_tools.slack's
        dm(), which logs the notices for the administrators."""
        to = [c.kwargs["channel"] for c in self.client.chat_postMessage.call_args_list]
        self.assertEqual([c for c in to if MEMBER.fullmatch(c)], [], "a DM sent around Slack.dm()")

    def call(self, kind, name, **kw):
        """As Bolt calls a listener: only the arguments it declares."""
        fn = self.app.handlers[(kind, name)]
        offered = {"ack": mock.Mock(), "client": self.client, **kw}
        fn(**{k: v for k, v in offered.items() if k in inspect.signature(fn).parameters})
        return offered["ack"]

    def test_join_opens_its_form_and_a_submission_becomes_a_join_request(self):
        self.call(
            "command",
            "/cluster",
            body={"user_id": "UNEW", "channel_id": "C", "trigger_id": "T", "text": "join"},
        )
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
            "command",
            "/cluster",
            body={"user_id": "UALICE", "channel_id": "C", "trigger_id": "T", "text": "unban"},
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
            admin["blocks"][0]["elements"][0]["elements"],
            [{"type": "usergroup", "usergroup_id": "SADMINS"}],
            "the blocks notify, not the text",
        )
        self.assertNotIn("SADMINS", posts["#help"]["text"])

    def test_the_shared_datas_cleanup_tells_the_requester_by_a_logged_dm(self):
        cfg = {
            **CFG,
            "data": {
                "unused_days": 60,
                "grace_days": 14,
                "seen": os.path.join(os.path.dirname(self.prefs), "seen.json"),
            },
        }

        def watch(data, seen, now, unused, grace, say):
            say("notice", "datasets/coco", {"requester": "alice"}, removal=now)
            say("removed", "datasets/coco", {"requester": "alice"})
            say("done", "datasets/coco", {"requester": "alice"})

        shared = mock.Mock(root="/home/shared")
        with mock.patch.object(app, "data_watch", watch):
            app.data_rounds(self.client, self.dms, shared, cfg, {"alice": "UALICE"}.get)()
        self.assertEqual([(k, who, log) for *_, k, who, log in self.dms.to("UALICE")],
                         [("data-unused", "alice", True), ("data-removed", "alice", True), ("data-done", "alice", True)])  # fmt: skip

    def test_data_opens_its_form_and_checks_it_before_a_request(self):
        shared = mock.Mock(kinds=["datasets", "models"], root="/home/shared")
        built = app.build(CFG, self.engine, self.join, unban=mock.Mock(), data=shared, dms=self.dms)
        self.app = built
        self.call(
            "command",
            "/cluster",
            body={"user_id": "UALICE", "channel_id": "C", "trigger_id": "T", "text": "data"},
        )
        self.assertEqual(self.client.views_open.call_args.kwargs["view"]["callback_id"], "data.submit")
        state = {"category": {"v": {"selected_option": {"value": "datasets"}}}, "name": {"v": {"value": "coco"}},
                 "url": {"v": {"value": "http://insecure.example/coco.tar"}}, "size": {"v": {"value": "20G"}},
                 "sha256": {"v": {"value": None}}, "purpose": {"v": {"value": "detection"}}}  # fmt: skip
        body, view = submitted(state, "UALICE", "data.submit")
        ack = self.call("view", "data.submit", body=body, view=view)
        self.assertEqual(ack.call_args.kwargs["errors"].keys(), {"url"})
        self.engine.submit.assert_not_called()
        state["url"] = {"v": {"value": " https://data.example/coco.tar "}}
        self.call("view", "data.submit", body=body, view=view)
        req = self.engine.submit.call_args.args[0]
        self.assertEqual((req["kind"], req["user"], req["category"], req["url"], req["size"]),
                         ("data", "alice", "datasets", "https://data.example/coco.tar", 20 << 30))  # fmt: skip
        # A notice's button records a use by whoever pressed it.
        self.call("action", "data.use", body={"user": {"id": "UALICE"}}, action={"value": "datasets/coco"})
        shared.run.assert_called_with("use", "datasets", "coco", "alice")

    def test_unban_needs_a_person_of_the_roster(self):
        self.call(
            "command",
            "/cluster",
            body={"user_id": "USTRANGER", "channel_id": "C", "trigger_id": "T", "text": "unban"},
        )
        self.client.views_open.assert_not_called()
        self.client.chat_postEphemeral.assert_called_once()

    def test_someone_with_an_account_cannot_join_again(self):
        self.call(
            "command",
            "/cluster",
            body={"user_id": "UALICE", "channel_id": "C", "trigger_id": "T", "text": "join"},
        )
        self.client.views_open.assert_not_called()
        reply = self.client.chat_postEphemeral.call_args.kwargs["text"]
        self.assertIn("ssh -p 2222 alice@login.example.org", reply, "and how to log in, from the settings")

    def test_a_join_request_is_answered_with_how_to_log_in_once_the_account_exists(self):
        state = {"login": {"v": {"value": "carl"}}, "name": {"v": {"value": "Carl"}},
                 "team": {"v": {"selected_option": {"value": "team-a"}}}, "key": {"v": {"value": "ssh-ed25519 AAA"}}}  # fmt: skip
        body, view = submitted(state, "UNEW", "join.submit")
        self.call("view", "join.submit", body=body, view=view)
        dms = self.dms.to("UNEW")
        self.assertTrue(any("/cluster join" in t and "login.example.org" in t for _, t, *_ in dms))
        self.assertFalse(any(log for *_, log in dms), "the answer to one's own request is not logged")
        self.dms.sent.clear()
        self.engine.submit.return_value = mock.Mock(status="refused", text="no", request={})
        self.call("view", "join.submit", body=body, view=view)
        self.assertFalse(
            any("login.example.org" in t for _, t, *_ in self.dms.to("UNEW")), "not after a refusal"
        )

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

    def test_a_decision_reaches_the_asker_as_a_logged_notice_the_answer_to_asking_not(self):
        self.engine.submit.return_value = mock.Mock(status="held", text="held", key="k1", request={})
        self.engine.approvers.return_value = ["boss"]  # held for a project's owner: buttons by DM
        self.client.chat_postMessage.return_value = {"ts": "1.0", "channel": "C"}
        body, view = submitted({"ip": {"v": {"value": "192.0.2.1"}}, "reason": {"v": {"value": None}}},
                               "UALICE", "unban.submit")  # fmt: skip
        self.call("view", "unban.submit", body=body, view=view)
        self.assertEqual(
            [(k, who, log) for _, _, _, k, who, log in self.dms.to("UBOSS")], [("approval", "boss", True)]
        )
        self.assertEqual([(k, log) for *_, k, _, log in self.dms.to("UALICE")], [("request", False)])
        self.dms.sent.clear()
        self.engine.approve.return_value = mock.Mock(text="done", request={"slack_id": "UALICE"})
        body = {"user": {"id": "UBOSS"}, "channel": {"id": "C"}, "message": {"ts": "1"}}
        self.call(
            "action", "request.approve", body=body, action={"action_id": "request.approve", "value": "k1"}
        )
        self.assertEqual([(t, k, who, log) for _, t, _, k, who, log in self.dms.to("UALICE")],
                         [("done", "request", "alice", True)])  # fmt: skip

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
        text = rich.read(view["blocks"])
        self.assertIn("#help", text)
        self.assertIn("스레드", text)
        self.assertIn("@admins 잡이", text, "a ready-made example that tags the group")


if __name__ == "__main__":
    unittest.main()
