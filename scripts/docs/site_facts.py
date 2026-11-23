#!/usr/bin/env python3
"""The facts the user and admin docs show, from the code and the site config: one data
file each, so no page types a value its source already holds.

    site_facts.py [--config DIR] --public FILE [--private FILE] [--check]

The public file goes to the user docs site, which is published: it carries only the
keys in PUBLIC (a test holds the generator to it), never addresses, secrets or the
site's own limits. The private file (admin docs, in the private config repo) carries
every fact. With --check nothing is written; a file that differs from what would be
written is named and the exit status is 1.

Facts:
  commands  the people's cluster commands (cluster_tools_builtin) and their options,
            read from each command's one definition (cluster_tools.cli Command)
  policy    the cluster-wide periods and fraction every cleanup and warning uses
            (cluster_user_stale_days, cluster_user_grace_days, cluster_user_warn_fraction)
  slurm     KillWait from the slurm.conf template the site renders, and what preemption
            gives a job: GraceTime, which every preempted QoS has equal to KillWait
            (roles/slurm accounting), and the most until SIGKILL (GraceTime + KillWait)
  slack     the channels by purpose and the administrators' group (private)
  quotas    each host's disk_quota_filesystems and disk_quota_inodes_per_tib (private)

A site value set in the config overrides a role default; a value the config computes
with a template is not resolved here and is left out (the private file says which).
"""

import importlib
import json
import re
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "scripts/deepops"))
import cli  # noqa: E402
from repo_files import tracked  # noqa: E402

# The only top-level keys the public file may carry, and under commands and policy the
# only fields: what a user may read on a public site.
PUBLIC = {
    "commands": {"summary", "usage", "options"},
    "policy": {"stale_days", "grace_days", "warn_fraction"},
    "slurm": {"kill_wait_seconds", "preempt_grace_seconds", "sigkill_after_seconds"},
}
POLICY = {
    "stale_days": "cluster_user_stale_days",
    "grace_days": "cluster_user_grace_days",
    "warn_fraction": "cluster_user_warn_fraction",
}


class _Loader(yaml.SafeLoader):
    """YAML with Ansible's tags: a vaulted value reads as None (never decrypted here)."""


_Loader.add_constructor("!vault", lambda loader, node: None)
_Loader.add_constructor("!unsafe", lambda loader, node: loader.construct_scalar(node))


def _yaml(path):
    try:
        return yaml.load(Path(path).read_text(), Loader=_Loader) or {}
    except FileNotFoundError:
        return {}


def _literal(value):
    """VALUE unless it is a template (left to Ansible) or vaulted (None)."""
    if value is None or (isinstance(value, str) and "{{" in value):
        return None
    return value


def settings(config):
    """Every setting the role defaults and the site's group_vars/all.yml and
    group_vars/slurm-cluster.yml give, the site's over the defaults (literal values only)."""
    out = {}
    for defaults in tracked("roles/*/defaults/main.yml"):
        for key, value in _yaml(defaults).items():
            out.setdefault(key, value)
    if config:
        for name in ("all.yml", "slurm-cluster.yml"):
            out.update(_yaml(Path(config) / "group_vars" / name))
    return {k: v for k, v in out.items() if _literal(v) is not None}


def commands(names):
    """{command: {summary, usage, options: [{flags, metavar, help}]}} from each
    command's definition (cluster_tools.<module>.COMMAND)."""
    out = {}
    for name in names:
        module = importlib.import_module(f"cluster_tools.{name.replace('-', '_')}")
        cmd = module.COMMAND
        out[name] = {
            "summary": cmd.summary,
            "usage": cmd.usage,
            "options": [
                {"flags": list(o.flags), "metavar": o.metavar or "", "help": o.help}
                for o in cmd.options
                if not getattr(o, "positional", False)
            ],
        }
    return out


def slurm_values(config):
    """KillWait from the site's slurm.conf template (config/files/slurm/slurm.conf), else
    the role's, and the preemption times that follow from it."""
    site = Path(config) / "files/slurm/slurm.conf" if config else None
    template = site if site and site.exists() else ROOT / "roles/slurm/templates/etc/slurm/slurm.conf"
    m = re.search(r"(?m)^\s*KillWait\s*=\s*(\d+)\s*$", template.read_text())
    if not m:
        return {}
    kill_wait = int(m.group(1))
    return {
        "kill_wait_seconds": kill_wait,
        "preempt_grace_seconds": kill_wait,
        "sigkill_after_seconds": 2 * kill_wait,
    }


def host_quotas(config):
    """{host: {filesystems, inodes_per_tib}} from the site's host_vars (private)."""
    out = {}
    for path in sorted((Path(config) / "host_vars").glob("*")) if config else []:
        data = _yaml(path)
        if "disk_quota_filesystems" in data:
            out[path.name] = {"filesystems": data["disk_quota_filesystems"]}
    return out


def facts(config):
    s = settings(config)
    every = {
        "commands": commands(s.get("cluster_tools_builtin") or []),
        "policy": {field: s.get(key) for field, key in POLICY.items()},
        "slurm": slurm_values(config),
        "slack": {"channels": s.get("slack_channels"), "admin_group": s.get("slack_admin_group")},
        "quotas": {"hosts": host_quotas(config), "inodes_per_tib": s.get("disk_quota_inodes_per_tib")},
    }
    return every


def public(every):
    """The keys and fields of EVERY that PUBLIC allows; nothing else."""
    out = {}
    for key, fields in PUBLIC.items():
        value = every.get(key)
        if key == "commands":
            out[key] = {c: {f: v for f, v in d.items() if f in fields} for c, d in (value or {}).items()}
        else:
            out[key] = {f: v for f, v in (value or {}).items() if f in fields}
    return out


def render(data):
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv=None):
    ap = cli.parser(__doc__)
    ap.add_argument("--config", default=os.environ.get("DEEPOPS_CONFIG_DIR", str(ROOT / "config")))
    ap.add_argument("--public", required=True)
    ap.add_argument("--private")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    config = args.config if Path(args.config).is_dir() else None
    every = facts(config)
    stale = []
    for path, data in ((args.public, public(every)), (args.private, every)):
        if not path:
            continue
        text = render(data)
        if args.check:
            if not Path(path).exists() or Path(path).read_text() != text:
                stale.append(path)
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text)
    for path in stale:
        print(f"{path} is stale: run scripts/docs/site_facts.py and commit it", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
