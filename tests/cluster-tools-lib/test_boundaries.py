"""Architecture checks: every process is bounded, and Slurm is reached through slurm.run.

A throttled Slurm RPC never fails, it stalls (the client library retries it for ever),
and a read from a hung NFS mount never returns, so an unbounded call hangs whatever
made it. cluster_tools.proc.run is the one way to start a process and refuses one
without a timeout; cluster_tools.slurm.run is the one way to call Slurm.
"""

import ast
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROLES = Path(__file__).resolve().parents[2] / "roles"
sys.path.insert(0, str(ROLES / "cluster_tools_lib/files"))
from cluster_tools import cli, proc, slurm  # noqa: E402

sys.path.insert(0, str(ROLES.parent / "tests/lib"))
from repo_scan import tracked  # noqa: E402

# All of the cluster's own Python: the cluster_tools package.
SOURCES = tracked("roles/cluster_tools_lib/files/*.py")
READER = ROLES / "cluster_tools_lib/files/cluster_tools/slurm.py"
PROC = ROLES / "cluster_tools_lib/files/cluster_tools/proc.py"
SLURM = (
    "scontrol", "squeue", "sinfo", "sacctmgr", "sacct", "scancel", "sprio", "sshare", "srun", "sbatch",
    "salloc", "sstat", "sdiag", "sreport", "scrontrol", "strigger", "sattach",
)  # fmt: skip


class BoundaryTests(unittest.TestCase):
    def test_only_proc_starts_processes(self):
        self.assertTrue(SOURCES, "no sources found")
        offenders = [
            f"{p.relative_to(ROLES)}:{n}"
            for p in SOURCES
            if p != PROC
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if re.search(
                r"(subprocess|os\.system|os\.popen|os\.exec\w*|os\.spawn\w*|os\.posix_spawn\w*)\b", line
            )
        ]
        self.assertEqual(offenders, [], "start processes with cluster_tools.proc.run, which is bounded")

    def test_a_timeout_kills_what_the_process_started(self):
        # subprocess.run kills only its own child: a pipeline's members (a dump and its
        # gzip) outlived it and kept writing.
        marker = f"{os.getpid()}.25"
        with self.assertRaises(proc.TimeoutExpired):
            proc.run(["bash", "-o", "pipefail", "-c", '"$@" | cat', "bash", "sleep", marker], timeout=0.5)
        # Gone within a deadline, not a fixed pause: on a loaded host the killed process
        # may take longer than 0.2 s to leave the process table.
        deadline = time.monotonic() + 10
        while True:
            alive = subprocess.run(["pgrep", "-x", "-f", f"sleep {marker}"], capture_output=True, text=True)
            if not alive.stdout or time.monotonic() > deadline:
                break
            time.sleep(0.1)
        self.assertEqual(alive.stdout, "")

    def test_proc_refuses_an_unbounded_call(self):
        for timeout in (None, 0, -1):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                proc.run(["true"], timeout=timeout)

    def test_proc_text_survives_bytes_that_are_not_utf8(self):
        out = proc.run(["printf", "job\\377name"], timeout=5, capture_output=True, text=True).stdout
        self.assertEqual(out, "job\ufffdname")

    def test_outcome_reports_instead_of_raising(self):
        # The validators' form: a failed probe is a finding, never a traceback.
        self.assertEqual(
            proc.outcome(["sh", "-c", "echo out; echo err >&2; exit 3"], timeout=5), (3, "out", "err")
        )
        self.assertEqual(proc.outcome(["printf", "a\\377"], timeout=5), (0, "a\ufffd", ""))
        self.assertEqual(proc.outcome(["cat"], timeout=5, input="in\n"), (0, "in", ""))
        self.assertEqual(proc.outcome(["sleep", "5"], timeout=0.2), (124, "", "timeout after 0.2s"))
        self.assertEqual(proc.outcome(["no-such-command-x"], timeout=5)[0], 127)
        self.assertEqual(proc.outcome(["/"], timeout=5)[0], 126)

    @staticmethod
    def slurm_argvs(tree):
        """Lines of every argv (a list or tuple literal) that starts with a Slurm command,
        wherever it is built: on the proc.run line or before it."""
        for node in ast.walk(tree):
            first = node.elts[0] if isinstance(node, (ast.List, ast.Tuple)) and node.elts else None
            # A bare name or an absolute path to one (/usr/bin/scancel).
            if isinstance(first, ast.Constant) and str(first.value).rsplit("/", 1)[-1] in SLURM:
                yield node.lineno

    def test_only_the_reader_calls_slurm(self):
        # slurm.run("scancel", id) is the way; an argv for proc.run, built on the same
        # line or anywhere before it, is not. cli.py only names commands for completion.
        offenders = [
            f"{p.relative_to(ROLES)}:{n}"
            for p in SOURCES
            if p not in (READER, ROLES / "cluster_tools_lib/files/cluster_tools/cli.py")
            for n in self.slurm_argvs(ast.parse(p.read_text()))
        ]
        self.assertEqual(offenders, [], "call Slurm through cluster_tools.slurm.run")

    def test_the_check_sees_an_argv_built_apart(self):
        tree = ast.parse(
            'argv = ["scancel", job]\nproc.run(argv, timeout=5)\nx = ("sinfo",)\ny = ["/usr/bin/srun"]\n'
        )
        self.assertEqual(list(self.slurm_argvs(tree)), [1, 3, 4])

    def test_the_reader_bounds_every_call(self):
        calls = [
            node
            for node in ast.walk(ast.parse(READER.read_text()))
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "proc.run"
        ]
        self.assertTrue(calls, "the reader starts no process?")
        for call in calls:
            with self.subTest(line=call.lineno):
                self.assertIn("timeout", [k.arg for k in call.keywords])
        self.assertGreater(slurm.TIMEOUT, 0)

    def test_completers_are_bounded(self):
        for name, command in cli.COMPLETERS.items():
            if any(re.search(rf"\b{c}\b", command) for c in SLURM):
                with self.subTest(completer=name):
                    self.assertTrue(command.startswith("timeout "), command)


if __name__ == "__main__":
    unittest.main()
