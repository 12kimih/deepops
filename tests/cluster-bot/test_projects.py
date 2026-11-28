"""cluster_bot.projects: collaboration folder requests go through the engine to a pull
request made with the roster's own editors. Owners manage at once, anyone joins with an
owner's approval, a new project or a closing waits for an administrator; whoever's role
changes is told once the change is deployed."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))  # cluster_tools, which the bot imports
sys.path.insert(0, str(ROOT / "tests/lib"))
import in_tree  # noqa: E402
import yaml  # noqa: E402
from cluster_bot import projects, projects_blocks, requests  # noqa: E402
from fake_slack import FakeDM  # noqa: E402

people = in_tree.load("plugins/filter/people.py")

ROSTER = (
    (ROOT / "config.example/people.yml")
    .read_text()
    .replace(
        "people:\n",
        "projects:\n  vision:\n    gid: 50001\n    read_gid: 50002\n    owners: [alice]\n    members: [bob]\n"
        "    purpose: shared eval set\npeople:\n",
        1,
    )
)
SETTINGS = {
    "base": "main",
    "roster": "people.yml",
    "reserved": [22078],
    "gid_min": 50000,
    "reserved_gids": [50003],
}


class FakeRepo:
    def __init__(self):
        self.proposed = []

    def read(self, path, ref):
        return ROSTER, "sha"

    def propose(self, base, branch, files, title, body):
        self.proposed.append((branch, files["people.yml"][0], title, body))
        return f"https://github.example/pr/{len(self.proposed)}"


class Requests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = FakeRepo()
        feature = projects.ProjectFeature(self.repo, people, SETTINGS)
        self.engine = requests.Engine({"project": feature}, os.path.join(tmp.name, "pending.json"))

    def ask(self, user, op, name="vision", **more):
        return self.engine.submit({"kind": "project", "op": op, "user": user, "project": name, **more})

    def roster(self):
        return yaml.safe_load(self.repo.proposed[-1][1])["projects"]

    def test_an_owner_changes_roles_at_once(self):
        out = self.ask("alice", "set", login="dave", role="viewers")
        self.assertEqual(out.status, "done", out.text)
        self.assertEqual(self.roster()["vision"]["viewers"], ["dave"])
        self.assertTrue(self.repo.proposed[-1][0].startswith("project/vision-set-"))
        out = self.ask("alice", "set", login="bob", role="owners")
        self.assertEqual(self.roster()["vision"]["owners"], ["alice", "bob"])

    def test_only_an_owner_manages(self):
        out = self.ask("bob", "set", login="dave", role="members")
        self.assertEqual(out.status, "refused")
        self.assertIn("소유자만", out.text)

    def test_joining_waits_for_an_owner(self):
        out = self.ask("dave", "join", role="members")
        self.assertEqual(out.status, "held")
        self.assertEqual(self.engine.approvers(out.key), ["alice"])
        self.assertIn("alice 중 한 명의 승인", out.text)
        done = self.engine.approve(out.key, "alice")
        self.assertEqual(done.status, "done", done.text)
        self.assertEqual(self.roster()["vision"]["members"], ["bob", "dave"])
        self.assertEqual(self.ask("bob", "join", role="members").status, "refused", "already in it")

    def test_leaving_is_yours_but_the_last_owner_stays(self):
        self.assertEqual(self.ask("bob", "leave").status, "done")
        self.assertNotIn("members", self.roster()["vision"])
        out = self.ask("alice", "leave")
        self.assertEqual(out.status, "refused")
        self.assertIn("only owner", out.text)

    def test_a_new_project_waits_for_an_administrator_and_gets_free_gids(self):
        out = self.ask("carol", "new", name="nlp", purpose="corpora", members=["bob"])
        self.assertEqual(out.status, "refused", "carol has left the roster")
        out = self.ask("dave", "new", name="nlp", purpose="corpora", members=["bob"], viewers=["alice"])
        self.assertEqual(out.status, "held")
        self.assertEqual(self.engine.approvers(out.key), [])
        self.assertEqual(self.engine.approve(out.key, "<@UADMIN>").status, "done")
        nlp = self.roster()["nlp"]
        self.assertEqual(
            (nlp["gid"], nlp["read_gid"]), (50000, 50004), "past the projects' and the reserved GIDs"
        )
        self.assertEqual((nlp["owners"], nlp["members"], nlp["viewers"]), (["dave"], ["bob"], ["alice"]))
        out = self.ask("dave", "new", name="fast", place="scratch")
        self.engine.approve(out.key, "<@UADMIN>")
        self.assertEqual(self.roster()["fast"]["place"], "scratch", "the folder's filesystem, recorded")
        self.assertNotIn("place", nlp, "home is the default, not written")
        self.assertEqual(self.ask("dave", "new", name="vision").status, "refused", "the name is taken")
        self.assertEqual(self.ask("dave", "new", name="Bad_Name").status, "refused")

    def test_closing_waits_for_an_administrator(self):
        out = self.ask("alice", "close")
        self.assertEqual(out.status, "held")
        self.engine.approve(out.key, "<@UADMIN>")
        self.assertNotIn("vision", self.roster() or {})

    def test_purpose(self):
        self.assertEqual(self.ask("alice", "purpose", purpose="benchmarks").status, "done")
        self.assertEqual(self.roster()["vision"]["purpose"], "benchmarks")


class Deployed(unittest.TestCase):
    BEFORE = {"vision": {"owners": ["alice"], "members": ["bob"]}}
    AFTER = {"vision": {"owners": ["alice", "bob"], "viewers": ["dave"]}, "nlp": {"owners": ["carol"]}}

    def test_whose_role_changed(self):
        self.assertEqual(projects.changes(self.BEFORE, self.AFTER), [
            ("carol", "nlp", None, "owners"),
            ("bob", "vision", "members", "owners"),
            ("dave", "vision", None, "viewers"),
        ])  # fmt: skip
        self.assertEqual(projects.changes(self.AFTER, {}), [
            ("carol", "nlp", "owners", None), ("alice", "vision", "owners", None),
            ("bob", "vision", "owners", None), ("dave", "vision", "viewers", None),
        ])  # fmt: skip

    def test_told_once_after_a_deploy_and_not_on_the_first_look(self):
        sent = []

        class DMs(FakeDM):
            def post(self, channel, text, blocks=None, thread_ts=None, cls=None):
                sent.append((channel, text))
                return "1.0"

        post = DMs()
        with tempfile.TemporaryDirectory() as d:
            seen = os.path.join(d, "seen.json")
            slack_of = {"bob": "UBOB", "dave": "UDAVE"}.get
            projects.notify_deployed(
                self.BEFORE, seen, "/home/shared/projects", slack_of, post, projects_blocks.notice
            )
            self.assertEqual(sent, [], "the first look only records")
            projects.notify_deployed(
                self.AFTER, seen, "/home/shared/projects", slack_of, post, projects_blocks.notice
            )
            self.assertEqual([w for w, _ in sent], ["UBOB", "UDAVE"], "carol has no Slack member: not told")
            self.assertEqual(post.dms, [("project", "bob", True), ("project", "dave", True)], "logged")
            self.assertIn("멤버에서 소유자(으)로 바뀌었습니다", sent[0][1])
            self.assertIn("`/home/shared/projects/vision` (읽기만)", sent[1][1])
            self.assertEqual(json.loads(Path(seen).read_text()), self.AFTER)


class Blocks(unittest.TestCase):
    PROJECTS = {
        "vision": {"owners": ["alice"], "members": ["bob"], "purpose": "eval"},
        "nlp": {"owners": ["carol"]},
    }

    def test_home_shows_your_projects_and_what_you_may_do(self):
        blocks = projects_blocks.home("alice", self.PROJECTS, "/home/shared/projects")
        rows = [b for b in blocks if b.get("accessory", {}).get("action_id") == "project.menu"]
        self.assertEqual(len(rows), 1)
        self.assertIn("`/home/shared/projects/vision`", rows[0]["text"]["text"])
        self.assertEqual([o["value"] for o in rows[0]["accessory"]["options"]],
                         ["manage:vision", "purpose:vision", "close:vision", "leave:vision"])  # fmt: skip
        bob = projects_blocks.home("bob", self.PROJECTS, "/home/shared/projects")
        menu = next(b for b in bob if b.get("accessory", {}).get("action_id") == "project.menu")
        self.assertEqual([o["value"] for o in menu["accessory"]["options"]], ["leave:vision"])

    def test_join_lists_only_projects_you_are_not_in(self):
        form = projects_blocks.join_form(self.PROJECTS, "alice")
        options = form["blocks"][0]["element"]["options"]
        self.assertEqual([o["value"] for o in options], ["nlp"])
        self.assertIsNone(projects_blocks.join_form({"vision": self.PROJECTS["vision"]}, "alice"))

    def test_join_offers_every_project_past_a_selects_limit(self):
        """A select takes 100 options: the 101st project and on were cut before."""
        many = {f"p{i:03d}": {"owners": ["bob"], "members": [], "viewers": []} for i in range(250)}
        element = projects_blocks.join_form(many, "alice")["blocks"][0]["element"]
        values = [o["value"] for g in element["option_groups"] for o in g["options"]]
        self.assertEqual(values, sorted(many))
        self.assertTrue(all(len(g["options"]) <= 100 for g in element["option_groups"]))
        self.assertEqual(element["option_groups"][0]["label"]["text"], "p000 ~ p099")

    def test_forms_name_the_fields_their_handlers_read(self):
        places = {"home": "/data/shared", "scratch": "/fast", "scratch_stale_days": 30}
        self.assertEqual([b.get("block_id") for b in projects_blocks.new_form(places)["blocks"][:6]],
                         ["name", "purpose", "place", "owners", "members", "viewers"])  # fmt: skip
        place = projects_blocks.new_form(places)["blocks"][2]["element"]
        self.assertEqual([o["value"] for o in place["options"]], ["home", "scratch"])
        # The cluster's own places and cleanup period, never a typed /scratch or 60 days.
        labels = [o["text"]["text"] for o in place["options"]]
        self.assertTrue(labels[0].startswith("/data/shared: "), labels)
        self.assertIn("/fast: ", labels[1])
        self.assertIn("30일", labels[1])
        manage = projects_blocks.manage_form("vision")
        self.assertEqual((manage["private_metadata"], [b["block_id"] for b in manage["blocks"]]),
                         ("vision", ["person", "role"]))  # fmt: skip


if __name__ == "__main__":
    unittest.main()
