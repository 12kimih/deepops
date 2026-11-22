"""plugins/callback/run_lock.py: one run that changes hosts at a time per inventory.

A rolling upgrade rebooted a node while a Slurm deploy run beside it was configuring
it (2026-11-23). A second real run against the same inventory now stops before its
first play, naming the first; previews and other inventories run.
"""

import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

BIN = Path(sys.executable).parent
PLUGIN = ROOT / "plugins" / "callback" / "run_lock.py"

spec = importlib.util.spec_from_file_location("run_lock", PLUGIN)
run_lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_lock)

PLAY = """
- hosts: all
  connection: local
  gather_facts: false
  tasks:
    - ansible.builtin.command: "{{ command | default('true') }}"
      changed_when: false
"""


class Lock(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ["rm", "-rf", self.base])

    def test_one_inventory_one_lock_whatever_its_spelling(self):
        inventory = os.path.join(self.base, "inventory")
        Path(inventory).write_text("localhost\n")
        same = run_lock.lock_path([inventory, "x,"], self.base)
        self.assertEqual(
            same, run_lock.lock_path(["x,", os.path.join(self.base, ".", "inventory")], self.base)
        )
        self.assertNotEqual(same, run_lock.lock_path(["x,"], self.base))

    def test_a_held_lock_names_its_holder_and_goes_with_its_process(self):
        path = os.path.join(self.base, "locks", "a.lock")
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import importlib.util, sys, time\n"
             f"spec = importlib.util.spec_from_file_location('m', {str(PLUGIN)!r})\n"
             "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
             f"fd, _ = m.acquire({path!r}, 'the first run'); print('held', flush=True); time.sleep(60)"],
            stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        self.assertEqual(run_lock.acquire(path, "the second run"), (None, "the first run"))
        self.assertEqual(oct(os.stat(os.path.dirname(path)).st_mode & 0o777), "0o700")
        holder.kill()
        holder.wait(timeout=10)
        fd, held_by = run_lock.acquire(path, "the second run")
        self.addCleanup(os.close, fd)
        self.assertIsNone(held_by)


class Runs(unittest.TestCase):
    """ansible-playbook with only this callback, the lock in a throwaway home."""

    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(d)])
        (d / "callbacks").mkdir()
        (d / "callbacks" / "run_lock.py").symlink_to(PLUGIN)
        (d / "play.yml").write_text(PLAY)
        self.play = d / "play.yml"
        self.env = {**unrecorded.env(), "HOME": str(d), "ANSIBLE_CALLBACK_PLUGINS": str(d / "callbacks"),
                    "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"), "ANSIBLE_LOCALHOST_WARNING": "false",
                    "ANSIBLE_GATHERING": "explicit", "ANSIBLE_CACHE_PLUGIN": "memory"}  # fmt: skip

    def run_play(self, *args, inventory="localhost,"):
        return subprocess.run(
            [str(BIN / "ansible-playbook"), "-i", inventory, *args, str(self.play)],
            env=self.env, capture_output=True, text=True, timeout=unrecorded.TIMEOUT,
        )  # fmt: skip

    def test_a_second_real_run_stops_and_a_preview_or_other_inventory_runs(self):
        first = subprocess.Popen(
            [str(BIN / "ansible-playbook"), "-i", "localhost,", "-e", "command='sleep 30'", str(self.play)],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        self.addCleanup(first.kill)
        lock = run_lock.lock_path(["localhost,"], os.path.join(self.env["HOME"], ".cache/deepops/run-locks"))
        deadline = time.monotonic() + unrecorded.TIMEOUT
        while not (os.path.exists(lock) and os.path.getsize(lock)) and time.monotonic() < deadline:
            time.sleep(0.2)
        second = self.run_play()
        self.assertEqual(second.returncode, 4, second.stdout + second.stderr)
        self.assertIn("Another run against this inventory is under way (pid", second.stderr)
        self.assertNotIn("PLAY [", second.stdout)
        self.assertEqual(self.run_play("--check").returncode, 0)
        self.assertEqual(self.run_play(inventory="127.0.0.1,").returncode, 0)
        # Ctrl-C: ansible ends its workers (each in its own session, so they hold the
        # lock as long as a task of theirs runs) and exits; the lock goes with them.
        first.send_signal(signal.SIGINT)
        first.wait(timeout=unrecorded.TIMEOUT)
        third = self.run_play()
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)


if __name__ == "__main__":
    unittest.main()
