#!/usr/bin/env python3
"""Check and edit people roster files with the same rules the deploy uses.

Usage (from the deepops directory):
  uv run scripts/deepops/people.py check                 # config.example and config rosters
  uv run scripts/deepops/people.py check FILE [FILE ...]
  uv run scripts/deepops/people.py add FILE LOGIN --account TEAM [--name NAME] [--slack ID]
  uv run scripts/deepops/people.py leave FILE LOGIN

The rules live in plugins/filter/people.py (check), which the playbooks apply through
the people_check filter, so a roster that passes here is one the deploy accepts.

add and leave are the one way the roster is edited, by a person or by the cluster bot
(an account request becomes a pull request of their change): add gives the next free
UID (after the roster's, never a system account's), an account declared in the
Slurm accounting file beside the roster (files/slurm/accounting.yml), keeps the people in order and the
file's comments; leave makes someone `status: left` with no roles or accounts, keeping
the entry so the UID stays reserved. Either writes nothing unless the result passes
check. The deploy (slurm-cluster.yml --tags people) applies the change.
"""

import importlib.util
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(os.environ.get("DEEPOPS_CONFIG_DIR", ROOT / "config"))
spec = importlib.util.spec_from_file_location("people_filter", ROOT / "plugins" / "filter" / "people.py")
people = importlib.util.module_from_spec(spec)
spec.loader.exec_module(people)


def system_ids():
    """The UIDs and GIDs roles/system_accounts reserves."""
    data = yaml.safe_load((ROOT / "roles" / "system_accounts" / "defaults" / "main.yml").read_text())
    accounts = data.get("system_accounts") or {}
    return {a["uid"] for a in accounts.values()} | {a.get("gid", a["uid"]) for a in accounts.values()}


def _load_rt(path):
    from ruamel.yaml import YAML

    text = path.read_text()
    rt = YAML()
    rt.preserve_quotes = True
    rt.indent(mapping=2, sequence=4, offset=2)
    rt.explicit_start = text.lstrip().startswith("---")
    return rt, rt.load(text)


def _save_checked(path, rt, doc):
    import io

    out = io.StringIO()
    rt.dump(doc, out)
    problems = people.check(yaml.safe_load(out.getvalue()))
    if problems:
        for problem in problems:
            print(f"{path}: {problem}", file=sys.stderr)
        return 1
    path.write_text(out.getvalue())
    return 0


def add(path, login, account, name=None, slack=None):
    from ruamel.yaml.comments import CommentedMap

    accounting = path.parent / "files" / "slurm" / "accounting.yml"
    if accounting.is_file():
        declared = (yaml.safe_load(accounting.read_text()) or {}).get("accounts") or {}
        if account not in declared:
            print(f"{path}: account {account} is not declared in {accounting}", file=sys.stderr)
            return 1
    rt, doc = _load_rt(path)
    roster = doc["people"]
    if login in roster:
        print(f"{path}: {login} is already in the roster", file=sys.stderr)
        return 1
    roster_uids = {p["uid"] for p in roster.values() if p and "uid" in p}
    taken = roster_uids | system_ids()
    uid = max(roster_uids | {people.UID_MIN - 1}) + 1
    while uid in taken:
        uid += 1
    entry = CommentedMap(uid=uid)
    if name:
        entry["name"] = name
    if slack:
        entry["slack"] = slack
    entry["accounts"] = CommentedMap({account: CommentedMap()})
    entry["accounts"][account].fa.set_flow_style()
    position = sum(1 for other in roster if str(other) < login)
    roster.insert(position, login, entry)
    status = _save_checked(path, rt, doc)
    if status == 0:
        print(f"added {login} (uid {uid}, account {account})")
    return status


def leave(path, login):
    rt, doc = _load_rt(path)
    entry = doc["people"].get(login)
    if entry is None:
        print(f"{path}: {login} is not in the roster", file=sys.stderr)
        return 1
    for key in ("roles", "accounts", "default_account", "disk_quota"):
        entry.pop(key, None)
    entry["status"] = "left"
    status = _save_checked(path, rt, doc)
    if status == 0:
        print(f"{login} has left (uid {entry['uid']} stays reserved)")
    return status


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
        args = parser.parse_args(argv)
        return leave(args.file, args.login)
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
        problems = people.check(yaml.safe_load(path.read_text()))
        for problem in problems:
            print(f"{path}: {problem}", file=sys.stderr)
        failed |= bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
