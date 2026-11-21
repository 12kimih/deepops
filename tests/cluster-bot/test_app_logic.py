"""The bot's Slack-free parts: the forms and messages carry what their handlers read,
and expiry reminders go once per stage, again after an extension."""

import re
import sys
import unittest
from pathlib import Path

ROLE = Path(__file__).resolve().parents[2] / "roles/cluster_bot"
sys.path.insert(0, str(ROLE / "files"))
from clusterbot import app, scratch_blocks  # noqa: E402

DAY = 86400
ALLOC = {"id": 1000, "user": "alice", "bytes": 1024**4, "state": "active", "path": "/scratch/alice-1000",
         "expires": 10 * DAY, "extensions": 0}  # fmt: skip


class Client:
    def __init__(self):
        self.sent = []

    def chat_postMessage(self, **kw):
        self.sent.append(kw)


class Service:
    def __init__(self, allocs):
        self.allocs = allocs

    def run(self, *words):
        return self.allocs


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
        cfg = {"members": {"UALICE": "alice"}, "scratch": {"remind_days": [7, 1]}}
        client, state, svc = Client(), {}, Service([dict(ALLOC)])
        app.remind_once(svc, client, cfg, state, 2 * DAY)  # 8 days left: nothing yet
        app.remind_once(svc, client, cfg, state, 4 * DAY)  # 6 days left: the 7-day reminder
        app.remind_once(svc, client, cfg, state, 5 * DAY)  # still the same stage: nothing
        app.remind_once(svc, client, cfg, state, 9.5 * DAY)  # the 1-day reminder
        self.assertEqual(len(client.sent), 2)
        self.assertEqual(client.sent[0]["channel"], "UALICE")
        svc.allocs[0]["expires"] = 30 * DAY  # extended
        app.remind_once(svc, client, cfg, state, 10 * DAY)
        app.remind_once(svc, client, cfg, state, 24 * DAY)
        self.assertEqual(len(client.sent), 3)

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
