"""Slurm's config files (slurm.conf, cgroup.conf, gres.conf, slurmdbd.conf): rendered
from settings, read back, compared, and their timers checked. The one implementation of
their syntax in this repository.

Rendering. roles/slurm builds each file from layers of settings, each a mapping of
Slurm's own keys (slurm.conf(5)): deepops' defaults, the site's overrides, and the
values the role works out from the inventory and its other settings.

    {{ [defaults, site] | slurm_settings('slurm.conf', fixed=computed) | slurm_lines('slurm.conf') }}
    {{ [hardware, policy, host] | slurm_settings('slurm.conf', 'node') | slurm_record('slurm.conf', 'node') }}

A later layer wins; a key set to null (or "") is left out. Every key must be one Slurm
reads in that file and part (plugins/filter/slurm_parameters.json, pinned from the
slurm_version source by scripts/deepops/slurm_parameters.py), in any case: a misspelt,
misplaced or retired (defunct) key fails here, not in slurmctld. FIXED keys are the
role's to set: a layer that sets one is refused. The lines come in a fixed order,
alphabetical as slurm.conf(5) lists them; a list is joined with commas, except for a key
Slurm takes more than once (SlurmctldHost, Prolog), which gets a line per item; true and
false are YES and NO; a value with blanks is quoted.

Reading. slurm_conf_parse(text, conf) -> {"global": {key: [values]}, "records": {(kind,
name): {key: value}}}, keys lower-case, quotes stripped, comments and Jinja-free text
only. slurm_conf_diff(old, new, conf) lists every setting that differs, for the
equivalence check of a site's own file against the role's rendering
(roles/slurm/tasks/conf-compare.yml).

Cluster. slurm_conf_cluster: the nodes (name, CPUs, memory, GPUs) and partitions (nodes,
time limit) a slurm.conf's literal NodeName and PartitionName lines list, templates left
out. The portal's overview page (open_ondemand_overview) and the admin docs
(scripts/docs/site_facts.py cluster) both read it, so neither types the cluster.

    {{ lookup('file', slurm_conf_template) | slurm_conf_cluster }}       -> {nodes, partitions}

Timers. slurm_timer_problems: Slurm's timers that must agree, on a rendered slurm.conf.

    {{ lookup('template', slurm_conf_template) | slurm_timer_problems }}   -> [] when fine

Each timer is set on its own line, but several only work in relation to another, and
nothing in Slurm refuses a pair that disagrees: it drains nodes or aborts jobs at run
time instead. The relations here are the ones slurm.conf(5) states; an unset key takes
Slurm's documented default.

    UnkillableStepTimeout >= 5 x MessageTimeout   (else nodes drain unexpectedly)
    HealthCheckTimeout    <  HealthCheckInterval  (when the health check runs)
    CompleteWait          == 0 or >= KillWait + 2 (the two values the manual recommends)
    BatchStartTimeout     >= PrologTimeout         (a prolog inside its own limit must not
                                                    abort a batch launch or an srun)

The time a job gets to stop cleanly (KillWait) also bounds a preempted job's grace:
roles/slurm passes it to the slurm_accounting module (preempted_grace_time).
"""

import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles" / "cluster_tools_lib" / "files"))
from cluster_tools.slurm import hostnames  # noqa: E402

try:
    from ansible.errors import AnsibleFilterError as Error
except ImportError:  # scripts/deepops/slurm_conf_diff.py without Ansible
    Error = ValueError

PARAMETERS = json.loads(Path(__file__).with_name("slurm_parameters.json").read_text())
# The key that names a record, per file and part; a record line starts with it.
RECORDS = {
    "slurm.conf": {"node": "NodeName", "partition": "PartitionName"},
    "gres.conf": {"line": "Name"},
}
# Fields whose value is a host list: compared as the set of names it expands to.
HOSTLISTS = {"nodes", "nodename"}
_TRUE, _FALSE = {"yes", "true", "1", "on"}, {"no", "false", "0", "off"}


def _table(conf, part):
    try:
        return PARAMETERS["files"][conf][part]
    except KeyError:
        raise Error(f"slurm_conf: no {part} parameters for {conf}") from None


def _key(conf, part, key):
    """KEY as Slurm spells it, after checking Slurm reads it there."""
    table = _table(conf, part)
    found = {k.lower(): k for k in table}.get(str(key).lower())
    if found is None:
        raise Error(f"{conf}: {key} is not a {part} parameter of Slurm {PARAMETERS['version']} ({conf}(5))")
    if table[found]["defunct"]:
        raise Error(f"{conf}: {found} is defunct in Slurm {PARAMETERS['version']}: Slurm ignores it")
    return found


def _order(key):
    return key.lower()


