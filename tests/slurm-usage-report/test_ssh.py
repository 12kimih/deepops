"""The SSH rate notices, cluster_tools.usage_report --ssh, from cluster_tools.ssh_rate's
own metrics (no cluster needed)."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "tests/lib"))
from fake_slack import FakeDM  # noqa: E402
from cluster_tools import prom as prom_text  # noqa: E402
from cluster_tools import ssh_rate  # noqa: E402
from cluster_tools import usage_report as report  # noqa: E402

NOW = 1_800_000_000


class Prom:
    """Instant queries over the textfile ssh_rate.metrics writes, as host m."""

    def __init__(self, text):
        self.values = prom_text.parse(text)

    def query(self, expr, at=None):
        name, _, want = expr.partition("{")
        return [{"metric": {**dict(labels), "host": "m"}, "value": [0, v]}
                for (n, labels), v in self.values.items() if n == name]  # fmt: skip


def counts(sessions, blocked=(), start=NOW - 5):
    """The metrics of a login node where each of SESSIONS ({login: n}) opened n new
    sessions and each of BLOCKED is refused, from START."""
    with tempfile.TemporaryDirectory() as d:
        cfg = {"mode": "refuse", "window_minutes": 10, "warn_sessions": 90, "refuse_sessions": 100,
               "block_minutes": 10, "block_max_minutes": 60, "exempt_users": [], "state_dir": d,
               "textfile": os.path.join(d, "out.prom"), "docs_url": ""}  # fmt: skip
        for login, n in sessions.items():
            state = {"sessions": [NOW - 60 + i * 0.1 for i in range(n)]}
            if login in blocked:
                state.update(blocked_until=start + 605, block_start=start)
            Path(d, login).write_text(json.dumps(state))
        ssh_rate.metrics(cfg, NOW)
        return Path(cfg["textfile"]).read_text()


class Run(unittest.TestCase):
    CFG = dict(prometheus_url="", dm_log_channel="", slack_token="t", email_domain="", admin_channel="#admins",
               admin_mention="", user_map={"kim": "UKIM", "lee": "ULEE", "park": "UPARK"}, ssh_rate=True,
               ssh_repeat_hours=24)  # fmt: skip

    def run_ssh(self, text, state, now=NOW, cfg=None):
        posted = []

        class Slack(FakeDM):
            token = "t"

            def __init__(self, *_, **__):
                pass

            def post(self, channel, text, blocks=None, thread_ts=None, cls=None):
                posted.append((channel, text))
                return "ts"

            def dm(self, member, text, **_):
                posted.append((member, text))

        cfg = dict(cfg or self.CFG, state_file=f"{state}/state.json")
        with (
            mock.patch.object(report, "Prometheus", lambda url: Prom(text)),
            mock.patch.object(report, "Slack", Slack),
            mock.patch.object(report.time, "time", lambda: now),
        ):
            report.ssh(cfg, mock.Mock(dry_run=False, to=None))
        return posted

    def test_a_warning_daily_and_a_refusal_daily_and_no_channel_line(self):
        text = counts({"kim": 95, "lee": 120, "park": 12}, blocked=("lee",))
        with tempfile.TemporaryDirectory() as d:
            posted = self.run_ssh(text, d)
            to = sorted(p[0] for p in posted)
            self.assertEqual(to, ["UKIM", "ULEE"], "park is below the warning; the DM log has them, no line")
            lee = next(t for m, t in posted if m == "ULEE")
            self.assertIn("막혔습니다", lee)
            self.assertIn("ControlMaster", lee)
            self.assertIn("`usage login-node`", lee, "every warning ends with its check")
            self.assertEqual(self.run_ssh(text, d, NOW + 300), [], "told already: nothing again")
            later = counts({"kim": 95, "lee": 120, "park": 12}, blocked=("lee",), start=NOW + 3600)
            self.assertEqual(
                self.run_ssh(later, d, NOW + 3600), [], "a new block within the day is not told again"
            )
            again = self.run_ssh(later, d, NOW + 25 * 3600)
            self.assertEqual(sorted(p[0] for p in again), ["UKIM", "ULEE"], "both again a day on")

    def test_off_tells_no_one(self):
        text = counts({"kim": 95})
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_ssh(text, d, cfg=dict(self.CFG, ssh_rate=False)), [])


if __name__ == "__main__":
    unittest.main()
