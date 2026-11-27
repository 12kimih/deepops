#!/usr/bin/env python3
"""The facts the user and admin docs show, from the code and the site config, so no page
types a value its source already holds.

    site_facts.py [--config DIR] --public FILE [--check]

The public file goes to the user docs site, a repository of its own, which is
published: it carries only the keys in PUBLIC (a test holds the generator to it), never
addresses, secrets or the site's own limits. The admin docs (the site config's pages)
read every fact straight from facts(), through scripts/docs/generate.py's fact: blocks.
With --check nothing is written; a file that differs from what would be written is
named and the exit status is 1.

Facts:
  commands  the people's cluster commands (cluster_tools_builtin) and their options,
            read from each command's one definition (cluster_tools.cli Command)
  policy    the cluster-wide rules the pages state, each field from the setting POLICY
            names (periods, fractions, the rules on jobs leaving their GPUs idle, how
            people are told, login grace, session QoS, submit holds); the pattern of a
            QoS stays private; max_submit_jobs, the cap on a job's queued and running jobs
            every account of slurm_accounting_file sets alike (None when they differ or
            none sets one)
  messages  how the submit holds' refusals begin (cluster_tools.submit_holds): hold_ended
            (past the membership's end), hold_disk (at a disk hard limit)
  terms     the Korean glossary (scripts/deepops/terms_ko.yml, scripts/deepops/terms.py):
            each entry's word to use, its title form, the variants refused (anywhere, or
            in titles only), why, and its scope; titles, the title rules (the sentence
            endings a title may not have, the files exempt), so the user docs' own test
            applies the same list
  storage   each measured filesystem's speeds (io_fairness_models, its model): read_bps
            and write_bps (sequential, bytes a second), read_iops and write_iops (random)
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
            (roles/slurm accounting), and the most until SIGKILL (GraceTime + KillWait);
            ResumeTimeout and HealthCheckInterval (private: the admin docs' timings)
  quota     each counted filesystem with limits (the hosts' disk_quota_filesystems):
            block_hard and its warn level in TiB, inode_hard (its own, or block_hard's TiB
            times disk_quota_inodes_per_tib) and its warn level (cluster_user_warn_fraction)
  dates     the site's announced dates (config schedule.yml, name: YYYY-MM-DD), so a
            page states each once; only date values pass
  threads   the knobs that size work by the machine a login and a job get
            (user_environment_cpu_knobs): each one's name, kind (threads: a worker's
            share; build: the job's CPUs on the node), value ({n} the number), what reads
            it and its own default; the person's own value wins
  environment
            the users' environment (user_environment over user_environment_defaults,
            roles/user_environment_defaults), each {name, login, job} as the profile.d
            files and the task prolog get it (plugins/filter/user_environment.py); public
            only an entry whose values are paths or URLs. A value derived from other
            settings (SHARED_HF_HUB_CACHE: model_cache_hub) is resolved as Ansible would
  cluster   the nodes (CPUs, memory, GPUs) and partitions (their nodes, time limit) the
            site's slurm.conf template lists (private; plugins/filter/slurm_conf.py, which
            the portal's overview page reads too)
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
a host knows (groups, facts, an Ansible filter) is left out.
"""

import importlib
import importlib.util
import json
import re
import os
import sys
import tomllib
from pathlib import Path

import jinja2
import jinja2.meta
import jinja2.nativetypes
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "scripts/deepops"))
import cli  # noqa: E402
import terms  # noqa: E402
from cluster_tools import ssh_rate, submit_holds  # noqa: E402
from repo_files import tracked  # noqa: E402