def slurm_settings(layers, conf, part="global", fixed=None):
    """LAYERS merged in order (null or "" leaves a key out), then FIXED, which no layer
    may set; keys checked and spelt as Slurm spells them, in slurm.conf(5) order."""
    merged = {}
    fixed = {_key(conf, part, k): v for k, v in (fixed or {}).items()}
    for layer in [x for x in layers if x]:
        if not isinstance(layer, dict):
            raise Error(f"{conf}: settings must be a mapping of Slurm keys, not {layer!r}")
        for key, value in layer.items():
            name = _key(conf, part, key)
            if name in fixed:
                raise Error(
                    f"{conf}: {name} is set by roles/slurm from its own settings; it cannot be overridden"
                )
            merged[name] = value
    merged.update(fixed)
    return {k: merged[k] for k in sorted(merged, key=_order) if merged[k] is not None and merged[k] != ""}


def _value(value):
    if isinstance(value, bool):  # Slurm reads YES and NO in any case, for flags and modes alike
        return "YES" if value else "NO"
    if isinstance(value, (list, tuple)):
        return ",".join(_value(v) for v in value)
    text = str(value)
    if any(c.isspace() for c in text) and not (text.startswith('"') and text.endswith('"')):
        return f'"{text}"'
    return text


def slurm_lines(settings, conf, part="global"):
    """Key=value lines of SETTINGS (slurm_settings), a line per item for a key Slurm
    takes more than once."""
    table = _table(conf, part)
    lines = []
    for key, value in settings.items():
        if table[key]["type"] == "array" and isinstance(value, (list, tuple)):
            lines += [f"{key}={_value(v)}" for v in value]
        else:
            lines.append(f"{key}={_value(value)}")
    return lines


def slurm_record(settings, conf, part):
    """One record line (NodeName=..., PartitionName=..., gres.conf's Name=...), its
    naming key first."""
    first = RECORDS[conf][part]
    if first not in settings:
        raise Error(f"{conf}: a {part} record needs {first}")
    rest = [f"{k}={_value(v)}" for k, v in settings.items() if k != first]
    return " ".join([f"{first}={_value(settings[first])}", *rest])


def slurm_node_hardware(host, manage_gpus=True, tmp_disk=None):
    """A node's measured slurm.conf fields, as `slurmd -C` prints them, from HOST (its
    hostvars): the role's local facts (topology, memory), its gres.conf lines
    (slurm_gres) and TMP_DISK (its job filesystem in MiB, slurm_tmp_disk). RealMemory is
    the measured total in whole GiB, rounded down: the total moves by a few MB with a
    kernel or firmware update, and every change rewrites slurm.conf and restarts every
    daemon (a site fixes it with slurm_node_policy or slurm_node_conf)."""
    local = host["ansible_facts"]["ansible_local"]
    memory = local["memory"]
    if isinstance(memory, str):  # a fact that failed to parse as JSON: fail on it here
        memory = json.loads(memory)
    cpu = local["topology"]["cpu_topology"]
    out = {
        "CPUs": int(cpu["logical_cpus"]),
        "Boards": 1,
        "SocketsPerBoard": int(cpu["sockets"]),
        "CoresPerSocket": int(cpu["cores_per_socket"]),
        "ThreadsPerCore": int(cpu["threads_per_core"]),
        "RealMemory": int(memory["total_mb"]) // 1024 * 1024,
    }
    if manage_gpus:
        lines = host.get("slurm_gres") or []
        if lines:
            counts = {}
            for line in lines:
                n = int(line["Count"]) if "Count" in line else len(hostnames(str(line.get("File", ""))))
                kind = ":".join(str(x) for x in (line["Name"], line.get("Type")) if x)
                counts[kind] = counts.get(kind, 0) + n
            out["Gres"] = [f"{kind}:{n}" for kind, n in sorted(counts.items())]
        elif local["topology"]["gpu_topology"]:
            out["Gres"] = f"gpu:{len(local['topology']['gpu_topology'])}"
    if tmp_disk:
        out["TmpDisk"] = int(tmp_disk)
    return out


def _tokens(line):
    """The key=value words of LINE, quotes removed, a comment dropped; blanks around "="
    are allowed, as Slurm allows them."""
    line = re.sub(r"\s*=\s*", "=", line)
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        return list(lexer)
    except ValueError:  # an unbalanced quote: Slurm splits on blanks then, as here
        return line.split("#", 1)[0].split()


def _canonical_value(conf, part, key, value):
    if key in HOSTLISTS:
        return ",".join(sorted(hostnames(value)))
    kind = {k.lower(): v["type"] for k, v in _table(conf, part).items()}.get(key)
    if kind == "boolean" and value.lower() in _TRUE | _FALSE:
        return "yes" if value.lower() in _TRUE else "no"
    return value


