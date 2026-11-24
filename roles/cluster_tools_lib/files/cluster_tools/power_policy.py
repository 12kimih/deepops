"""power-policy: the host's power management held at full performance.

    power-policy apply CONFIG.json   set every setting found off; write the metrics
    power-policy check CONFIG.json   list the settings found off; set nothing

A compute host wants every device at speed, all the time: the CPU governor, its
hardware hint (EPP), turbo, the PCIe and SATA link power states. Each setting is a
sysfs file glob and the value it must hold; a file the host does not have (no SATA
host, another CPU frequency driver) is no setting there. A choice file that lists every
option with the chosen one in brackets ("[default] performance") holds the bracketed
one. Run at boot before slurmd and again on a timer, apply puts back what anything else
changed (a job, a package, a firmware event), and says so.

Metrics, one series per setting (its glob):
  power_policy_files        files the glob matched
  power_policy_off          of them, found off at this run (apply set them)
  power_policy_refused      of them, the kernel would not set (firmware owns it)
  power_policy_last_success_timestamp_seconds
An apply exits 1 when the kernel refused one; a check when one is off.

CONFIG: sys_root (the sysfs root, /sys), textfile, settings: [{path (a glob under
sys_root), value}]. Python 3.9 or newer, standard library only.
"""

import glob
import os
import re
import sys
import time

from . import config
from .files import write_textfile
from .prom import series

CONFIG_KEYS = {"sys_root": str, "textfile": str, "settings": list}
CHOSEN = re.compile(r"\[([^]]+)\]")


def load_config(path):
    cfg = config.require(path, CONFIG_KEYS)
    for i, setting in enumerate(cfg["settings"]):
        where = f"{path}: settings[{i}]"
        if not isinstance(setting, dict) or set(setting) != {"path", "value"}:
            raise config.ConfigError(f"{where}: needs exactly path and value")
        if not all(isinstance(setting[k], str) and setting[k] for k in ("path", "value")):
            raise config.ConfigError(f"{where}: path and value are non-empty strings")
        if setting["path"].startswith("/") or ".." in setting["path"].split("/"):
            raise config.ConfigError(f"{where}: path is relative to sys_root, without ..")
    return cfg


def held(path):
    """The value PATH holds: a choice file's bracketed option, else its text."""
    with open(path) as f:
        text = f.read().strip()
    chosen = CHOSEN.search(text)
    return chosen.group(1) if chosen else text


def run(cfg, fix, out=sys.stdout):
    """Each setting's files, off and refused counts; with FIX, the off ones are set."""
    rows = []
    for setting in cfg["settings"]:
        want, files, off, refused = setting["value"], 0, 0, 0
        for path in sorted(glob.glob(os.path.join(cfg["sys_root"], setting["path"]))):
            files += 1
            try:
                have = held(path)
            except OSError as e:
                print(f"power-policy: {path}: {e.strerror}", file=sys.stderr)
                refused += 1
                continue
            if have == want:
                continue
            off += 1
            if not fix:
                print(f"{path}: {have}, wants {want}", file=out)
                continue
            try:
                with open(path, "w") as f:
                    f.write(want)
                now_held = held(path)
            except OSError as e:
                now_held = f"{have} ({e.strerror})"
            if now_held == want:
                print(f"{path}: {have} -> {want}", file=out)
            else:
                print(f"power-policy: {path}: the kernel keeps {now_held}, not {want}", file=sys.stderr)
                refused += 1
        rows.append((setting["path"], files, off, refused))
    return rows


def textfile(rows, now):
    lines = []
    for name, help_text, column in (
        ("power_policy_files", "Files the setting's glob matched.", 1),
        ("power_policy_off", "Files found off the setting at the last run.", 2),
        ("power_policy_refused", "Files the kernel would not set to the setting.", 3),
    ):
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
        lines += [series(name, {"setting": row[0]}, row[column]) for row in rows]
    lines += [
        "# HELP power_policy_last_success_timestamp_seconds When an apply last ran through every setting.",
        "# TYPE power_policy_last_success_timestamp_seconds gauge",
        series("power_policy_last_success_timestamp_seconds", {}, int(now)),
    ]
    return "\n".join(lines) + "\n"


def apply(cfg, out=sys.stdout, now=None):
    rows = run(cfg, fix=True, out=out)
    write_textfile(cfg["textfile"], textfile(rows, time.time() if now is None else now))
    return int(any(row[3] for row in rows))


def check(cfg, out=sys.stdout):
    rows = run(cfg, fix=False, out=out)
    return int(any(row[2] or row[3] for row in rows))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    commands = {"apply": apply, "check": check}
    if len(argv) != 2 or argv[0] not in commands:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        cfg = load_config(argv[1])
    except config.ConfigError as e:
        print(f"power-policy: {e}", file=sys.stderr)
        return 2
    return commands[argv[0]](cfg)


if __name__ == "__main__":
    sys.exit(main())
