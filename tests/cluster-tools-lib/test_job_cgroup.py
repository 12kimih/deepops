"""cluster_tools.job_cgroup: whose a process is, by the cgroup structure, on every case of
tests/lib/slurm_cgroups.json (the epilog's bash is tested on the same cases,
tests/slurm-epilog). Before, a job was found by a job_<id> directory name: on Slurm 26.05,
which names it by the job's SLUID, no process was ever in a job (2026-11-27)."""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from cluster_tools import job_cgroup  # noqa: E402

sys.path.insert(0, str(ROOT / "tests/lib"))
from repo_scan import tracked  # noqa: E402

SPEC = ROOT / "tests/lib/slurm_cgroups.json"
# The two readers of a job's cgroup path; nothing else parses one.
READERS = {
    "roles/cluster_tools_lib/files/cluster_tools/job_cgroup.py",
    "roles/slurm/templates/etc/slurm/shared/lib/epilog-job-scope.sh",
}
# A job cgroup path in code: the scope's name, the v1 user directory, or a job_<id> match.
PARSING = re.compile(r"slurmstepd[\\.]+scope|/slurm/uid_|job_\(\\d|job_\(\[0-9|job_\[0-9")


class Spec(unittest.TestCase):
    def test_every_case(self):
        cases = json.loads(SPEC.read_text())["cases"]
        self.assertGreater(len(cases), 0)
        kinds = set()
        for case in cases:
            with self.subTest(case["name"]):
                self.assertEqual(job_cgroup.classify(case["cgroup"]), (case["kind"], case["keys"]))
                kinds.add(case["kind"])
        self.assertEqual(kinds, {"job", "slurm", "none", "bad"}, "the spec covers every kind")

    def test_the_spec_has_both_namings_and_both_versions(self):
        keys = [k for case in json.loads(SPEC.read_text())["cases"] for k in case["keys"]]
        self.assertTrue(any(k.startswith("job_") for k in keys))
        self.assertTrue(any(not k.startswith("job_") for k in keys), "a SLUID-named job")
        cgroups = [case["cgroup"] for case in json.loads(SPEC.read_text())["cases"]]
        self.assertTrue(
            any(c.startswith("0::") for c in cgroups) and any("/slurm/uid_" in c for c in cgroups)
        )


class OneReader(unittest.TestCase):
    def test_no_other_code_parses_a_job_cgroup_path(self):
        found, scanned = [], 0
        for path in tracked("roles", "scripts", "playbooks"):
            name = path.relative_to(ROOT).as_posix()
            if name in READERS or path.suffix == ".md":
                continue
            scanned += 1
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            if PARSING.search(text):
                found.append(name)
        self.assertGreater(scanned, 100)
        self.assertEqual(found, [], "use cluster_tools.job_cgroup (or the epilog's lib)")

    def test_the_pattern_finds_the_old_parsers(self):
        for old in (r'JOB = re.compile(r"/job_(\d+)(?:/|$)")', '[[ "$path" =~ (^|/)job_([0-9]+)(/|$) ]]',
                    "/system.slice/slurmstepd.scope/job_1"):  # fmt: skip
            self.assertRegex(old, PARSING)


if __name__ == "__main__":
    unittest.main()
