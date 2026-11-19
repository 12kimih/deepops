#!/usr/bin/env python3
"""Slurm's own names for node, partition and job states, pending reasons and the
metrics slurmctld serves, taken from the source of the Slurm release deepops pins.

    slurm_names.py          rewrite tests/cluster-tools/slurm-names.json
    slurm_names.py --check  exit 1 when the file is not what the pinned release gives

The cluster tools classify what Slurm's JSON says (roles/cluster_tools_lib, vocab.py).
A word Slurm never sends matches nothing, silently: "MAINT" (sinfo's short form) once
left nodes in a maintenance reservation counted as schedulable, since the JSON says
"MAINTENANCE". The tests compare vocab, and the dashboards' and alert rules' Slurm
metrics, with this file, and the file's release with slurm_version, so a Slurm bump is
not done until this is rerun and they follow.

Sources at the release's tag: the newest data_parser plugin's parsers.c (the JSON's
flag names), src/common/job_state_reason.c (the reason codes) and the metrics/openmetrics
plugin (the metric names, by endpoint). Standard library and
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


# slurmctld's metrics endpoints (src/slurmctld/http.c) by the plugin function that
# fills each one (src/plugins/metrics/openmetrics/openmetrics.c).
METRIC_SETS = {
    "nodes": "nodes",
    "jobs": "jobs",
    "parts": "partitions",
    "sched": "scheduler",
    "ua": "jobs-users-accts",
}


def metric_names(source):
    """{endpoint: sorted metric names} of metrics/openmetrics. ADD_METRIC*(set, type,
    data, name, ...) emits slurm_<name>; the _PFX form, run per user and per account
    for /metrics/jobs-users-accts, slurm_<user|account>_<name>. A metric belongs to the
    endpoint whose metrics_p_parse_* function holds it, or calls the helper that does."""
    functions = [
        (m.start(), m.group(1))
        for m in re.finditer(r"^(?:static|extern) [^\n(]*?\**(\w+)\([^;]*?\n\{", source, re.M)
    ]
    bodies = {name: source[start : (functions[i + 1][0] if i + 1 < len(functions) else len(source))]
              for i, (start, name) in enumerate(functions)}  # fmt: skip

    def endpoint(function, seen=()):
        m = re.fullmatch(r"metrics_p_parse_(\w+)_metrics", function)
        if m:
            return METRIC_SETS[m.group(1)]
        callers = [
            n
            for n, body in bodies.items()
            if n != function and n not in seen and re.search(rf"\b{function}\b", body)
        ]
        found = {endpoint(c, (*seen, function)) for c in callers} - {None}
        if len(found) > 1:
            raise RuntimeError(f"{function} serves several endpoints: {sorted(found)}")
        return found.pop() if found else None

    sets = {e: set() for e in METRIC_SETS.values()}
    prefixes = re.findall(r'args\.pfx = "(\w+)"', source)
    for m in re.finditer(
        r"^\s*ADD_METRIC(_KEYVAL_PFX|_KEYVAL|)\(set, \w+, [^,]+, (?:pfx, )?(\w+)", source, re.M
    ):
        holder = [name for start, name in functions if start < m.start()][-1]
        target = endpoint(holder)
        if target is None:
            raise RuntimeError(f"metric {m.group(2)} in {holder}, which no endpoint reaches")
        if m.group(1) == "_KEYVAL_PFX":
            sets[target].update(f"slurm_{p}_{m.group(2)}" for p in prefixes)
        else:
            sets[target].add("slurm_" + m.group(2))
    if not all(sets.values()):
        raise RuntimeError(f"no metrics found for {[e for e, n in sets.items() if not n]}")
    return {e: sorted(n) for e, n in sets.items()}


def names(version):
    tag = "slurm-" + version.replace(".", "-") + "-1"
    parser, parsers = newest_parser(tag)
    reasons = re.findall(r'\.str = "([^"]+)"', fetch(f"{RAW}/{tag}/src/common/job_state_reason.c"))
    metrics = metric_names(fetch(f"{RAW}/{tag}/src/plugins/metrics/openmetrics/openmetrics.c"))
    return {
        "slurm_version": version,
        "source": f"SchedMD/slurm {tag}: src/plugins/data_parser/{parser}/parsers.c, src/common/job_state_reason.c,"
        " src/plugins/metrics/openmetrics/openmetrics.c",
        "node_states": flag_names(parsers, "NODE_STATES"),
        "partition_states": flag_names(parsers, "PARTITION_STATES"),
        "job_states": flag_names(parsers, "JOB_STATE"),
        "job_reasons": reasons,
        "metrics": metrics,
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