def _filter(name):
    """A filter plugin's module, loaded from its file: plugins/filter on sys.path would
    shadow the standard library with its secrets.py, so a module that puts it there for
    its own imports (people.py, for days) has sys.path back as it was afterwards."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "plugins/filter" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = path
    return module


# The one function each reader of the users' environment, and of the cluster, uses.
_user_env = _filter("user_environment")
_slurm_conf = _filter("slurm_conf")

# The submit policy's switches (slurm_job_submit_policy, roles/slurm's job_submit.lua),
# each a policy fact of its name: what the job rules on the user pages say holds.
SUBMIT_RULES = ("strict_gpu_type", "force_gpu_partition", "gpu_jobs_use_defaults", "gpu_nodes_shared")

# The only top-level keys the public file may carry, and under commands and policy the
# only fields: what a user may read on a public site.
PUBLIC = {
    "commands": {"summary", "summary_ko", "usage", "options", "subcommands"},
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
        "submit_holds",
        "submit_holds_disk",
        "max_submit_jobs",
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
    "threads": {"variables"},
    "chat": {"channels", "admin_group"},
    "bot": {"command", "subcommands"},
    "reports": {"usage", "weekly", "disk"},
    "schedules": {"scratch_purge"},
    "messages": {"hold_ended", "hold_disk", "ssh_rate"},
    "storage": None,  # each measured mount, its fields STORAGE_FIELDS
    "terms": {"use", "title", "not", "title_not", "why", "scope", "id", "code"},  # every glossary entry
    "titles": {"endings", "exempt"},  # the glossary's title rules
    "rules": {"id", "title", "action", "check", "anchor", "active", "values"},  # cluster_tools.rules, each
    # {old: new} of the retired commands (cluster_tools_retired_builtin), for the docs' and
    # the site config's checks that refuse an old name (scripts/deepops/retired_names.py)
    "commands_retired": None,
    # retired_names.pattern() as text, so a check outside deepops (cluster-docs CI)
    # compiles exactly that rule and maps each match through commands_retired
    "commands_retired_pattern": None,
}
STORAGE_FIELDS = {
    "read_bps": "rbps",
    "write_bps": "wbps",
    "read_iops": "rrandiops",
    "write_iops": "wrandiops",
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
    # The rules on jobs leaving their GPUs idle (roles/slurm_usage_report): after idle_hours, a job cancelled
    # after cancel_after_hours (0: never, the docs say not active), warned cancel_warn_hours
    # before.
    "idle_hours": "slurm_usage_report_idle_hours",
    "cancel_after_hours": "slurm_usage_report_cancel_after_hours",
    "cancel_warn_hours": "slurm_usage_report_cancel_warn_hours",
    "idle_notify": "slurm_usage_report_notify",  # mention (in the report thread), dm; [] none
    "leave_grace_days": "people_leave_grace_days",
    "submit_holds": "slurm_enable_submit_holds",  # past a membership's end, no submitting
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


GROUPS = ("all.yml", "slurm-cluster.yml")


def settings(config, group=None):
    """Every setting the role defaults and the site's group_vars/all.yml and
    group_vars/slurm-cluster.yml give (and GROUP's file, a narrower group's, last), the
    site's over the defaults: the literal ones, and each template string that renders
    from them (repeated while one more does)."""
    out = {}
    for defaults in tracked("roles/*/defaults/main.yml"):
        for key, value in _yaml(defaults).items():
            out.setdefault(key, value)
    if config:
        for name in GROUPS + ((group,) if group else ()):
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


def resolved(name, raw, _seen=frozenset()):
    """RAW[NAME] as Ansible renders it over the other values of RAW (one host's settings,
    scripts/deepops/settings.py), rendering only the names it uses; None when it is unset,
    vaulted, or needs what only a host knows. Strings inside lists and dicts too."""
    if name not in raw or name in _seen:
        return None
    seen = _seen | {name}

    def one(value):
        if isinstance(value, dict):
            out = {k: one(v) for k, v in value.items()}
            return None if any(out[k] is None and value[k] is not None for k in value) else out
        if isinstance(value, list):
            out = [one(v) for v in value]
            return None if any(o is None and v is not None for o, v in zip(out, value)) else out
        if not isinstance(value, str):
            return value if value is None or isinstance(value, (bool, int, float)) else None
        if "{{" not in value and "{%" not in value:
            return value
        try:
            used = jinja2.meta.find_undeclared_variables(_JINJA.parse(value))
        except jinja2.TemplateError:
            return None
        values = {}
        for n in used:
            v = resolved(n, raw, seen)
            if v is None:
                return None
            values[n] = v
        return _render(value, values)

    return one(raw[name])


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


def _retired_pattern(retired):
    """The regex text of a retired command as a word (scripts/deepops/retired_names.py),
    or "" with none retired."""
    if not retired:
        return ""
    sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
    import retired_names

    return retired_names.pattern(retired).pattern


def rules_active(config):
    """{rule id: bool}: whether this cluster runs each rule on some of its hosts, the
    role's own gates (roles/cluster_tools/vars/main.yml _cluster_tools_usage_active) over
    the settings of the cluster and of each narrower group (the login nodes' limits are
    theirs): what empties a rule's part of the rules file, so usage says off there."""
    gates = _yaml(ROOT / "roles/cluster_tools/vars/main.yml").get("_cluster_tools_usage_active", {})
    env = jinja2.nativetypes.NativeEnvironment(undefined=jinja2.StrictUndefined, autoescape=False)  # noqa: S701
    # What the gates use of Ansible: bool (a native value or a word), extract, and the
    # roster's filter, over the site's roster and its hosts' own vars (host_vars; one
    # host more without any, as every host without a file has the role defaults).
    env.filters["bool"] = lambda v: (
        v if isinstance(v, bool) else str(v).strip().lower() in ("yes", "on", "1", "true", "y")
    )
    env.filters["extract"] = lambda item, container: container[item]
    env.filters["people_disk_limited"] = _filter("people").people_disk_limited
    hosts = {p.name: _yaml(p) for p in sorted((Path(config) / "host_vars").glob("*"))} if config else {}
    hosts[""] = {}
    inventory = {
        "people": (_yaml(Path(config) / "people.yml").get("people") or {}) if config else {},
        "groups": {"all": list(hosts)},
        "hostvars": hosts,
    }
    groups = Path(config) / "group_vars" if config else None
    others = sorted(p.name for p in groups.glob("*.yml") if p.name not in GROUPS) if groups else []
    out = dict.fromkeys(gates, False)
    for group in [None, *others]:
        s = {**settings(config, group), **inventory}
        for rule, gate in gates.items():
            try:
                value = gate if isinstance(gate, bool) else env.from_string(gate).render(s)
            except (jinja2.TemplateError, TypeError, ValueError):
                value = None
            if not isinstance(value, bool):
                where = group or "the cluster"
                raise SystemExit(f"site_facts: the usage gate of {rule} does not render ({where})")
            out[rule] = out[rule] or value
    return out


