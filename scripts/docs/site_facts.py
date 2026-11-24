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
            (cluster_user_stale_days, cluster_user_grace_days, cluster_user_warn_fraction),
            and the idle-GPU rules (slurm_usage_report_idle_hours, _cancel_after_hours,
            _cancel_warn_hours; cancel_after_hours 0 means no cancel)
  slurm     KillWait from the slurm.conf template the site renders, and what preemption
            gives a job: GraceTime, which every preempted QoS has equal to KillWait
            (roles/slurm accounting), and the most until SIGKILL (GraceTime + KillWait)
  quota     each counted filesystem with limits (the hosts' disk_quota_filesystems):
            block_hard and its warn level in TiB, inode_hard (its own, or block_hard's TiB
            times disk_quota_inodes_per_tib) and its warn level (cluster_user_warn_fraction)
  dates     the site's announced dates (config schedule.yml, name: YYYY-MM-DD), so a
            page states each once; only date values pass
  environment
            the users' environment (user_environment over user_environment_defaults,
            roles/user_environment_defaults), each {name, login, job} as the profile.d
            files and the task prolog get it (plugins/filter/user_environment.py); public
            only an entry whose values are paths or URLs. A value derived from other
            settings (SHARED_HF_HUB_CACHE: model_cache_hub) is resolved as Ansible would
  cluster   the nodes (CPUs, memory, GPUs) and partitions (their nodes, time limit) the
            site's slurm.conf template lists (private: the portal's overview page)
  slack     the channels by purpose and the administrators' group (private)
  quotas    each host's disk_quota_filesystems and disk_quota_inodes_per_tib (private)

A site value set in the config overrides a role default. A template is rendered (Jinja,
over the settings already known, until nothing more resolves); one that needs what only
a host knows (groups, facts, an Ansible filter) is left out (the private file says which).
"""

import importlib
import importlib.util
import json
import re
import os
import sys
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "scripts/deepops"))
import cli  # noqa: E402
from repo_files import tracked  # noqa: E402

# The one function every reader of the users' environment uses, loaded from its file:
# plugins/filter on sys.path would shadow the standard library with its secrets.py.
_spec = importlib.util.spec_from_file_location(
    "user_environment", ROOT / "plugins/filter/user_environment.py"
)
_user_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_user_env)

# The only top-level keys the public file may carry, and under commands and policy the
# only fields: what a user may read on a public site.
PUBLIC = {
    "commands": {"summary", "usage", "options"},
    "policy": {
        "stale_days",
        "grace_days",
        "warn_fraction",
        "idle_hours",
        "cancel_after_hours",
        "cancel_warn_hours",
    },
    "slurm": {"kill_wait_seconds", "preempt_grace_seconds", "sigkill_after_seconds"},
    "dates": None,  # any name the site's schedule gives, its value a date
    "quota": None,  # each limited filesystem, its fields QUOTA_FIELDS
    "environment": {"name", "login", "job"},  # only entries whose values are paths or URLs
}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
QUOTA_FIELDS = {"block_hard_tib", "block_warn_tib", "inode_hard", "inode_warn"}
_TIB = {"K": 1 / 1024**3, "M": 1 / 1024**2, "G": 1 / 1024, "T": 1}
POLICY = {
    "stale_days": "cluster_user_stale_days",
    "grace_days": "cluster_user_grace_days",
    "warn_fraction": "cluster_user_warn_fraction",
    # The idle-GPU rules (roles/slurm_usage_report): idle after idle_hours, a job cancelled
    # after cancel_after_hours (0: never, the docs say not active), warned cancel_warn_hours
    # before.
    "idle_hours": "slurm_usage_report_idle_hours",
    "cancel_after_hours": "slurm_usage_report_cancel_after_hours",
    "cancel_warn_hours": "slurm_usage_report_cancel_warn_hours",
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
    group_vars/slurm-cluster.yml give, the site's over the defaults: the literal ones,
    and each template string that renders from them (repeated while one more does)."""
    out = {}
    for defaults in tracked("roles/*/defaults/main.yml"):
        for key, value in _yaml(defaults).items():
            out.setdefault(key, value)
    if config:
        for name in ("all.yml", "slurm-cluster.yml"):
            out.update(_yaml(Path(config) / "group_vars" / name))
    known = {k: v for k, v in out.items() if _literal(v) is not None}
    pending = {k: v for k, v in out.items() if k not in known and isinstance(v, str)}
    while pending:
        done = {k: r for k, r in ((k, _render(v, known)) for k, v in pending.items()) if r is not None}
        if not done:
            break
        known.update(done)
        pending = {k: v for k, v in pending.items() if k not in done}
    return known


# Ansible's templating as far as the settings alone go: an undefined name, or a filter or
# test only Ansible has, leaves the template unresolved.
_JINJA = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)  # noqa: S701 (not HTML)


def _render(text, values):
    """TEXT rendered over VALUES, or None when it needs anything they lack."""
    try:
        return _JINJA.from_string(text).render(values)
    except (jinja2.TemplateError, TypeError, ValueError, AttributeError):
        return None


def _resolve(value, settings_):
    """VALUE rendered over the settings; None when a template stays (Ansible's to
    resolve). Dicts are resolved field by field."""
    if isinstance(value, dict):
        out = {k: _resolve(v, settings_) for k, v in value.items()}
        return None if any(v is None and value[k] is not None for k, v in out.items()) else out
    if not isinstance(value, str) or ("{{" not in value and "{%" not in value):
        return value
    return _render(value, settings_)


