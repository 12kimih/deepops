"""usage, end to end against the fake Slurm: the exit status an agent reads (2 you called
it wrong, 3 retry later), the help naming the JSON keys, one completion of every
subcommand (the owner's single command, 2026-11-27)."""

import json
import tempfile
import unittest
from pathlib import Path

from common import run_tool

from cluster_tools import rules, usage


class Usage(unittest.TestCase):
    def run_with(self, cfg, *args, path=True):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "usage.json"
            if cfg is not None:
                file.write_text(json.dumps(cfg))
            return run_tool("usage", *args, env={"CLUSTER_TOOLS_USAGE_CONFIG": str(file)}, path=path)

    def test_a_wrong_call_is_2_and_a_silent_source_3(self):
        self.assertEqual(self.run_with({"prometheus_url": ""}, "--bogus").returncode, 2)
        self.assertEqual(self.run_with(None).returncode, 2, "no rules file: could not run")
        done = self.run_with({"prometheus_url": ""}, "--json", path=False)  # no Slurm on this host
        self.assertEqual(done.returncode, 3, done.stderr)
        doc = json.loads(done.stdout)
        self.assertEqual((doc["schema"], doc["status"]), (1, 3))
        self.assertEqual({r["rule"]: r["state"] for r in doc["rules"]}["queued-jobs"], "?")

    def test_the_help_lists_the_subcommands_and_the_json_keys(self):
        text = run_tool("usage", "--help").stdout
        for sub in usage.GROUP.subs:
            self.assertIn(f"  {sub} ", text)
        for key in rules.JSON_KEYS:
            self.assertIn(key, text)
        for sub in usage.GROUP.subs:
            self.assertEqual(run_tool("usage", sub, "-h").returncode, 0, sub)

    def test_one_completion_names_every_subcommand(self):
        text = run_tool("usage", "--completion", "bash").stdout
        self.assertIn("complete -F _usage usage", text)
        for sub in usage.GROUP.subs:
            self.assertIn(f"        {sub})", text)


if __name__ == "__main__":
    unittest.main()
