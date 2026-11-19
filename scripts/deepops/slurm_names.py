#!/usr/bin/env python3
"""Slurm's own names for node, partition and job states and pending reasons, taken from
the source of the Slurm release deepops pins.

    slurm_names.py          rewrite tests/cluster-tools/slurm-names.json
    slurm_names.py --check  exit 1 when the file is not what the pinned release gives

The cluster tools classify what Slurm's JSON says (roles/cluster_tools_lib, vocab.py).
A word Slurm never sends matches nothing, silently: "MAINT" (sinfo's short form) once
left nodes in a maintenance reservation counted as schedulable, since the JSON says
"MAINTENANCE". The tests compare vocab with this file, and the file's release with
slurm_version, so a Slurm bump is not done until this is rerun and vocab follows.

Sources at the release's tag: the newest data_parser plugin's parsers.c (the JSON's
flag names) and src/common/job_state_reason.c (the reason codes). Standard library and
PyYAML only.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests/cluster-tools/slurm-names.json"
RAW = "https://raw.githubusercontent.com/SchedMD/slurm"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "deepops-slurm-names"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode()


def pinned_version():
    return str(yaml.safe_load((ROOT / "roles/slurm/defaults/main.yml").read_text())["slurm_version"])


def newest_parser(tag):
    """(version, parsers.c text) of the newest data_parser plugin in the release."""
    minor, found = 40, None
    while True:
        try:
            text = fetch(f"{RAW}/{tag}/src/plugins/data_parser/v0.0.{minor}/parsers.c")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            if found:
                return found
        else:
            found = (f"v0.0.{minor}", text)
        minor += 1
        if minor > 99:
            raise RuntimeError(f"no data_parser plugin found in {tag}")


def flag_names(parsers, array):
    """The names in PARSER_FLAG_ARRAY(ARRAY), in Slurm's order."""
    m = re.search(rf"PARSER_FLAG_ARRAY\({array}\)\[\] = \{{(.*?)\n\}};", parsers, re.S)
    if not m:
        raise RuntimeError(f"PARSER_FLAG_ARRAY({array}) not found")
    # add_flag_eq(JOB_PENDING, JOB_STATE_BASE, "PENDING", false, "description"): the
    # first string of each entry is the name; a hidden entry (END) is never sent.
    return re.findall(r'\badd_flag(?!_hidden)\w*\([^"]*"([A-Z_]+)"', m.group(1))


def names(version):
    tag = "slurm-" + version.replace(".", "-") + "-1"
    parser, parsers = newest_parser(tag)
    reasons = re.findall(r'\.str = "([^"]+)"', fetch(f"{RAW}/{tag}/src/common/job_state_reason.c"))
    return {
        "slurm_version": version,
        "source": f"SchedMD/slurm {tag}: src/plugins/data_parser/{parser}/parsers.c, src/common/job_state_reason.c",
        "node_states": flag_names(parsers, "NODE_STATES"),
        "partition_states": flag_names(parsers, "PARTITION_STATES"),
        "job_states": flag_names(parsers, "JOB_STATE"),
        "job_reasons": reasons,
    }


def main(argv):
    text = json.dumps(names(pinned_version()), indent=1) + "\n"
    if "--check" in argv:
        if not OUT.is_file() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is stale: run scripts/deepops/slurm_names.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
