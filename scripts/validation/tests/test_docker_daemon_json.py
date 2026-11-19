"""/etc/docker/daemon.json changes only through roles/docker_daemon_json.

Several roles own keys in that one file. Each merging its own keys by hand never
removed a key it stopped setting (default-runtime: nvidia stayed after the toggle
went off), and each restarted dockerd through its own handler and knob. The role
records each owner's keys, removes the ones it drops, and restarts dockerd once.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# Reads the file without writing it, or a tool that manages its own keys.
READ_ONLY = {
    "roles/nvidia_container_toolkit/tasks/docker.yml": "checks the NVIDIA runtime block nvidia-ctk writes",
}
WRITE = re.compile(r"/etc/docker/daemon\.json")


class DaemonJsonTests(unittest.TestCase):
    def test_only_docker_daemon_json_writes_the_file(self):
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", "roles", "playbooks"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        offenders = []
        for name in filter(None, out.split("\0")):
            path = ROOT / name
            if name.startswith(("roles/docker_daemon_json/", "roles/galaxy/")) or name in READ_ONLY:
                continue
            if not path.is_file() or not name.endswith((".yml", ".yaml")) or "/molecule/" in name:
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if WRITE.search(line) and not line.lstrip().startswith("#"):
                    offenders.append(f"{name}:{number}")
        self.assertEqual(offenders, [], "set daemon.json keys through roles/docker_daemon_json")


if __name__ == "__main__":
    unittest.main()
