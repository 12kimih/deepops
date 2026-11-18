"""Architecture checks: Slurm is reached through clustertools.slurm alone.

A throttled Slurm RPC never fails, it stalls (the client library retries it for ever),
so an unbounded call hangs whatever made it. clustertools.slurm.run is the one bounded
way out; these tests keep it the only one.
"""

import re
import sys
import unittest
from pathlib import Path

ROLES = Path(__file__).resolve().parents[2] / "roles"
sys.path.insert(0, str(ROLES / "cluster_tools_lib/files"))
from clustertools import cli, slurm  # noqa: E402

# All of the cluster's own Python: the clustertools package.
SOURCES = list(ROLES.glob("cluster_tools_lib/files/**/*.py"))
READER = ROLES / "cluster_tools_lib/files/clustertools/slurm.py"
SLURM = ("scontrol", "squeue", "sinfo", "sacctmgr", "sacct", "scancel", "sprio", "sshare")


class BoundaryTests(unittest.TestCase):
    def test_only_the_reader_starts_processes(self):
        self.assertTrue(SOURCES, "no sources found")
        offenders = [
            f"{p.relative_to(ROLES)}:{n}"
            for p in SOURCES
            if p != READER
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if re.search(r"\b(subprocess|os\.system|os\.popen)\b", line)
        ]
        self.assertEqual(offenders, [], "call Slurm through clustertools.slurm.run, which is bounded")

    def test_the_reader_bounds_every_call(self):
        self.assertIn("timeout=TIMEOUT", READER.read_text())
        self.assertGreater(slurm.TIMEOUT, 0)

    def test_completers_are_bounded(self):
        for name, command in cli.COMPLETERS.items():
            if any(re.search(rf"\b{c}\b", command) for c in SLURM):
                with self.subTest(completer=name):
                    self.assertTrue(command.startswith("timeout "), command)


if __name__ == "__main__":
    unittest.main()