def environment(config):
    """[{name, login, job}] from user_environment over user_environment_defaults, as
    the filter gives the hosts; an entry whose value stays a template is left out."""
    raw = {}
    for defaults in tracked("roles/*/defaults/main.yml"):
        data = _yaml(defaults)
        for key in ("user_environment_defaults", "user_environment"):
            if key in data:
                raw.setdefault(key, data[key])
    every = dict(raw)
    if config:
        for name in ("all.yml", "slurm-cluster.yml"):
            every.update(_yaml(Path(config) / "group_vars" / name))
    literal = settings(config)
    resolved = {}
    for key in ("user_environment_defaults", "user_environment"):
        resolved[key] = {}
        for name, value in (every.get(key) or {}).items():
            value = _resolve(value, literal)
            if value is not None or (every.get(key) or {}).get(name) is None:
                resolved[key][name] = value
    return _user_env.user_environment(resolved["user_environment_defaults"], resolved["user_environment"])


def _path_or_url(value):
    return value == "" or value.startswith(("/", "~/")) or "://" in value


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


def _slurm_conf(config):
    site = Path(config) / "files/slurm/slurm.conf" if config else None
    return site if site and site.exists() else ROOT / "roles/slurm/templates/etc/slurm/slurm.conf"


def _pairs(line):
    """KEY=VALUE words of a slurm.conf line, templates left out."""
    line = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", line)
    return dict(w.split("=", 1) for w in line.split() if "=" in w)


def cluster(config):
    """{nodes: [{name, cpus, memory_mb, gres}], partitions: [{name, nodes, max_time}]} from
    the literal NodeName and PartitionName lines of the site's slurm.conf template."""
    nodes, partitions = [], []
    for line in _slurm_conf(config).read_text().splitlines():
        if line.startswith("NodeName=") and "{%" not in line.split()[0]:
            kv = _pairs(line)
            nodes.append({"name": kv["NodeName"], "cpus": int(kv.get("CPUs", 0) or 0),
                          "memory_mb": int(kv.get("RealMemory", 0) or 0), "gres": kv.get("Gres", "")})  # fmt: skip
        elif line.startswith("PartitionName="):
            kv = _pairs(line)
            partitions.append(
                {"name": kv["PartitionName"], "nodes": kv.get("Nodes", ""), "max_time": kv.get("MaxTime", "")}
            )
    return {"nodes": nodes, "partitions": partitions}


def slurm_values(config):
    """KillWait from the site's slurm.conf template (config/files/slurm/slurm.conf), else
    the role's, and the preemption times that follow from it."""
    template = _slurm_conf(config)
    m = re.search(r"(?m)^\s*KillWait\s*=\s*(\d+)\s*$", template.read_text())
    if not m:
        return {}
    kill_wait = int(m.group(1))
    return {
        "kill_wait_seconds": kill_wait,
        "preempt_grace_seconds": kill_wait,
        "sigkill_after_seconds": 2 * kill_wait,
    }


def schedule(config):
    """{name: "YYYY-MM-DD"} from the site's schedule.yml; anything not a date is refused."""
    out = {}
    for name, value in (_yaml(Path(config) / "schedule.yml") if config else {}).items():
        text = str(value)
        if not DATE.fullmatch(text):
            raise SystemExit(f"schedule.yml: {name}: {value!r} is not a date (YYYY-MM-DD)")
        out[str(name)] = text
    return out


def quota_limits(config, settings_):
    """{mount: {block_hard_tib, block_warn_tib, inode_hard, inode_warn}} for every counted
    filesystem whose site limits (disk_quota_filesystems in the hosts' host_vars) set a
    block_hard; derived as roles/disk_quota derives them."""
    ratio = int(settings_.get("disk_quota_inodes_per_tib") or 0)
    fraction = float(settings_.get("cluster_user_warn_fraction") or 0)
    out = {}
    for host in host_quotas(config).values():
        for mount, lim in (host["filesystems"] or {}).items():
            hard = str((lim or {}).get("block_hard", ""))
            m = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGT])", hard)
            if not m:
                continue
            tib = float(m.group(1)) * _TIB[m.group(2)]
            inodes = int(lim["inode_hard"]) if "inode_hard" in lim else int(tib * ratio)
            out[mount] = {
                "block_hard_tib": round(tib, 3),
                "block_warn_tib": round(tib * fraction, 3),
                "inode_hard": inodes,
                "inode_warn": int(inodes * fraction),
            }
    return out


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
        "dates": schedule(config),
        "quota": quota_limits(config, s),
        "environment": environment(config),
        "cluster": cluster(config),
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
        elif key == "environment":
            out[key] = [
                {f: e[f] for f in fields}
                for e in value or []
                if _path_or_url(e["login"]) and _path_or_url(e["job"])
            ]
        elif key == "quota":
            out[key] = {
                m: {f: v for f, v in d.items() if f in QUOTA_FIELDS} for m, d in (value or {}).items()
            }
        elif fields is None:  # names the site chooses; the values are checked by kind
            out[key] = {f: v for f, v in (value or {}).items() if isinstance(v, str) and DATE.fullmatch(v)}
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
