"""/etc/docker/daemon.json changes only through roles/docker_daemon_json.

Several roles own keys in that one file. Each merging its own keys by hand never
removed a key it stopped setting (default-runtime: nvidia stayed after the toggle
went off), and each restarted dockerd through its own handler and knob. The role
records each owner's keys, removes the ones it drops, and restarts dockerd once.
Any task that writes to daemon.json (a path, a templated one) or runs a command naming
it is checked;
the exceptions are named by file and task.
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import Scan, ansible_files, module, tasks  # noqa: E402

# (file, task) -> why it may touch the file
EXCEPTIONS = {
    ("roles/nvidia_container_toolkit/tasks/docker.yml", "Docker | look for the Docker daemon's settings"):
        "a stat: whether roles/docker keeps the file, writes nothing",
}  # fmt: skip
TOUCHES = re.compile(r"daemon\.json|nvidia-ctk\s+runtime\s+configure|--default-runtime")


def touching(path):
    for task in tasks(path):
        name, _ = module(task)
        if name in ("include_role", "import_role"):
            continue
        _, args = module(task)
        # Where it writes, and what it runs; not the words of a file's content.
        targets = [args.get(k, "") for k in ("dest", "path", "_raw_params", "cmd", "argv")]
        if TOUCHES.search(" ".join(map(str, targets))):
            yield task.get("name", "?")


class DaemonJsonTests(unittest.TestCase):
    def test_only_docker_daemon_json_writes_the_file(self):
        scan, offenders = Scan(EXCEPTIONS, minimum=200), []
        for path in map(scan.seen, ansible_files()):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("roles/docker_daemon_json/"):
                continue
            offenders += [f"{rel}: {n}" for n in touching(path) if not scan.excuse((rel, n))]
        self.assertEqual(offenders, [], "set daemon.json keys through roles/docker_daemon_json")
        scan.check(self)

    def test_the_check_sees_each_form(self):
        with tempfile.TemporaryDirectory() as d:
            sample = Path(d) / "t.yml"
            sample.write_text(
                "- name: a\n  copy: {dest: '{{ docker_config_dir }}/daemon.json', content: '{}'}\n"
                "- name: b\n  shell: nvidia-ctk runtime configure --runtime=docker\n"
                "- name: c\n  lineinfile: path=/etc/docker/daemon.json line=x\n"
                "- name: d\n  include_role: {name: docker_daemon_json}\n"
            )
            self.assertEqual(list(touching(sample)), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
