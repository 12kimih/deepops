#!/usr/bin/env python3
"""slurm_timer_problems: the Slurm timers that must agree (slurm.conf(5)); the role's
template satisfies them. Pure checks on text; nothing is contacted."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

timers = in_tree.load("plugins/filter/slurm_conf.py")


def problems(text):
    return timers.slurm_timer_problems(text)


class Timers(unittest.TestCase):
    def test_slurms_defaults_agree(self):
        self.assertEqual(problems(""), [])

    def test_unkillable_below_five_message_timeouts(self):
        self.assertEqual(len(problems("MessageTimeout=20\nUnkillableStepTimeout=60")), 1)
        self.assertEqual(problems("MessageTimeout=20\nUnkillableStepTimeout=100"), [])

    def test_health_check_inside_its_interval(self):
        self.assertEqual(len(problems("HealthCheckInterval=60\nHealthCheckTimeout=60")), 1)
        self.assertEqual(problems("HealthCheckInterval=300"), [])
        self.assertEqual(problems("HealthCheckTimeout=600"), [])  # no interval: never runs

    def test_complete_wait_zero_or_past_kill_wait(self):
        self.assertEqual(len(problems("KillWait=120\nCompleteWait=60")), 1)
        self.assertEqual(problems("KillWait=120\nCompleteWait=122"), [])

    def test_batch_start_covers_the_prolog(self):
        self.assertEqual(len(problems("PrologEpilogTimeout=600")), 1)
        self.assertEqual(problems("PrologEpilogTimeout=600\nBatchStartTimeout=600"), [])
        self.assertEqual(
            len(problems("PrologEpilogTimeout=600\nPrologTimeout=900\nBatchStartTimeout=600")), 1
        )

    def test_comments_are_not_settings(self):
        self.assertEqual(problems("#PrologEpilogTimeout=600"), [])

    def test_the_role_template_agrees(self):
        text = (ROOT / "roles/slurm/templates/etc/slurm/slurm.conf").read_text()
        self.assertEqual(problems(text), [])


if __name__ == "__main__":
    unittest.main()