def rules_table(settings_, config=None):
    """[{id, title, action, check, anchor, active, values}] of every rule that warns,
    limits or cancels (cluster_tools.rules RULES, in order): what usage and the bot show,
    and the docs' rules table. active: whether this cluster runs it (rules_active, CONFIG's).
    values: each of the rule's settings this site resolves to a plain number, word or
    switch (a mapping, a site's own limits per filesystem, stays out)."""
    import cluster_tools.rules as registry

    active = rules_active(config)
    return [
        {"id": r.id, "title": r.title, "action": r.action, "check": r.check, "anchor": r.anchor,
         "active": active[r.id],
         "values": {n: settings_[n] for n in r.settings
                    if isinstance(settings_.get(n), (int, float, str, bool)) and settings_.get(n) != ""}}
        for r in registry.RULES
    ]  # fmt: skip


def _command(cmd):
    return {
        "summary": cmd.summary,
        "summary_ko": cmd.summary_ko,
        "usage": cmd.usage,
        "options": [
            {"flags": list(o.flags), "metavar": o.metavar or "", "help": o.help}
            for o in cmd.options
            if not getattr(o, "positional", False)
        ],
    }


def commands(names):
    """{command: {summary, summary_ko, usage, options: [{flags, metavar, help}]}} from
    each command's definition (cluster_tools.<module>.COMMAND), and for a command of
    subcommands (usage, its GROUP) subcommands: [{name, the same}] in order."""
    out = {}
    for name in names:
        module = importlib.import_module(f"cluster_tools.{name.replace('-', '_')}")
        out[name] = _command(module.COMMAND)
        group = getattr(module, "GROUP", None)
        if group:
            out[name]["subcommands"] = [{"name": sub, **_command(c)} for sub, c in group.commands()[1:]]
    return out


