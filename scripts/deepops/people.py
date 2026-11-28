#!/usr/bin/env python3
"""Check and edit people roster files with the same rules the deploy uses.

Usage (from the deepops directory):
  uv run scripts/deepops/people.py check                 # config.example and config rosters
  uv run scripts/deepops/people.py check FILE [FILE ...]
  uv run scripts/deepops/people.py add FILE LOGIN --account TEAM [--name NAME] [--slack ID]
  uv run scripts/deepops/people.py leave FILE LOGIN [--ends YYYY-MM-DD]

The rules live in plugins/filter/people.py (check), which the playbooks apply through
the people_check filter, so a roster that passes here is one the deploy accepts.

add and leave are the one way the roster is edited, by a person or by the cluster bot
(an account request becomes a pull request of their change): add gives the next free
UID (after the roster's, never a system account's), an account declared in the
Slurm accounting file (slurm_accounting_file in the config), keeps the people in order and the
file's comments; leave --ends sets someone's last day (they keep their account for the
grace period, people_leave_grace_days), and leave alone makes them `status: left` at
once with no roles or accounts, keeping the entry so the UID stays reserved. Either writes nothing unless the result passes
check. The deploy (slurm-cluster.yml --tags people) applies the change.
"""

import importlib.util
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
import yaml_load  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(os.environ.get("DEEPOPS_CONFIG_DIR", ROOT / "config"))
spec = importlib.util.spec_from_file_location("people_filter", ROOT / "plugins" / "filter" / "people.py")
people = importlib.util.module_from_spec(spec)
spec.loader.exec_module(people)


def system_ids():
    """The UIDs and GIDs roles/system_accounts reserves."""
    data = yaml_load.load((ROOT / "roles" / "system_accounts" / "defaults" / "main.yml").read_text())
    accounts = data.get("system_accounts") or {}
    return {a["uid"] for a in accounts.values()} | {a.get("gid", a["uid"]) for a in accounts.values()}


def edit(path, change):
    """Write CHANGE(text) to PATH; a refusal is printed and nothing is written."""
    try:
        result = change(path.read_text())
    except people.EditError as e:
        for line in str(e).splitlines():
            print(f"{path}: {line}", file=sys.stderr)
        return None
    text = result[0] if isinstance(result, tuple) else result
    path.write_text(text)
    return result


def accounting_file(config):
    """The site's accounting file (slurm_accounting_file in CONFIG's group_vars, rendered
    as Ansible does with CONFIG as inventory_dir), or None when the site manages none."""
    import jinja2

    value = ""
    for name in ("all.yml", "slurm-cluster.yml"):
        path = config / "group_vars" / name
        if path.is_file():
            value = (yaml_load.load(path.read_text()) or {}).get("slurm_accounting_file", value)
    if not value:
        return None
    rendered = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(value)  # noqa: S701
    return Path(rendered.render(inventory_dir=str(config)))


def add(path, login, account, name=None, slack=None):
    accounting = accounting_file(path.parent)
    declared = None
    if accounting is not None and accounting.is_file():
        declared = (yaml_load.load(accounting.read_text()) or {}).get("accounts") or {}
    done = edit(path, lambda t: people.roster_add(t, login, account, name, slack, system_ids(), declared))
    if done is None:
        return 1
    print(f"added {login} (uid {done[1]}, account {account})")
    return 0


def leave(path, login, ends=None):
    if edit(path, lambda t: people.roster_leave(t, login, ends)) is None:
        return 1
    print(f"{login} ends on {ends}" if ends else f"{login} has left (the UID stays reserved)")
    return 0


def default_files():
    return [p for p in (ROOT / "config.example" / "people.yml", CONFIG / "people.yml") if p.is_file()]


def main(argv):
    if argv[:1] in (["add"], ["leave"]):
        parser = cli.parser(__doc__)
        parser.add_argument("command")
        parser.add_argument("file", type=Path)
        parser.add_argument("login")
        if argv[0] == "add":
            parser.add_argument("--account", required=True)
            parser.add_argument("--name")
            parser.add_argument("--slack")
            args = parser.parse_args(argv)
            return add(args.file, args.login, args.account, args.name, args.slack)
        parser.add_argument("--ends")
        args = parser.parse_args(argv)
        return leave(args.file, args.login, args.ends)
    parser = cli.parser(__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)
    files = args.files or default_files()
    if not files:
        print("no roster to check: pass FILE, or create config.example/people.yml", file=sys.stderr)
        return 1
    failed = False
    for path in files:
        problems = people.check(yaml_load.load(path.read_text()))
        for problem in problems:
            print(f"{path}: {problem}", file=sys.stderr)
        failed |= bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
