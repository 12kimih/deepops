"""Run promtool for the monitoring tests.

Uses $PROMTOOL or promtool on PATH; otherwise the promtool inside the Prometheus image
the role deploys (prometheus_container), so tests check against the deployed version
and nobody pins promtool separately. Skips when neither promtool nor docker exists.
"""

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def prometheus_image():
    defaults = (ROOT / "roles/prometheus/defaults/main.yml").read_text()
    return re.search(r'^prometheus_container:\s*"?([^"\s]+)"?', defaults, re.MULTILINE).group(1)


def run(args, cwd, mounts=()):
    """promtool <args> in cwd; mounts are extra directories the args refer to."""
    tool = shutil.which(os.environ.get("PROMTOOL", "promtool"))
    if tool:
        return subprocess.run([tool, *args], cwd=cwd, capture_output=True, text=True, check=False)
    if not shutil.which("docker"):
        raise unittest.SkipTest("promtool not found: set PROMTOOL or install docker")
    dirs = sorted({str(Path(d).resolve()) for d in (cwd, *mounts)})
    binds = [arg for d in dirs for arg in ("--mount", f"type=bind,src={d},dst={d},readonly")]
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-u",
            f"{os.getuid()}:{os.getgid()}",
            # The CPUs scripts/check.sh gives a step (GOMAXPROCS=1), which a container
            # does not inherit: without it promtool took every core.
            *(["--env", "GOMAXPROCS"] if "GOMAXPROCS" in os.environ else []),
            *binds,
            "-w",
            str(Path(cwd).resolve()),
            "--entrypoint",
            "promtool",
            prometheus_image(),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
