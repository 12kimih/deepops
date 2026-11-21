"""plugins/action/find_present.py run by Ansible: a missing path is an empty directory,
not a warning; the paths that exist are searched as find does."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")


def run(paths):
    with tempfile.TemporaryDirectory() as tmp:
        play = [{"hosts": "localhost", "gather_facts": False, "connection": "local",
                 "tasks": [{"name": "find", "find_present": {"paths": paths, "patterns": "*.yml"}}]}]  # fmt: skip
        (Path(tmp) / "play.yml").write_text(json.dumps(play))
        env = unrecorded.env(
            ANSIBLE_ACTION_PLUGINS=str(ROOT / "plugins" / "action"),
            ANSIBLE_LOAD_CALLBACK_PLUGINS="1",
            ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json",
            ANSIBLE_LOCALHOST_WARNING="false",
        )
        done = subprocess.run([str(PLAYBOOK), "-i", "localhost,", "play.yml"], cwd=tmp, env=env,
                              capture_output=True, text=True, timeout=unrecorded.TIMEOUT)  # fmt: skip
    out = json.loads(done.stdout)
    return out["plays"][0]["tasks"][0]["hosts"]["localhost"], out.get("stats"), done.stderr


class FindPresent(unittest.TestCase):
    def test_missing_paths_are_empty_without_a_warning(self):
        result, _, stderr = run(["/nonexistent/deepops-a", "/nonexistent/deepops-b"])
        self.assertEqual((result["matched"], result["files"]), (0, []))
        self.assertNotIn("Skipped", json.dumps(result) + stderr)

    def test_present_paths_are_searched(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.yml").write_text("x: 1\n")
            result, _, _ = run(f"{d},/nonexistent/deepops-c")
        self.assertEqual(result["matched"], 1)
        self.assertEqual([Path(f["path"]).name for f in result["files"]], ["a.yml"])


if __name__ == "__main__":
    unittest.main()
