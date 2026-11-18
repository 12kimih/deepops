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
from grafana import render

ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = [gpu_nodes, slurm_scheduler, slurm_gpu_allocation]


def main(argv):
    check = "--check" in argv
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
