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
  policy    the cluster-wide rules the pages state, each field from the setting POLICY
            names (periods, fractions, the idle-GPU rules, how people are told, login
            grace, session QoS, submit holds); the pattern of a QoS stays private
  services  what the cluster runs that the pages describe, so none says a service is coming
            or missing: portal (slurm_install_open_ondemand), its overview page's menu title
            (open_ondemand_overview_title, "" without one), the menu titles of the services
            it publishes (open_ondemand_proxied_services, as Grafana), dashboard
            (slurm_enable_monitoring), bot (slurm_enable_cluster_bot: the /cluster command),
            job_notices (slurm_enable_job_notices: --mail-type is a Slack DM to the job's
            owner), the portal's own menu groups in order (portal_groups:
            open_ondemand_nav_groups, OOD's names), the folders its file browser opens
            (portal_files: open_ondemand_allowlist_paths) and the dashboards Grafana
            shows (dashboards: [{uid, title}] of every set in grafana_dashboard_sets, read
            from each src/dashboards file); never an address
  paths     the shared trees people use: scratch (scratch_space_root), software
            (sm_prefix), shared (shared_space_root) and its data folders (shared_space_data)
  login     each person's limits on a login node (cluster_tools_user_slice_limits, the
            slurm-login group's over the others): cpus (CPUQuota), memory_gib
            (MemoryMax), tasks (TasksMax); a limit not set is left out; and private_dirs,
            the directories each login has its own of (login_private_dirs_paths), only
            while slurm_enable_login_private_dirs is on
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
  chat      the Slack channels by purpose (slack_channels, {role: name}) and the
            administrators' group handle (slack_admin_group): names people see in Slack
            anyway; the group's ID stays private
  bot       the cluster bot's slash command (cluster_bot_command) and its subcommands
            with their Korean help, in order (cluster_bot.commands SUBCOMMANDS)
  reports   when the status channel's reports run (systemd OnCalendar):
            usage (slurm_usage_report_schedule), weekly, disk
  schedules when the cluster's other timers that people meet run (OnCalendar):
            scratch_purge (scratch_space_purge_on_calendar: the purge list and cleanup)
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
import jinja2.nativetypes
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

# The submit policy's switches (slurm_job_submit_policy, roles/slurm's job_submit.lua),
# each a policy fact of its name: what the job rules on the user pages say holds.
SUBMIT_RULES = ("strict_gpu_type", "force_gpu_partition", "gpu_jobs_use_defaults", "gpu_nodes_shared")

# The only top-level keys the public file may carry, and under commands and policy the
# only fields: what a user may read on a public site.
PUBLIC = {
    "commands": {"summary", "summary_ko", "usage", "options"},
    "policy": {
        "stale_days",
        "grace_days",
        "warn_fraction",
        "idle_hours",
        "cancel_after_hours",
        "cancel_warn_hours",
        "idle_notify",
        "leave_grace_days",
        "interactive_team_qos",
        "submit_holds_disk",
        "gpus_per_node",
        "gpu_type_defaulted",
        *SUBMIT_RULES,
    },
    "login": {"cpus", "memory_gib", "tasks", "private_dirs"},
    "services": {
        "portal",
        "overview",
        "menu",
        "dashboard",
        "bot",
        "job_notices",
        "portal_groups",
        "portal_files",
        "dashboards",
    },
    "paths": {"scratch", "software", "shared", "shared_data"},
    "slurm": {"kill_wait_seconds", "preempt_grace_seconds", "sigkill_after_seconds"},
    "dates": None,  # any name the site's schedule gives, its value a date
    "quota": None,  # each limited filesystem, its fields QUOTA_FIELDS
    "environment": {"name", "login", "job"},  # only entries whose values are paths or URLs
    "chat": {"channels", "admin_group"},
    "bot": {"command", "subcommands"},
    "reports": {"usage", "weekly", "disk"},
    "schedules": {"scratch_purge"},
}
REPORTS = {
    "usage": "slurm_usage_report_schedule",
    "weekly": "slurm_usage_report_weekly_schedule",
    "disk": "slurm_usage_report_disk_schedule",
}
SCHEDULES = {"scratch_purge": "scratch_space_purge_on_calendar"}
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
    "idle_notify": "slurm_usage_report_notify",  # mention (in the report thread), dm; [] none
    "leave_grace_days": "people_leave_grace_days",
    "submit_holds_disk": "submit_holds_disk",  # a person at a disk hard limit cannot submit
}
_SIZE_GIB = {"K": 1 / 1024**2, "M": 1 / 1024, "G": 1, "T": 1024}


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
# test only Ansible has, leaves the template unresolved. Native, as Ansible's: "{{ days }}"
# over an integer is that integer, not its text.
_JINJA = jinja2.nativetypes.NativeEnvironment(undefined=jinja2.StrictUndefined, autoescape=False)  # noqa: S701


def _render(text, values):
    """TEXT rendered over VALUES, or None when it needs anything they lack."""
    try:
        out = _JINJA.from_string(text).render(values)
        # A lone undefined name renders as itself natively instead of raising.
        return None if isinstance(out, jinja2.Undefined) else out
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
            "summary_ko": cmd.summary_ko,
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