def _slurm_conf_path(config):
    site = Path(config) / "files/slurm/slurm.conf" if config else None
    return site if site and site.exists() else ROOT / "roles/slurm/templates/etc/slurm/slurm.conf"


def cluster(config):
    """{nodes: [{name, cpus, memory_mb, gres}], partitions: [{name, nodes, max_time}]} from
    the literal NodeName and PartitionName lines of the site's slurm.conf template."""
    return _slurm_conf.slurm_conf_cluster(_slurm_conf_path(config).read_text())


# Timings the admin docs state, as slurm.conf sets them literally (seconds).
SLURM_SECONDS = {
    "resume_timeout_seconds": "ResumeTimeout",
    "health_check_interval_seconds": "HealthCheckInterval",
}


def slurm_values(config):
    """KillWait as slurm.conf says it, the preemption times that follow from it, and
    SLURM_SECONDS: from the site's own slurm.conf template while it has one
    (config/files/slurm/slurm.conf), else the settings roles/slurm renders."""
    text = _slurm_conf_path(config).read_text()
    rendered = slurm_conf_settings(config)

    def seconds(key):
        m = re.search(rf"(?m)^\s*{key}\s*=\s*(\d+)\s*$", text)
        found = m.group(1) if m else rendered.get(key.lower())
        return int(found) if found not in (None, "") else None

    out = {field: seconds(key) for field, key in SLURM_SECONDS.items() if seconds(key) is not None}
    kill_wait = seconds("KillWait")
    if kill_wait is not None:
        out.update(
            kill_wait_seconds=kill_wait, preempt_grace_seconds=kill_wait, sigkill_after_seconds=2 * kill_wait
        )
    return out


def slurm_conf_settings(config):
    """{key (lower-case): value} of slurm.conf as roles/slurm renders it: slurm_conf over
    slurm_conf_defaults, and the values the role works out (_slurm_conf_computed) that
    render from the settings."""
    s = settings(config)
    computed = _yaml(ROOT / "roles/slurm/vars/main.yml").get("_slurm_conf_computed", {})
    out = {}
    for layer in (s.get("slurm_conf_defaults") or {}, s.get("slurm_conf") or {}):
        out.update({str(k).lower(): v for k, v in layer.items()})
    for key, value in computed.items():
        resolved = _resolve(value, s)
        if resolved is not None:
            out[key.lower()] = resolved
    return {k: v for k, v in out.items() if v is not None}


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


def threads(knobs):
    """The knobs (user_environment_cpu_knobs) as the docs' table lists them, checked as the
    profile.d files and the task prolog render them."""
    _user_env.cpu_knobs(knobs)
    return [
        {"name": k["name"], "kind": k["kind"], "value": str(k.get("value", "{n}")),
         "reader": k.get("reader", ""), "default": str(k.get("default", ""))}
        for k in knobs
    ]  # fmt: skip


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


def max_submit_jobs(config):
    """The cap on each person's queued and running jobs (max_submit_jobs) of the site's
    accounting file, as Slurm applies it: an account's own or the nearest parent's, a
    member's own over its account's. One number when every account under root and every
    member gets the same; None when they differ or one gets none. The file's setting
    names it under inventory_dir, the config itself."""
    raw = {}
    for name in ("all.yml", "slurm-cluster.yml") if config else ():
        raw.update(_yaml(Path(config) / "group_vars" / name))
    path = re.sub(r"\{\{\s*inventory_dir\s*\}\}", str(config), str(raw.get("slurm_accounting_file") or ""))
    if not path or "{{" in path:
        return None
    accounts = {n: a or {} for n, a in (_yaml(path).get("accounts") or {}).items()}

    def cap(name, seen=()):
        a = accounts.get(name) or {}
        if "max_submit_jobs" in a or not a.get("parent") or name in seen:
            return a.get("max_submit_jobs")
        return cap(a["parent"], (*seen, name))

    values = set()
    for name, a in accounts.items():
        if not a.get("parent"):
            continue  # root: Slurm's own, never a person's account
        values.add(cap(name))
        values |= {
            m["max_submit_jobs"]
            for m in a.get("members") or []
            if isinstance(m, dict) and "max_submit_jobs" in m
        }
    return values.pop() if len(values) == 1 else None


