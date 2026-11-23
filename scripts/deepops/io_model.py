#!/usr/bin/env python3
"""io_model: the iocost model and latency targets of a device, from one fio run.

    scripts/deepops/io_model.py RESULT.json MOUNT [--command "..."]

RESULT.json is `fio --output-format=json` of scripts/deepops/io-model.fio, run as root
on the host: six patterns on a scratch file larger than the device's cache, each for a
minute, O_DIRECT: throughput at 1M (seq-read, seq-write) and
IOPS at 4k, sequential (seq-read-4k, seq-write-4k) and random (rand-read, rand-write).
It prints the config entry io_fairness_models[MOUNT] (YAML), with when the run ended
(local time and offset) and the command, so what the kernel is told always says where it came from:

  model  the linear model's coefficients (io.cost.model ctrl=user): rbps, rseqiops,
         rrandiops, wbps, wseqiops, wrandiops;
  qos    the latency targets (io.cost.qos): 95% of reads and writes within twice the
         p95 completion latency the run measured for random I/O, vrate between 50% and
         150%, so iocost scales the model by the device's real latency;
  latency  each pattern's p50/p95/p99 in milliseconds, for the record.

A pattern missing, failed, or measured without O_DIRECT is refused: the model would be
the page cache's. Python 3.9 or newer, standard library only.
"""

import datetime
import json
import sys

import cli

PATTERNS = ("seq-read", "seq-write", "seq-read-4k", "seq-write-4k", "rand-read", "rand-write")
# What each coefficient is measured by: (pattern, side, field).
MODEL = {
    "rbps": ("seq-read", "read", "bw_bytes"),
    "rseqiops": ("seq-read-4k", "read", "iops"),
    "rrandiops": ("rand-read", "read", "iops"),
    "wbps": ("seq-write", "write", "bw_bytes"),
    "wseqiops": ("seq-write-4k", "write", "iops"),
    "wrandiops": ("rand-write", "write", "iops"),
}
PERCENTILES = {"p50": "50.000000", "p95": "95.000000", "p99": "99.000000"}


class Refused(ValueError):
    """A result the model must not be made from; str() says why."""


def side_of(pattern):
    return "write" if "write" in pattern else "read"


def entry(result, command=""):
    """The io_fairness_models entry of RESULT, a parsed fio JSON."""
    jobs = {j["jobname"]: j for j in result.get("jobs", [])}
    missing = [p for p in PATTERNS if p not in jobs]
    if missing:
        raise Refused(f"patterns missing: {', '.join(missing)}")
    failed = [p for p in PATTERNS if jobs[p].get("error")]
    if failed:
        raise Refused(f"patterns failed: {', '.join(failed)}")
    if str(result.get("global options", {}).get("direct", "0")) != "1":
        raise Refused("not run with direct=1: that measures the page cache, not the device")
    latency = {}
    for pattern in PATTERNS:
        percentiles = jobs[pattern][side_of(pattern)].get("clat_ns", {}).get("percentile") or {}
        if not all(key in percentiles for key in PERCENTILES.values()):
            raise Refused(f"{pattern}: no p50/p95/p99 (percentile_list=50:95:99)")
        latency[pattern] = {name: round(percentiles[key] / 1e6, 3) for name, key in PERCENTILES.items()}
    model = {}
    for name, (pattern, side, field) in MODEL.items():
        value = int(jobs[pattern][side][field])
        if value <= 0:
            raise Refused(f"{name}: {pattern} measured nothing")
        model[name] = value

    def target(pattern):  # microseconds, twice the measured p95
        return int(2 * jobs[pattern][side_of(pattern)]["clat_ns"]["percentile"][PERCENTILES["p95"]] / 1000)

    qos = {
        "rpct": 95,
        "rlat": target("rand-read"),
        "wpct": 95,
        "wlat": target("rand-write"),
        "min": 50,
        "max": 150,
    }
    # When the run ended, in the host's local time with its offset: a UTC date alone named
    # the day before for a run just after local midnight.
    measured = datetime.datetime.fromtimestamp(result.get("timestamp", 0)).astimezone()
    return {
        "measured": measured.isoformat(timespec="minutes"),
        "command": command,
        "model": model,
        "qos": qos,
        "latency": latency,
    }


def as_yaml(mount, item):
    """The config block, in the shape config/group_vars holds it."""
    lines = ["io_fairness_models:", f"  {mount}:", f"    measured: {json.dumps(item['measured'])}",
             f"    command: {json.dumps(item['command'])}"]  # fmt: skip
    for key in ("model", "qos"):
        values = ", ".join(f"{k}: {v}" for k, v in item[key].items())
        lines.append(f"    {key}: {{{values}}}")
    lines.append("    latency:  # ms, for the record")
    for pattern, values in item["latency"].items():
        lines.append(f"      {pattern}: {{" + ", ".join(f"{k}: {v}" for k, v in values.items()) + "}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = cli.parser(__doc__)
    parser.add_argument("result", help="fio --output-format=json of the io_fairness job")
    parser.add_argument("mount", help="the filesystem it measured, e.g. /home")
    parser.add_argument("--command", default="", help="the command that ran it, recorded beside the model")
    args = parser.parse_args(argv)
    try:
        with open(args.result) as f:
            result = json.load(f)
        sys.stdout.write(as_yaml(args.mount, entry(result, args.command)))
    except (OSError, ValueError) as e:  # Refused is a ValueError
        print(f"io_model: {args.result}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
