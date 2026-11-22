"""A cluster tool's config file, read whole and checked before the tool acts: one way.

A tool that reads a JSON config its role writes defines CONFIG_KEYS (each key it needs,
with its type), CONFIG_OPTIONAL (the keys it may read, with theirs) and
load_config(path), which starts from require() and adds its own checks. A mistake is one
ConfigError line naming the file and the key, never a traceback part-way through a run.

The package and the configs deploy apart: cluster_tools_lib, a dependency of every role
that ships a tool, publishes the whole package wherever any of them runs, so a tool can
change to need a key its own role has not written yet (slurm-usage-report.service failed
every hour after disk-quota.yml published a newer usage_report, 2026-11). Each role
therefore records its tool's config (roles/cluster_tools_lib, tasks_from config:
/etc/cluster-tools/configs.d/<role>.json names the module, the file and the playbook
that writes it), and

    python3 -m cluster_tools.config check MODULE FILE     one file: the role's validate
    python3 -m cluster_tools.config installed [--skip ROLE ...] [--dir DIR]
        every recorded config against this package; each misfit is a line naming the
        playbook that rewrites it, and the exit status is 1
"""

import argparse
import importlib
import json
import sys
from pathlib import Path

RECORDS = Path("/etc/cluster-tools/configs.d")


class ConfigError(ValueError):
    pass


def _kind_name(kind):
    return getattr(kind, "__name__", "number")


def _fits(value, kind):
    # bool is an int in Python; a config's true is never a number.
    return isinstance(value, kind) and (kind is bool or not isinstance(value, bool))


def read(path):
    """The JSON object in PATH ("-": standard input, a preview's config not yet written)."""
    try:
        cfg = json.loads(sys.stdin.read() if str(path) == "-" else Path(path).read_text())
    except OSError as e:
        raise ConfigError(f"{path}: cannot be read: {e.strerror}") from None
    except ValueError as e:
        raise ConfigError(f"{path}: not JSON: {e}") from None
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path}: not a JSON object")
    return cfg


def require(path, keys, optional=None):
    """The config in PATH with every one of KEYS, and each of OPTIONAL it has, of its
    type; a key in neither is refused, since a setting nothing reads does nothing."""
    cfg = read(path)
    optional = optional or {}
    for key, kind in keys.items():
        if not _fits(cfg.get(key), kind):
            raise ConfigError(f"{path}: {key} is missing or not a {_kind_name(kind)}")
    for key, kind in optional.items():
        if key in cfg and not _fits(cfg[key], kind):
            raise ConfigError(f"{path}: {key} is not a {_kind_name(kind)}")
    unknown = sorted(set(cfg) - set(keys) - set(optional) - {"_comment"})
    if unknown:
        raise ConfigError(f"{path}: {', '.join(unknown)}: not a setting of this tool")
    return cfg


def check(module, path):
    """MODULE's load_config on PATH, as the tool itself reads it."""
    importlib.import_module(module).load_config(path)


def installed(records=RECORDS, skip=()):
    """A line for each recorded config this package's tool would refuse."""
    problems = []
    for record_file in sorted(Path(records).glob("*.json")):
        try:
            record = json.loads(record_file.read_text())
            module, path, playbook, role = (
                record["module"],
                record["config"],
                record["playbook"],
                record["role"],
            )
        except (OSError, ValueError, KeyError, TypeError) as e:
            problems.append(f"{record_file}: a damaged record ({e!r}); rerun the playbook of its role")
            continue
        if role in skip or not Path(path).exists():
            continue
        try:
            check(module, path)
        except ConfigError as e:
            problems.append(f"{e}; rerun {playbook}")
        except (ImportError, AttributeError):  # a tool renamed or dropped from the package
            problems.append(f"{path}: {module} is not a tool of this package; rerun {playbook}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="action", required=True)
    one = sub.add_parser("check", help="check one config file with its tool")
    one.add_argument("module")
    one.add_argument("file")
    every = sub.add_parser("installed", help="check every recorded config against this package")
    every.add_argument("--dir", type=Path, default=RECORDS)
    every.add_argument("--skip", action="append", default=[], metavar="ROLE")
    args = ap.parse_args(argv)
    if args.action == "check":
        try:
            check(args.module, args.file)
        except ConfigError as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    problems = installed(args.dir, args.skip)
    for line in problems:
        print(line, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
