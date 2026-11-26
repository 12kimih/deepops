"""roles/cluster_bot: the administrators' command (cluster-bot.j2, `sudo cluster-bot
notices|tidy [--dry-run]`) runs the bot's code with the interpreter, path, config and user of
the bot's unit, both rendered from the same role vars. Before, the owner was handed a
line of internal paths copied from the unit, which drifts when the unit changes."""

import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import load, render  # noqa: E402

TEMPLATES = ROOT / "roles/cluster_bot/templates"
SITE = {"deepops_dir": "/opt/x", "cluster_tools_lib_dir": "/usr/local/lib/tools", "cluster_bot_user": "bot user",
        "system_accounts": {"bot user": {"home": "/var/lib/bot"}}, "cluster_bot_prefs_file": "/var/lib/p/p.json",
        "ansible_managed": "Ansible managed"}  # fmt: skip


def rendered(name):
    variables = {**load("roles/cluster_bot/vars/main.yml"), **SITE}
    return render((TEMPLATES / name).read_text(), variables)


class AdminCommand(unittest.TestCase):
    def test_the_command_and_the_unit_run_the_same_python_path_config_and_user(self):
        unit = rendered("cluster-bot.service.j2")
        exec_start = shlex.split(re.search(r"^ExecStart=(.*)$", unit, re.M)[1])
        path = re.search(r"^Environment=PYTHONPATH=(.*)$", unit, re.M)[1]
        user = re.search(r"^User=(.*)$", unit, re.M)[1]
        script = rendered("cluster-bot.j2")
        line = re.search(r"^exec (runuser .*?\"\$@\")", script.replace("\\\n", " "), re.M | re.S)[1]
        argv = shlex.split(line)
        self.assertEqual(argv[:5], ["runuser", "-u", user, "--", "env"])
        self.assertEqual(argv[5], f"PYTHONPATH={path}")
        self.assertEqual(argv[6], exec_start[0], "the interpreter")
        self.assertEqual(argv[7:10], ["-m", "$module", exec_start[-1]], "the config")
        # Each subcommand is the bot's module of that name.
        self.assertIn('notices|tidy) module="cluster_bot.$1"', script)
        for sub in ("notices", "tidy"):
            self.assertTrue((ROOT / f"roles/cluster_bot/files/cluster_bot/{sub}.py").is_file())
        self.assertEqual(exec_start[1:3], ["-m", "cluster_bot.app"])

    def test_it_says_its_usage_and_wants_root(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d, "cluster-bot")
            script.write_text(rendered("cluster-bot.j2"))
            script.chmod(0o755)
            run = lambda *a: subprocess.run([str(script), *a], capture_output=True, text=True)  # noqa: E731
            self.assertEqual(run().returncode, 2)
            self.assertEqual(run("bogus").returncode, 2)
            if os.geteuid() != 0:
                for sub in ("notices", "tidy"):
                    done = run(sub, "--dry-run")
                    self.assertEqual(done.returncode, 1)
                    self.assertIn("sudo", done.stderr)

    def test_the_role_installs_it_and_records_it(self):
        tasks = (ROOT / "roles/cluster_bot/tasks/present.yml").read_text()
        self.assertIn("src: cluster-bot.j2", tasks)
        self.assertIn(
            "file:{{ _cluster_bot_admin_command }}",
            load("roles/cluster_bot/vars/main.yml")["cluster_bot_items"],
        )


if __name__ == "__main__":
    unittest.main()