def slurm_conf_parse(text, conf):
    """The settings of a rendered file: {"global": {key: [values]}, "records": {(part,
    name): {key: value}}}, keys lower-case."""
    out = {"global": {}, "records": {}}
    firsts = {v.lower(): k for k, v in RECORDS.get(conf, {}).items()}
    for raw in str(text).splitlines():
        tokens = _tokens(raw)
        if not tokens:
            continue
        pairs = [t.partition("=") for t in tokens]
        lead = pairs[0][0].lower()
        part = firsts.get(lead)
        if conf == "gres.conf" and lead == "nodename" and len(pairs) > 1:
            part = "line"
        if part is None:
            key, _, value = pairs[0]
            out["global"].setdefault(key.lower(), []).append(
                _canonical_value(conf, "global", key.lower(), value)
            )
            continue
        fields = {k.lower(): _canonical_value(conf, part, k.lower(), v) for k, _, v in pairs}
        if conf == "gres.conf":
            name = tuple(fields.get(k, "") for k in ("nodename", "name", "type", "file"))
        else:
            name = fields[RECORDS[conf][part].lower()]
        out["records"][(part, name)] = fields
    return out


def slurm_conf_diff(old, new, conf):
    """Each setting OLD and NEW (rendered texts) disagree on, as {"where", "key", "old",
    "new"} (None where a side lacks it), sorted; [] when they say the same."""
    a, b = slurm_conf_parse(old, conf), slurm_conf_parse(new, conf)
    out = []
    for key in sorted(set(a["global"]) | set(b["global"])):
        x, y = a["global"].get(key), b["global"].get(key)
        if x != y:
            out.append({"where": conf, "key": _spelt(conf, "global", key), "old": _show(x), "new": _show(y)})
    for name in sorted(set(a["records"]) | set(b["records"]), key=str):
        x, y = a["records"].get(name, {}), b["records"].get(name, {})
        where = f"{name[0]} {' '.join(n for n in name[1] if n) if isinstance(name[1], tuple) else name[1]}"
        for key in sorted(set(x) | set(y)):
            if x.get(key) != y.get(key):
                out.append(
                    {"where": where, "key": _spelt(conf, name[0], key), "old": x.get(key), "new": y.get(key)}
                )
    return out


def _show(values):
    return None if values is None else " | ".join(values)


def _spelt(conf, part, key):
    """KEY (lower-case) as Slurm spells it, or as given when Slurm has no such key."""
    for name in PARAMETERS["files"].get(conf, {}).get(part, {}):
        if name.lower() == key:
            return name
    return key


def slurm_conf_difference_line(d):
    """One slurm_conf_diff entry as a line: where, key, then each side or (unset)."""
    unset = "(unset)"
    return f"{d['where']}: {d['key']}: {d['old'] if d['old'] is not None else unset} -> {d['new'] if d['new'] is not None else unset}"


MESSAGE_TIMEOUT = 10  # slurm.conf(5) defaults
KILL_WAIT = 30
HEALTH_CHECK_TIMEOUT = 60
BATCH_START_TIMEOUT = 10


def _values(conf):
    return {k: v[-1] for k, v in slurm_conf_parse(conf, "slurm.conf")["global"].items()}


def _int(values, key, default):
    raw = values.get(key.lower())
    return int(raw) if raw is not None and raw.lstrip("-").isdigit() else default


def slurm_timer_problems(conf):
    v = _values(conf)
    problems = []
    message = _int(v, "MessageTimeout", MESSAGE_TIMEOUT)
    unkillable = _int(v, "UnkillableStepTimeout", max(60, 5 * message))
    if unkillable < 5 * message:
        problems.append(
            f"UnkillableStepTimeout={unkillable} is below 5 x MessageTimeout ({5 * message}): "
            "nodes drain while a step is still being killed"
        )
    interval = _int(v, "HealthCheckInterval", 0)
    check = _int(v, "HealthCheckTimeout", HEALTH_CHECK_TIMEOUT)
    if interval > 0 and check >= interval:
        problems.append(f"HealthCheckTimeout={check} is not below HealthCheckInterval={interval}")
    kill_wait = _int(v, "KillWait", KILL_WAIT)
    complete = _int(v, "CompleteWait", 0)
    if complete and complete < kill_wait + 2:
        problems.append(f"CompleteWait={complete} is neither 0 nor at least KillWait + 2 ({kill_wait + 2})")
    prolog = _int(v, "PrologTimeout", _int(v, "PrologEpilogTimeout", 0))
    batch = _int(v, "BatchStartTimeout", BATCH_START_TIMEOUT)
    if prolog and batch < prolog:
        problems.append(
            f"BatchStartTimeout={batch} is below the prolog's limit ({prolog}): a prolog that "
            "is slow but within its limit aborts the batch launch or the srun"
        )
    return problems


def _pairs(line):
    """KEY=VALUE words of a slurm.conf line, templates left out."""
    line = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", line)
    return dict(w.split("=", 1) for w in line.split() if "=" in w)


def slurm_conf_cluster(conf):
    nodes, partitions = [], []
    for line in str(conf).splitlines():
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


class FilterModule:
    def filters(self):
        return {
            "slurm_settings": slurm_settings,
            "slurm_lines": slurm_lines,
            "slurm_record": slurm_record,
            "slurm_node_hardware": slurm_node_hardware,
            "slurm_conf_diff": slurm_conf_diff,
            "slurm_conf_difference_line": slurm_conf_difference_line,
            "slurm_timer_problems": slurm_timer_problems,
            "slurm_conf_cluster": slurm_conf_cluster,
        }
