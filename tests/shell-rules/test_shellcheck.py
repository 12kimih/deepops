"""Every shell script passes shellcheck (pinned in pyproject.toml) at warning severity:
the errors and warnings are bugs or traps (a `cd` that fails and the script goes on, an
array squashed into one string, an unquoted command substitution split on spaces).

PENDING lists the scripts not clean yet, each with why; it only shrinks: a script that
passes and is still listed fails the test, so the entry goes with the fix. Role templates
parse as shell only once rendered: tests/slurm-epilog checks the rendered Slurm scripts.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import shell_scripts  # noqa: E402

SHELLCHECK = shutil.which("shellcheck", path=str(Path(sys.executable).parent)) or shutil.which("shellcheck")

# script -> why it is not clean yet
PENDING = {
    "virtual/vagrant_shutdown.sh": "upstream Vagrant test cluster (virtual/): fix, or retire",
    "workloads/bit/hpl/launch_hpl_experiment.sh": "upstream HPL burn-in example (workloads/bit): fix, or retire with workloads/",
    "workloads/bit/hpl/submit_hpl.sh": "upstream HPL burn-in example (workloads/bit): fix, or retire with workloads/",
    "workloads/bit/hpl/syscfg-dgx1v.sh": "upstream HPL burn-in example (workloads/bit): fix, or retire with workloads/",
    "workloads/bit/hpl/syscfg-dgx2.sh": "upstream HPL burn-in example (workloads/bit): fix, or retire with workloads/",
    "workloads/bit/hpl/syscfg-dgxa100-40gb.sh": "upstream HPL burn-in example (workloads/bit): fix, or retire with workloads/",
    "workloads/bit/hpl/syscfg-dgxa100-80gb.sh": "upstream HPL burn-in example (workloads/bit): fix, or retire with workloads/",
    "workloads/examples/k8s/dask-rapids/deploy.sh": "upstream Kubernetes example: fix, or retire with workloads/",
    "workloads/examples/k8s/users/k8s_user.sh": "upstream Kubernetes example: fix, or retire with workloads/",
    "workloads/jenkins/scripts/get-k8s-debug.sh": "upstream NVIDIA Jenkins CI, which this fork does not run: retire, or fix",
    "workloads/jenkins/scripts/get-slurm-debug.sh": "upstream NVIDIA Jenkins CI, which this fork does not run: retire, or fix",
    "workloads/jenkins/scripts/jenkins-common.sh": "upstream NVIDIA Jenkins CI, which this fork does not run: retire, or fix",
    "workloads/jenkins/scripts/test-dashboard.sh": "upstream NVIDIA Jenkins CI, which this fork does not run: retire, or fix",
    "workloads/jenkins/scripts/test-dcgm-metrics.sh": "upstream NVIDIA Jenkins CI, which this fork does not run: retire, or fix",
    "workloads/jenkins/scripts/test-dle-deployment.sh": "upstream NVIDIA Jenkins CI, which this fork does not run: retire, or fix",
    "workloads/jenkins/scripts/test-kubeflow-pipeline.sh": "upstream NVIDIA Jenkins CI, which this fork does not run: retire, or fix",
    "workloads/jenkins/scripts/test-kubeflow.sh": "upstream NVIDIA Jenkins CI, which this fork does not run: retire, or fix",
    "workloads/jenkins/scripts/test-local-registry.sh": "upstream NVIDIA Jenkins CI, which this fork does not run: retire, or fix",
}


def findings(paths):
    out = subprocess.run(
        [SHELLCHECK, "--severity=warning", "--format=json1", *map(str, paths)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    report = {}
    for c in json.loads(out.stdout or "{}").get("comments", []):
        rel = Path(c["file"]).resolve().relative_to(ROOT).as_posix()
        report.setdefault(rel, []).append(f"{rel}:{c['line']}: SC{c['code']} {c['message']}")
    return report


class Shellcheck(unittest.TestCase):
    def test_scripts_pass_shellcheck(self):
        self.assertIsNotNone(SHELLCHECK, "shellcheck comes with the dev dependencies (uv sync)")
        paths = [p for p, _ in shell_scripts(templates=False)]
        self.assertGreater(len(paths), 50, "the scan found the repository's scripts")
        report = findings(paths)
        offenders = [line for rel, lines in sorted(report.items()) if rel not in PENDING for line in lines]
        self.assertEqual(offenders, [], "fix these (shellcheck --severity=warning)")
        tracked = {p.relative_to(ROOT).as_posix() for p in paths}
        stale = sorted(rel for rel in PENDING if rel not in report or rel not in tracked)
        self.assertEqual(stale, [], "clean or gone now: drop them from PENDING")


if __name__ == "__main__":
    unittest.main()
