#!/usr/bin/env python3
"""Write the dashboards deepops builds (scripts/dashboards/*.py) to the files the roles
provision. The JSON is generated; edit the Python, then run this.

Usage: build.py [--check]   (--check reports stale files and exits 1)
"""

import sys
from pathlib import Path

import gpu_nodes
import slurm_gpu_allocation
import slurm_scheduler
import storage_io
from grafana import render

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deepops"))
import cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = [gpu_nodes, slurm_scheduler, slurm_gpu_allocation, storage_io]


def main(argv):
    parser = cli.parser(__doc__)
    parser.add_argument("--check", action="store_true", help="report stale files and exit 1, writing nothing")
    check = parser.parse_args(argv).check
    stale = []
    for module in DASHBOARDS:
        path = ROOT / module.PATH
        text = render(module.build())
        if path.exists() and path.read_text() == text:
            continue
        if check:
            stale.append(module.PATH)
        else:
            path.write_text(text)
            print(f"wrote {module.PATH}")
    for path in stale:
        print(f"stale: {path} (run scripts/dashboards/build.py)")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
