"""Our programs' lines reach the journal as they are written (cli.line_output): a line
printed by a program that keeps running is readable at once, as the journal reads it
(a pipe, not a terminal), and a stop does not lose it. Tested through the two ways a
program of ours starts: the cluster command launcher (templates/command.j2) and the
cluster_bot package (its service and its admin command)."""

import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "roles/cluster_tools_lib/files"
BOT = ROOT / "roles/cluster_bot/files"
LINE = "dm welcome user1 D0123ABCD 1.2"
SPEAKER = f"import time\n\n\ndef main():\n    print({LINE!r})\n    time.sleep(60)\n"


def launcher(lib_dir, module):
    """templates/command.j2 as the role renders it for MODULE."""
    text = (ROOT / "roles/cluster_tools_lib/templates/command.j2").read_text()
    text = "\n".join(line for line in text.splitlines() if "ansible_managed" not in line)
    return (
        text.replace("{{ cluster_tools_lib_python }}", sys.executable)
        .replace("{{ cluster_tools_lib_dir }}", str(lib_dir))
        .replace("{{ cluster_tools_lib_command.module }}", module)
    )


def first_line(argv, env=None):
    """The first line ARGV writes to a pipe while it is still running, or "" when none
    comes within a few seconds; the program is stopped as systemd stops it (SIGTERM)."""
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, env=env, text=True)
    try:
        ready = select.select([p.stdout], [], [], 5)[0]
        return p.stdout.readline().strip() if ready else ""
    finally:
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=10)


class LineOutput(unittest.TestCase):
    def test_a_cluster_command_writes_each_line_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d)
            (lib / "cluster_tools").symlink_to(LIB / "cluster_tools")
            (lib / "speaker.py").write_text(SPEAKER)
            command = lib / "command"
            command.write_text(launcher(lib, "speaker"))
            command.chmod(0o755)
            self.assertEqual(first_line([str(command)]), LINE)

    def test_every_bot_program_writes_each_line_at_once(self):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": f"{BOT}:{LIB}"}
        code = f"import cluster_bot, time; print({LINE!r}); time.sleep(60)"
        started = time.monotonic()
        self.assertEqual(first_line([sys.executable, "-c", code], env), LINE)
        self.assertLess(time.monotonic() - started, 15)


if __name__ == "__main__":
    unittest.main()
