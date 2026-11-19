"""Architecture checks: every process is bounded, and Slurm is reached through slurm.run.

A throttled Slurm RPC never fails, it stalls (the client library retries it for ever),
and a read from a hung NFS mount never returns, so an unbounded call hangs whatever
made it. clustertools.proc.run is the one way to start a process and refuses one
without a timeout; clustertools.slurm.run is the one way to call Slurm.
"""

import re
import sys
import unittest
from pathlib import Path

ROLES = Path(__file__).resolve().parents[2] / "roles"
sys.path.insert(0, str(ROLES / "cluster_tools_lib/files"))
from clustertools import cli, proc, slurm  # noqa: E402

# All of the cluster's own Python: the clustertools package.
SOURCES = list(ROLES.glob("cluster_tools_lib/files/**/*.py"))
READER = ROLES / "cluster_tools_lib/files/clustertools/slurm.py"
PROC = ROLES / "cluster_tools_lib/files/clustertools/proc.py"
SLURM = ("scontrol", "squeue", "sinfo", "sacctmgr", "sacct", "scancel", "sprio", "sshare")


class BoundaryTests(unittest.TestCase):
    def test_only_proc_starts_processes(self):
        self.assertTrue(SOURCES, "no sources found")
        offenders = [
            f"{p.relative_to(ROLES)}:{n}"
            for p in SOURCES
            if p != PROC
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if re.search(r"\b(subprocess|os\.system|os\.popen|os\.exec\w*|os\.spawn\w*)\b", line)
        ]
        self.assertEqual(offenders, [], "start processes with clustertools.proc.run, which is bounded")

    def test_proc_refuses_an_unbounded_call(self):
        for timeout in (None, 0, -1):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                proc.run(["true"], timeout=timeout)

    def test_only_the_reader_calls_slurm(self):
        commands = re.compile(r"""["'](%s)["']""" % "|".join(SLURM))
        offenders = [
            f"{p.relative_to(ROLES)}:{n}"
            for p in SOURCES
            if p not in (READER, ROLES / "cluster_tools_lib/files/clustertools/cli.py")
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if commands.search(line) and "proc.run" in line
        ]
        self.assertEqual(offenders, [], "call Slurm through clustertools.slurm.run")

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
