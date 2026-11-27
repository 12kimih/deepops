"""One exit status across the cluster commands (cluster_tools.exits), end to end: a wrong
option is 2, a Slurm client missing here 2, a Slurm that does not answer 3, and each
command's -h states the codes it gives from that one place."""

import json
import tempfile
import unittest
from pathlib import Path

from common import run_tool

from cluster_tools import exits, qos, sq, usage

SILENT = {"FAKE_SLURM_SLEEP": "3", "CLUSTER_TOOLS_TIMEOUT": "1"}


class Commands(unittest.TestCase):
    """Every command that reads Slurm, called as its user calls it (module, args)."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        cfg = self.dir / "usage.json"
        cfg.write_text(json.dumps({"prometheus_url": ""}))
        self.env = {"CLUSTER_TOOLS_USAGE_CONFIG": str(cfg)}
        self.slurm_readers = [
            ("sq", ()),
            ("sq", ("--json",)),
            ("qos", ()),
            ("usage", ()),
            ("usage", ("gpu", "42")),
            ("usage", ("io",)),
            ("usage", ("submit",)),
            ("metrics", (str(self.dir / "slurm_gpu.prom"),)),
        ]

    def run_cmd(self, module, args, env=None, path=True):
        return run_tool(module, *args, env={**self.env, **(env or {})}, path=path)

    def test_a_silent_slurm_is_3(self):
        for module, args in self.slurm_readers:
            with self.subTest(command=(module, *args)):
                done = self.run_cmd(module, args, SILENT)
                self.assertEqual(done.returncode, exits.UNKNOWN, done.stderr)
                self.assertNotIn("Traceback", done.stderr)

    def test_no_slurm_client_could_not_run(self):
        for module, args in self.slurm_readers:
            if (module, args) in (("usage", ()), ("usage", ("submit",))):
                continue  # each rule it cannot read is a "?" row, whatever the cause (3)
            with self.subTest(command=(module, *args)):
                done = self.run_cmd(module, args, path=False)
                self.assertEqual(done.returncode, exits.CANNOT_RUN, done.stderr)
                self.assertIn("not found", done.stderr)

    def test_a_wrong_option_is_2(self):
        for module, args in self.slurm_readers + [("usage", ("disk",)), ("usage", ("login-node",))]:
            with self.subTest(command=(module, *args)):
                done = self.run_cmd(module, (*args, "--bogus"))
                self.assertEqual(done.returncode, exits.CANNOT_RUN, done.stderr)

    def test_a_silent_prometheus_is_3(self):
        cfg = self.dir / "dead.json"
        cfg.write_text(json.dumps({"prometheus_url": "http://127.0.0.1:9"}))  # the discard port: refused
        done = run_tool("usage", "disk", env={"CLUSTER_TOOLS_USAGE_CONFIG": str(cfg)})
        self.assertEqual(done.returncode, exits.UNKNOWN, done.stderr)


class Help(unittest.TestCase):
    def test_every_command_states_its_codes_in_its_brief_help(self):
        commands = [sq.COMMAND, qos.COMMAND, usage.COMMAND] + [c for _, c in usage.GROUP.commands()[1:]]
        self.assertEqual(
            {c.name for c in commands},
            {"sq", "qos", "usage", "usage gpu", "usage disk", "usage io", "usage submit", "usage login-node"},
        )
        for c in commands:
            with self.subTest(command=c.name):
                for full in (False, True):
                    self.assertIn(exits.note(c.judges), c.help(full))
                self.assertEqual(c.judges, c.name.startswith("usage"), "only usage judges risk (1)")
        self.assertNotIn(f"  {exits.AT_RISK}  ", sq.COMMAND.help(False))
        self.assertIn(f"  {exits.AT_RISK}  at risk", usage.COMMAND.help(False))


if __name__ == "__main__":
    unittest.main()
