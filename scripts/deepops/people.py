#!/usr/bin/env python3
"""Validate people roster files with the same check the deploy uses.

Usage (from the deepops directory):
  uv run scripts/deepops/people.py check                 # config.example and config rosters
  uv run scripts/deepops/people.py check FILE [FILE ...]

The rules live in plugins/filter/people.py (check), which the playbooks apply through
the people_check filter, so a roster that passes here is one the deploy accepts.
"""

import argparse
import importlib.util
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(os.environ.get("DEEPOPS_CONFIG_DIR", ROOT / "config"))
spec = importlib.util.spec_from_file_location("people_filter", ROOT / "plugins" / "filter" / "people.py")
people = importlib.util.module_from_spec(spec)
spec.loader.exec_module(people)


def default_files():
    return [p for p in (ROOT / "config.example" / "people.yml", CONFIG / "people.yml") if p.is_file()]


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check"])
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)
    failed = False
    for path in args.files or default_files():
        problems = people.check(yaml.safe_load(path.read_text()))
        for problem in problems:
            print(f"{path}: {problem}", file=sys.stderr)
        failed |= bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