def dashboards(settings_):
    """[{uid, title}] of every dashboard deepops ships (grafana_dashboard_sets), in order."""
    out = []
    for files in (settings_.get("grafana_dashboard_sets") or {}).values():
        for name in files:
            board = json.loads((ROOT / "src" / "dashboards" / name).read_text())
            out.append({"uid": board["uid"], "title": board["title"]})
    return out


def services(config, settings_):
    """Whether the portal, the monitoring dashboard, the cluster bot and job notices run;
    the portal's overview page title (its setting is a template, set when the site has
    one), the titles of the services in its top menu, its own menu groups and the folders
    its file browser opens; the dashboards Grafana shows."""
    portal = bool(settings_.get("slurm_install_open_ondemand"))
    raw = {}
    for name in ("all.yml", "slurm-cluster.yml") if config else ():
        raw.update(_yaml(Path(config) / "group_vars" / name))
    overview = portal and raw.get("open_ondemand_overview", settings_.get("open_ondemand_overview"))
    services_ = settings_.get("open_ondemand_proxied_services") or [] if portal else []
    return {
        "portal": portal,
        "overview": settings_.get("open_ondemand_overview_title") if overview else "",
        "menu": [svc["title"] for svc in services_ if svc.get("title")],
        "dashboard": bool(settings_.get("slurm_enable_monitoring")),
        "bot": bool(settings_.get("slurm_enable_cluster_bot")),
        "job_notices": bool(settings_.get("slurm_enable_job_notices")),
        "portal_groups": list(settings_.get("open_ondemand_nav_groups") or []) if portal else [],
        "portal_files": list(settings_.get("open_ondemand_allowlist_paths") or []) if portal else [],
        "dashboards": dashboards(settings_) if settings_.get("slurm_enable_monitoring") else [],
    }


def paths(settings_):
    return {
        "scratch": settings_.get("scratch_space_root"),
        "software": settings_.get("sm_prefix"),
        "shared": settings_.get("shared_space_root"),
        "shared_data": settings_.get("shared_space_data"),
    }


def login_limits(config, settings_):
    """{cpus, memory_gib, tasks, private_dirs}: the user-.slice limits the login nodes
    get (cluster_tools_user_slice_limits; group_vars/slurm-login.yml over the others),
    and the directories each login has its own of (roles/login_private_dirs)."""
    limits = settings_.get("cluster_tools_user_slice_limits") or {}
    if config:
        limits = _yaml(Path(config) / "group_vars" / "slurm-login.yml").get(
            "cluster_tools_user_slice_limits", limits
        )
    out = {}
    cpu = str(limits.get("CPUQuota", "")).strip()
    if cpu.endswith("%"):
        out["cpus"] = _whole(float(cpu[:-1]) / 100)
    memory = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGT])", str(limits.get("MemoryMax", "")).strip())
    if memory:
        out["memory_gib"] = _whole(float(memory.group(1)) * _SIZE_GIB[memory.group(2)])
    if str(limits.get("TasksMax", "")).isdigit():
        out["tasks"] = int(limits["TasksMax"])
    if settings_.get("slurm_enable_login_private_dirs"):
        out["private_dirs"] = settings_.get("login_private_dirs_paths")
    return out


def _whole(x):
    return int(x) if float(x).is_integer() else round(x, 1)


def bot(settings_):
    """{command, subcommands: [{name, help}]}: the one slash command and its subcommands
    in their order, each with its Korean help line (cluster_bot.commands)."""
    spec = importlib.util.spec_from_file_location(
        "cluster_bot_commands", ROOT / "roles/cluster_bot/files/cluster_bot/commands.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {
        "command": settings_.get("cluster_bot_command"),
        "subcommands": [{"name": s.name, "help": s.ko} for s in module.SUBCOMMANDS],
    }


def submit_policy(p):
    """The submit policy as the user pages state it, never its patterns or names: whether
    GPU sessions must name a team QoS, the allowed GPUs per node ([] any), whether a job
    without a GPU type gets the default one, and each switch of SUBMIT_RULES."""
    return {
        "interactive_team_qos": bool(p.get("interactive_gpu_qos")),
        "gpus_per_node": [int(n) for n in p.get("gpus_per_node") or []],
        "gpu_type_defaulted": bool(p.get("default_gpu_type")),
        **{rule: bool(p.get(rule)) for rule in SUBMIT_RULES},
    }


def facts(config):
    s = settings(config)
    every = {
        "commands": commands(s.get("cluster_tools_builtin") or []),
        "policy": {
            **{field: s.get(key) for field, key in POLICY.items()},
            **submit_policy(s.get("slurm_job_submit_policy") or {}),
        },
        "slurm": slurm_values(config),
        "dates": schedule(config),
        "quota": quota_limits(config, s),
        "environment": environment(config),
        # the commands the overview page names: the ones the cluster publishes
        "cluster": {
            **cluster(config),
            "commands": [c for c in s.get("cluster_tools_commands") or [] if isinstance(c, str)],
        },
        "chat": {"channels": s.get("slack_channels"), "admin_group": s.get("slack_admin_group")},
        "bot": bot(s),
        "login": login_limits(config, s),
        "services": services(config, s),
        "paths": paths(s),
        "reports": {field: s.get(key) for field, key in REPORTS.items()},
        "schedules": {field: s.get(key) for field, key in SCHEDULES.items()},
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
