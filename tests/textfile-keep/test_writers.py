"""A textfile a timer writes survives a reboot. node_exporter's textfile directory is a
tmpfs: a writer that runs daily or weekly lost its series at every boot until its next
run, and with them the last success its staleness alert counts from (a reboot after the
04:00 container cleanup left ContainerStorageCleanupNeverReported pending, 2026-11-23).

A role that writes a textfile (it names node_exporter_prom_dir) from a timer either has
that timer run again soon after boot (OnBootSec, OnUnitActiveSec or OnUnitInactiveSec of
30 minutes or less, read from the role's defaults when it is a variable) or keeps the
file through roles/textfile_keep. Each timer of the role counts: a role with a frequent
timer and a weekly one keeps the weekly one's file."""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

SOON = 30 * 60
UNITS = {"": 1, "s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600, "d": 86400}
EARLY_KEYS = ("OnBootSec", "OnUnitActiveSec", "OnUnitInactiveSec")


def seconds(value):
    match = re.fullmatch(r"(\d+)\s*([a-z]*)", value.strip())
    return int(match[1]) * UNITS[match[2]] if match and match[2] in UNITS else None


def timers(role):
    """The [Timer] sections a role installs: its *.timer templates and timer units
    written inline by its tasks."""
    found = [(ROOT / p).read_text() for p in tracked(f"roles/{role}/templates/*.timer.j2")]
    for path in tracked(f"roles/{role}/tasks/*.yml"):
        found += re.findall(r"\[Timer\][^\[]*", (ROOT / path).read_text())
    return found


def early(timer, defaults):
    """Does TIMER run within SOON of boot?"""
    for key in EARLY_KEYS:
        for value in re.findall(rf"^\s*{key}=(.+)$", timer, re.M):
            name = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", value.strip())
            value = str(defaults.get(name[1], "")) if name else value
            if (seconds(value) or SOON + 1) <= SOON:
                return True
    return False


def writers():
    """{role: (defaults, timers, keeps)} for each role that writes a textfile."""
    out = {}
    for role in sorted(
        {Path(p).resolve().relative_to(ROOT).parts[1] for p in tracked("roles/*/*/*.yml")}
        - {"textfile_keep", "prometheus_paths"}
    ):
        files = [
            ROOT / p
            for p in tracked(
                f"roles/{role}/defaults/*.yml", f"roles/{role}/vars/*.yml", f"roles/{role}/tasks/*.yml"
            )
        ]
        if not any("node_exporter_prom_dir" in f.read_text() for f in files):
            continue
        defaults = {}
        for path in tracked(f"roles/{role}/defaults/*.yml"):
            defaults.update(yaml.safe_load((ROOT / path).read_text()) or {})
        keeps = any(
            "name: textfile_keep" in (ROOT / p).read_text() for p in tracked(f"roles/{role}/tasks/*.yml")
        )
        out[role] = (defaults, timers(role), keeps)
    return out


class Writers(unittest.TestCase):
    def test_the_daily_writers_are_found(self):
        self.assertTrue(
            {"container_storage_gc", "scratch_space", "disk_quota", "slurm_backup"} <= set(writers())
        )

    def test_a_timer_that_does_not_run_soon_after_boot_keeps_its_file(self):
        offenders = [
            role
            for role, (defaults, found, keeps) in writers().items()
            if any(not early(t, defaults) for t in found) and not keeps
        ]
        self.assertEqual(
            offenders, [], "include roles/textfile_keep (tasks_from: restore) and write the kept copy"
        )

    def test_the_rule_reads_values_and_variables(self):
        self.assertTrue(early("[Timer]\nOnBootSec=5min\n", {}))
        self.assertTrue(early("[Timer]\nOnUnitActiveSec={{ every }}\n", {"every": "60s"}))
        self.assertFalse(early("[Timer]\nOnCalendar=daily\n", {}))
        self.assertFalse(early("[Timer]\nOnCalendar=daily\nOnBootSec=2h\n", {}))


if __name__ == "__main__":
    unittest.main()
