"""cluster_bot.scopes: the scopes Slack granted the bot token against those it needs, as
metrics and once in the administrators' channel. 2026-11-27: a feature added since the
app's manifest was imported failed with missing_scope at the first person who used it."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))  # cluster_tools, which the bot imports
from cluster_bot import commands, scopes  # noqa: E402
from cluster_tools.slack import Error  # noqa: E402

CFG = {"channels": {"admin": "C0ADMIN"}, "admin_mention": "<!subteam^S0ADMINS>", "manifest": "/etc/m.json"}


class FakeSlack:
    def __init__(self, granted=None, error=None):
        self.granted, self.error, self.posts = granted, error, []

    def granted_scopes(self):
        if self.error:
            raise Error(self.error)
        return set(self.granted)

    def post(self, channel, text, blocks=None, thread_ts=None, cls=None):
        self.posts.append((channel, text))


class Scopes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.state = self.dir / "state.json"

    def run_with(self, slack):
        return scopes.run(CFG, slack, self.dir, self.state, 1000)

    def metrics(self):
        return (self.dir / scopes.TEXTFILE).read_text()

    def test_all_granted_is_quiet(self):
        slack = FakeSlack(set(commands.SCOPES) | {"extra:read"})
        self.assertEqual(self.run_with(slack), [])
        self.assertNotIn("cluster_bot_scope_missing{", self.metrics())
        self.assertIn("cluster_bot_scopes_last_success_timestamp_seconds 1000", self.metrics())
        self.assertEqual(slack.posts, [], "nothing was missing before: nothing to say")

    def test_a_missing_scope_is_a_series_and_told_once(self):
        lacking = sorted(commands.SCOPES)[0]
        slack = FakeSlack(set(commands.SCOPES) - {lacking})
        self.assertEqual(self.run_with(slack), [lacking])
        self.assertIn(f'cluster_bot_scope_missing{{scope="{lacking}"}} 1', self.metrics())
        self.assertEqual(len(slack.posts), 1)
        channel, text = slack.posts[0]
        self.assertEqual(channel, "C0ADMIN")
        self.assertTrue(text.startswith("<!subteam^S0ADMINS> "), "the administrators are mentioned")
        self.assertIn(f"`{lacking}`", text)
        self.assertIn("/etc/m.json", text, "what to import")
        self.run_with(slack)
        self.assertEqual(len(slack.posts), 1, "the same gap is not told again")
        self.run_with(FakeSlack(set(commands.SCOPES)))
        self.assertEqual(json.loads(self.state.read_text()), {"reported": []})

    def test_once_whole_again_it_says_so(self):
        lacking = sorted(commands.SCOPES)[0]
        self.run_with(FakeSlack(set(commands.SCOPES) - {lacking}))
        slack = FakeSlack(set(commands.SCOPES))
        self.run_with(slack)
        self.assertEqual(len(slack.posts), 1)
        self.assertIn("every scope it needs again", slack.posts[0][1])

    def test_slack_not_answering_fails_the_run_and_keeps_the_last_answer(self):
        self.run_with(FakeSlack(set(commands.SCOPES)))
        with self.assertRaises(Error):
            scopes.run(CFG, FakeSlack(error="Slack auth.test failed: timed out"), self.dir, self.state, 5000)
        self.assertIn("cluster_bot_scopes_last_success_timestamp_seconds 1000", self.metrics(), "as it was")

    def test_the_needed_scopes_are_the_manifests(self):
        manifest = commands.manifest("/cluster")
        self.assertEqual(set(manifest["oauth_config"]["scopes"]["bot"]), set(commands.SCOPES))


if __name__ == "__main__":
    unittest.main()