def storage(settings_):
    """{mount: {read_bps, write_bps, read_iops, write_iops}} of each measured filesystem
    (io_fairness_models): what the user pages say about how fast each one is."""
    out = {}
    for mount, entry in (settings_.get("io_fairness_models") or {}).items():
        model = (entry or {}).get("model") or {}
        if all(isinstance(model.get(k), (int, float)) for k in STORAGE_FIELDS.values()):
            out[mount] = {field: model[k] for field, k in STORAGE_FIELDS.items()}
    return out


def facts(config):
    s = settings(config)
    every = {
        "commands": commands(s.get("cluster_tools_builtin") or []),
        "rules": rules_table(s, config),
        "commands_retired": dict(s.get("cluster_tools_retired_builtin") or {}),
        "commands_retired_pattern": _retired_pattern(s.get("cluster_tools_retired_builtin") or {}),
        "policy": {
            **{field: s.get(key) for field, key in POLICY.items()},
            **submit_policy(s.get("slurm_job_submit_policy") or {}),
            "max_submit_jobs": max_submit_jobs(config),
        },
        "messages": {
            "hold_ended": submit_holds.ENDED,
            "hold_disk": submit_holds.DISK_HELD,
            "ssh_rate": ssh_rate.REFUSED,
        },
        "storage": storage(s),
        "terms": terms.public(terms.load()),
        "titles": terms.public_titles(terms.load()),
        "slurm": slurm_values(config),
        "dates": schedule(config),
        "quota": quota_limits(config, s),
        "environment": environment(config),
        "threads": {"variables": threads(s.get("user_environment_cpu_knobs") or [])},
        "cluster": cluster(config),
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
        elif key in ("terms", "rules"):
            out[key] = [{f: v for f, v in e.items() if f in fields} for e in value or []]
        elif key == "storage":
            out[key] = {
                m: {f: v for f, v in d.items() if f in STORAGE_FIELDS} for m, d in (value or {}).items()
            }
        elif key == "quota":
            out[key] = {
                m: {f: v for f, v in d.items() if f in QUOTA_FIELDS} for m, d in (value or {}).items()
            }
        elif key == "commands_retired_pattern":
            out[key] = value if isinstance(value, str) else ""
        elif key == "commands_retired":
            out[key] = {old: new for old, new in (value or {}).items() if isinstance(new, str)}
        elif fields is None:  # names the site chooses; the values are checked by kind
            out[key] = {f: v for f, v in (value or {}).items() if isinstance(v, str) and DATE.fullmatch(v)}
        else:
            out[key] = {f: v for f, v in (value or {}).items() if f in fields}
    return out


def render(data):
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def docs_url_mismatch(public_file, config):
    """Where the docs site says it is published (site_url in the zensical.toml beside
    its data/ directory) against where the cluster tells people to read it
    (cluster_info.docs_url: the login banner, the bot, the agent rules): a message when
    both are set and differ, else None. Neither read the other, unchecked (2026-11-27)."""
    toml = Path(public_file).resolve().parents[1] / "zensical.toml"
    url = ((settings(config).get("cluster_info") or {}).get("docs_url") or "") if config else ""
    if not toml.is_file() or not url:
        return None
    site = (tomllib.loads(toml.read_text()).get("project") or {}).get("site_url") or ""
    if site and site.rstrip("/") != str(url).rstrip("/"):
        return f"{toml}: site_url {site} is not cluster_info.docs_url {url} (the site config)"
    return None


def main(argv=None):
    ap = cli.parser(__doc__)
    ap.add_argument("--config", default=os.environ.get("DEEPOPS_CONFIG_DIR", str(ROOT / "config")))
    ap.add_argument("--public", required=True)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    config = args.config if Path(args.config).is_dir() else None
    path, text = Path(args.public), render(public(facts(config)))
    mismatch = docs_url_mismatch(path, config)
    if mismatch:
        print(mismatch, file=sys.stderr)
        return 1
    if not args.check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    elif not path.exists() or path.read_text() != text:
        print(f"{path} is stale: run scripts/docs/site_facts.py and commit it", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
