"""Every oneshot unit the roles install is bounded in time.

systemd gives Type=oneshot no start timeout by default. A timer starts its service
only when the last run has finished, so one hung run (a stuck NFS read, a Slurm or
Prometheus call that never returns) stops it for good, silently; a boot-time oneshot
that hangs holds up whatever is ordered after it. A bound is TimeoutStartSec=,
TimeoutSec= or RuntimeMaxSec= with a finite value, in the same [Service] section.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import Scan, tracked  # noqa: E402

# file -> why its oneshot units are left unbounded
UNBOUNDED_OK = {
    "roles/nfs/templates/nfs-detach-on-shutdown.service.j2": "runs only at shutdown, bounded by TimeoutStopSec",
    "roles/nfs/templates/nfs-server-restart.service.j2": "OnFailure= restart of nfs-server, which has its own timeout",
}
BOUNDS = {"timeoutstartsec", "timeoutsec", "runtimemaxsec"}
UNBOUNDED = {"", "infinity", "0"}


def services(text):
    """The [Service] sections of a unit (as a file, or embedded in a task's content),
    each as {lower-case key: last value}, comments left out."""
    sections, current = [], None
    for raw in text.splitlines():
        line = raw.strip().strip("'\"")
        if re.fullmatch(r"\[[A-Za-z]+\]", line):
            current = {} if line == "[Service]" else None
            if current is not None:
                sections.append(current)
            continue
        if current is None or not line or line[0] in "#;":
            continue
        key, sep, value = line.partition("=")
        if sep:
            current[key.strip().lower()] = value.strip()
    return sections


def unbounded(text):
    return [
        s for s in services(text)
        if s.get("type", "").lower() == "oneshot"
        and not any(s.get(k, "").lower() not in UNBOUNDED for k in BOUNDS if k in s)
    ]  # fmt: skip


class OneshotTests(unittest.TestCase):
    def test_every_oneshot_unit_is_bounded(self):
        scan, offenders = Scan(UNBOUNDED_OK, minimum=100), []
        ours = [
            p
            for p in tracked("roles", "playbooks")
            if "/galaxy/" not in str(p) and "/molecule/" not in str(p)
        ]
        for path in map(scan.seen, ours):
            text = path.read_text(errors="ignore")
            if "oneshot" in text.lower() and unbounded(text):
                rel = path.relative_to(ROOT).as_posix()
                if not scan.excuse(rel):
                    offenders.append(rel)
        self.assertEqual(offenders, [], "give each oneshot TimeoutStartSec= (or RuntimeMaxSec=)")
        scan.check(self)

    def test_the_check_reads_units_as_systemd_does(self):
        self.assertTrue(unbounded("[Service]\nType = oneshot\n#TimeoutStartSec=5min\n"))
        self.assertTrue(unbounded("[Service]\nType=oneshot\nTimeoutStartSec=infinity\n"))
        self.assertTrue(unbounded("[Service]\nType=oneshot\n[Install]\n[Service]\nTimeoutStartSec=5\n"))
        self.assertFalse(unbounded("[Service]\nType=oneshot\nTimeoutSec=30\n"))
        self.assertFalse(unbounded("  [Service]\n  Type=oneshot\n  RuntimeMaxSec=10min\n"))
        self.assertFalse(unbounded("[Service]\nType=simple\n"))


if __name__ == "__main__":
    unittest.main()
