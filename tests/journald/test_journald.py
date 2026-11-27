"""roles/journald: the system journal sized so its size decides how far back it reaches,
never journald's 100-file default (a file per user evicted the system journal after 11
hours on a login host, 2026-11-27), and cluster_tools.journald, which measures
how far back it reaches for JournalRetentionShort."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "tests/lib"))
from ansible_yaml import load, render  # noqa: E402

from cluster_tools import journald, prom  # noqa: E402

# As journalctl --header prints two files (systemd 255), the older one first or not.
HEADER = """File path: /var/log/journal/m/system.journal
Head sequential number: 29546867 (1c2d973)
Head realtime timestamp: Fri 2026-11-27 21:24:40 KST (6614124e775da)
Tail realtime timestamp: Fri 2026-11-27 22:01:13 KST (66141a79caeb9)
File path: /var/log/journal/m/system@x-0000000001bdff6b-0006613d18c8c343.journal
Head sequential number: 29228907 (1bdff6b)
Head realtime timestamp: Fri 2026-11-27 16:34:56 KST (6613d18c8c343)
Tail realtime timestamp: Fri 2026-11-27 18:09:26 KST (6613e6abdbfd3)
"""
DEFAULTS = load("roles/journald/defaults/main.yml")
VARS = load("roles/journald/vars/main.yml")


def dropin():
    text = (ROOT / "roles/journald/templates/journald.conf.j2").read_text()
    return render(text, {**DEFAULTS, "ansible_managed": "Ansible managed"})


class Retention(unittest.TestCase):
    def test_the_oldest_head_entry_and_its_number(self):
        self.assertEqual(int(journald.oldest(HEADER)), 0x6613D18C8C343 // 10**6)
        self.assertIsNone(journald.oldest("No journal files were found.\n"))

    def test_the_textfile_names_the_target_and_this_run(self):
        values = prom.parse(journald.textfile(1700000000.5, 14, 1700100000))
        self.assertEqual(values, {
            prom.key("journald_entries_since_seconds"): "1700000000",
            prom.key("journald_retention_target_seconds"): "1209600",
            prom.key("journald_retention_last_success_timestamp_seconds"): "1700100000",
        })  # fmt: skip

    def test_the_command_reads_journalctl_and_writes_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d, "journalctl")
            fake.write_text(f"#!/bin/sh\ncat <<'X'\n{HEADER}X\n")
            fake.chmod(0o755)
            out = Path(d, "journal-retention.prom")
            env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}"}
            code = f"import sys; sys.path.insert(0, {str(ROOT / 'roles/cluster_tools_lib/files')!r}); from cluster_tools import journald as j; sys.exit(j.main())"
            done = subprocess.run(
                [sys.executable, "-c", code, str(out), "14"], env=env, capture_output=True, text=True
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn(f"journald_entries_since_seconds{{}} {0x6613D18C8C343 // 10**6}", out.read_text())
            bad = subprocess.run(
                [sys.executable, "-c", code, str(out), "0"], env=env, capture_output=True, text=True
            )
            self.assertEqual(bad.returncode, 2)


class DropIn(unittest.TestCase):
    def test_the_size_decides_never_the_file_count_or_an_age(self):
        text = dropin()
        self.assertIn("SystemMaxFiles=10000", text)
        self.assertIn(f"SystemMaxUse={DEFAULTS['journald_system_max_use']}", text)
        self.assertNotIn("MaxRetentionSec", text)
        self.assertTrue(
            Path(VARS["_journald_dropin"]).name.startswith("zz-deepops-"), "sorts after a package's"
        )

    @unittest.skipUnless(shutil.which("systemd-analyze"), "no systemd-analyze here")
    def test_systemd_reads_it_last_and_knows_every_key(self):
        with tempfile.TemporaryDirectory() as root:
            dropins = Path(root, "etc/systemd/journald.conf.d")
            dropins.mkdir(parents=True)
            Path(root, "etc/systemd/journald.conf.d/00-package.conf").write_text(
                "[Journal]\nSystemMaxFiles=100\n"
            )
            Path(root, VARS["_journald_dropin"].lstrip("/")).write_text(dropin())
            done = subprocess.run(["systemd-analyze", f"--root={root}", "cat-config", "systemd/journald.conf"],
                                  capture_output=True, text=True)  # fmt: skip
            self.assertEqual(done.returncode, 0, done.stderr)
            files = [line[2:] for line in done.stdout.splitlines() if line.startswith("# /")]
            self.assertTrue(files[-1].endswith(Path(VARS["_journald_dropin"]).name), files)
        shipped = Path("/etc/systemd/journald.conf")
        if shipped.exists():  # the package lists every key it takes, commented
            known = {
                line[1:].split("=")[0]
                for line in shipped.read_text().splitlines()
                if line.startswith("#") and "=" in line
            }
            for line in dropin().splitlines():
                if "=" in line and not line.startswith("#"):
                    self.assertIn(line.split("=")[0], known, line)


if __name__ == "__main__":
    unittest.main()
