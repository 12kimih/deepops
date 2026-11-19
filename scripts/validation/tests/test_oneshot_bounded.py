"""Every oneshot unit the roles install is bounded in time.

systemd gives Type=oneshot no start timeout by default. A timer starts its service
only when the last run has finished, so one hung run (a stuck NFS read, a Slurm or
Prometheus call that never returns) stops it for good, silently; a boot-time oneshot
that hangs holds up whatever is ordered after it.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# file -> why its oneshot units are left unbounded
UNBOUNDED_OK = {
    "roles/nfs/templates/nfs-detach-on-shutdown.service.j2": "runs only at shutdown, bounded by TimeoutStopSec",
    "roles/nfs/templates/nfs-server-restart.service.j2": "OnFailure= restart of nfs-server, which has its own timeout",
    "roles/nfs/templates/nfs-server-autorestart.conf.j2": "a drop-in for nfs-server, not a unit of its own",
    "roles/nfs/defaults/main.yml": "describes the nfs units above",
    "roles/nfs/tasks/server.yml": "installs the nfs units above",
    "roles/nvidia_power_limit/molecule/remove/prepare.yml": "a test fixture",
}


def unit_sources():
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "roles", "playbooks"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for name in filter(None, out.split("\0")):
        if not name.startswith("roles/galaxy/") and name not in UNBOUNDED_OK:
            path = ROOT / name
            if path.is_file() and "Type=oneshot" in path.read_text(errors="ignore"):
                yield path


class OneshotTests(unittest.TestCase):
    def test_every_oneshot_unit_is_bounded(self):
        offenders = []
        for path in unit_sources():
            # One unit per [Service] section; each needs its own bound.
            for section in re.split(r"\[Service\]", path.read_text())[1:]:
                body = section.split("[Install]")[0].split("[Unit]")[0]
                if "Type=oneshot" in body and not re.search(r"(TimeoutStartSec|RuntimeMaxSec)=", body):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "give each oneshot TimeoutStartSec= (or RuntimeMaxSec=)")

    def test_exceptions_still_exist(self):
        for name in UNBOUNDED_OK:
            self.assertTrue((ROOT / name).is_file(), f"{name} is gone; drop it from UNBOUNDED_OK")


if __name__ == "__main__":
    unittest.main()
