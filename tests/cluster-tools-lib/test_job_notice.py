"""cluster_tools.job_notice: Slurm's MailProg as Slack DMs. A notice reaches the job
user's Slack ID; notices close together become one message updated in place; a
muted person or a missing token gets nothing and the call still succeeds."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/lib"))
from fake_slack import FakeDM  # noqa: E402
from cluster_tools import job_notice as jn  # noqa: E402

ENV = {"SLURM_JOB_ID": "42", "SLURM_JOB_NAME": "train", "SLURM_JOB_STATE": "COMPLETED",
       "SLURM_JOB_RUN_TIME": "01:02:03", "SLURM_JOB_EXIT_CODE_MAX": "0"}  # fmt: skip


class FakeSlack(FakeDM):
    calls = []

    def __init__(self, token, dry_run=False, counts=None):
        self.token, self.counts = token, counts

    def dm_channel(self, member):
        return "D" + member

    def post(self, channel, text, blocks=None, thread_ts=None, cls=None):
        FakeSlack.calls.append(("post", channel, text))
        return f"ts{len(FakeSlack.calls)}"

    def update(self, channel, ts, text, blocks=None):
        FakeSlack.calls.append(("update", channel, ts, text))


class JobNotice(unittest.TestCase):
    def setUp(self):
        FakeSlack.calls = []
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "token").write_text("xoxb-test\n")
        self.cfg = d / "cfg.json"
        self.cfg.write_text(json.dumps({"token_file": str(d / "token"), "members": {"alice": "UALICE"},
                                        "email_domain": "example.org", "state_file": str(d / "state" / "s.json"),
                                        "coalesce_seconds": 600, "prefs_file": str(d / "prefs.json")}))  # fmt: skip
        self.prefs = d / "prefs.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_notice(self, recipient, env=ENV, now=1000.0):
        with mock.patch.object(jn, "Slack", FakeSlack):
            return jn.main(
                ["job-notice", str(self.cfg), "-s", "Slurm Job_id=42", recipient], env=env, now=now
            )

    def test_a_notice_reaches_the_users_dm(self):
        self.assertEqual(self.run_notice("alice"), 0)
        self.assertEqual(FakeSlack.calls[0][:2], ("post", "DUALICE"))
        self.assertIn("- `42` train: COMPLETED, 01:02:03", FakeSlack.calls[0][2])
        self.assertNotIn("exit", FakeSlack.calls[0][2], "a zero exit code is not shown")

    def test_only_the_jobs_own_user_is_told_never_whom_mail_user_names(self):
        # --mail-user may name anyone (a colleague, an outside address): the notice goes to
        # the job's user (SLURM_JOB_USER) or to no one.
        self.run_notice("bob@elsewhere.example", env={**ENV, "SLURM_JOB_USER": "alice"})
        self.assertEqual(FakeSlack.calls[0][1], "DUALICE")
        FakeSlack.calls = []
        self.run_notice("someone@elsewhere.example")
        self.assertEqual(FakeSlack.calls, [], "an outside address is never looked up")

    def test_the_job_name_is_shown_as_text(self):
        self.run_notice("alice", env={**ENV, "SLURM_JOB_NAME": "<!channel> & <@U1>"})
        self.assertIn("&lt;!channel&gt; &amp; &lt;@U1&gt;", FakeSlack.calls[0][2])
        self.assertNotIn("<!channel>", FakeSlack.calls[0][2])

    def test_the_member_cache_outlives_a_run(self):
        state_file = Path(json.loads(self.cfg.read_text())["state_file"])
        self.run_notice("alice@example.org", now=1000.0)
        first = json.loads(state_file.read_text())["_members"]["alice"]
        self.run_notice("alice@example.org", now=5000.0)  # past the message window
        self.assertEqual(json.loads(state_file.read_text())["_members"]["alice"], first,
                         "the cached answer is reused, not looked up again")  # fmt: skip

    def test_the_sites_email_domain_names_the_login(self):
        self.run_notice("alice@example.org")
        self.assertEqual(FakeSlack.calls[0][1], "DUALICE")

    def test_notices_close_together_become_one_message(self):
        self.run_notice("alice", now=1000)
        failed = dict(ENV, SLURM_JOB_ID="43", SLURM_JOB_STATE="FAILED", SLURM_JOB_EXIT_CODE_MAX="1")
        self.run_notice("alice", env=failed, now=1200)
        self.assertEqual([c[0] for c in FakeSlack.calls], ["post", "update"])
        self.assertIn("2건", FakeSlack.calls[1][3])
        self.assertIn("- `43` train: FAILED, 01:02:03, exit 1", FakeSlack.calls[1][3])
        self.run_notice("alice", now=1700)  # past the window: a new message
        self.assertEqual(FakeSlack.calls[2][0], "post")

    def test_a_muted_person_or_no_token_gets_nothing(self):
        self.prefs.write_text(json.dumps({"muted": ["alice"]}))
        self.assertEqual(self.run_notice("alice"), 0)
        self.prefs.unlink()
        Path(json.loads(self.cfg.read_text())["token_file"]).write_text("")
        self.assertEqual(self.run_notice("alice"), 0)
        self.assertEqual(FakeSlack.calls, [])

    def test_a_long_burst_lists_the_latest_and_counts_the_rest(self):
        text = jn.render([f"line {i}" for i in range(40)])
        self.assertIn("40건", text)
        self.assertIn("line 39", text)
        self.assertNotIn("line 0\n", text)
        self.assertIn("앞의 25개 생략", text)


if __name__ == "__main__":
    unittest.main()
