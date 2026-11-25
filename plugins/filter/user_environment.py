"""The user environment (roles/user_environment_defaults) as one list of entries.

defaults | user_environment(site) -> [{name, login, job}, ...] sorted by name

An entry is NAME: value (the same in a login and a job) or NAME: {login, job} (job: the
value inside a Slurm job where it differs). The site's entry of a name replaces the
default's; a null or empty value drops it. login or job empty: not set there. A value
starting with ~/ is under the user's home. The same function gives the profile.d files
(roles/cluster_tools), the task prolog part (roles/slurm) and the docs' table
(scripts/docs/site_facts.py), so the three cannot disagree.

A library cache (PRIVATE_CACHES) is written by whoever uses it, so its value is per
user: under ~/, or in a job the job's own /tmp; a shared path is refused. A shared
read-only cache is published under a name of its own (SHARED_HF_HUB_CACHE) that users
read from, never as the library's default (a writer-owned HF_HUB_CACHE fails every
download not yet in it, and older huggingface_hub releases a moved branch too).
"""

import re

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# The caches a library writes into as it runs (huggingface_hub, datasets, transformers,
# torch, uv, pip, conda, XDG).
PRIVATE_CACHES = frozenset({
    "HF_HOME", "HF_HUB_CACHE", "HF_ASSETS_CACHE", "HF_XET_CACHE", "HF_DATASETS_CACHE",
    "TRANSFORMERS_CACHE", "TORCH_HOME", "UV_CACHE_DIR", "PIP_CACHE_DIR", "CONDA_PKGS_DIRS",
    "XDG_CACHE_HOME",
})  # fmt: skip


def _private(value, job):
    return value.startswith("~/") or (job and (value == "/tmp" or value.startswith("/tmp/")))


def user_environment(defaults, site=None):
    merged = dict(defaults or {})
    merged.update(site or {})
    out = []
    for name in sorted(merged):
        value = merged[name]
        if not NAME.fullmatch(str(name)):
            raise ValueError(f"user_environment: {name!r} is not a variable name")
        if isinstance(value, dict):
            unknown = set(value) - {"login", "job"}
            if unknown:
                raise ValueError(f"user_environment: {name}: unknown keys {sorted(unknown)}")
            login, job = value.get("login"), value.get("job")
        else:
            login, job = value, None
        login, job = str(login or ""), str(job or "")
        if not login and not job:
            continue
        if name in PRIVATE_CACHES:
            shared = [v for v, in_job in ((login, False), (job, True)) if v and not _private(v, in_job)]
            if shared:
                raise ValueError(
                    f"user_environment: {name}={shared[0]} is a cache its library writes into: a path"
                    " under ~/ (or the job's /tmp), never shared storage; publish a shared read-only"
                    " cache under a name of its own (SHARED_HF_HUB_CACHE)"
                )
        out.append({"name": str(name), "login": login, "job": job if job != login else ""})
    return out


# Each kind of knob (user_environment_cpu_knobs): the variable that carries the number a
# login or a job set, so the next one tells a value it may replace from a user's own.
KINDS = {"threads": "CLUSTER_CPUS", "build": "CLUSTER_BUILD_JOBS"}


def cpu_knobs(knobs):
    """KNOBS checked: [{name, kind, value}], value "{n}" unless given."""
    out = []
    for knob in knobs or []:
        name, kind, value = str(knob.get("name", "")), knob.get("kind"), str(knob.get("value", "{n}"))
        if not NAME.fullmatch(name) or name in KINDS.values():
            raise ValueError(f"user_environment_cpu_knobs: {name!r} is not a variable this table may set")
        if kind not in KINDS:
            raise ValueError(
                f"user_environment_cpu_knobs: {name}: kind {kind!r} is not one of {sorted(KINDS)}"
            )
        if "'" in value or '"' in value or "$" in value or "`" in value:
            raise ValueError(
                f"user_environment_cpu_knobs: {name}: value {value!r} must be plain text with {{n}}"
            )
        out.append({"name": name, "kind": kind, "value": value})
    names = [k["name"] for k in out]
    if len(set(names)) != len(names):
        raise ValueError("user_environment_cpu_knobs: a name is listed twice")
    return out


def _with(value, number):
    return value.replace("{n}", number)


def cpu_knob_lines(knobs, shell, command):
    """The lines that set KNOBS (user_environment_cpu_knobs) from COMMAND (cluster_tools.cpus):
    "sh" and "csh" for a login's profile.d, "prolog" for a Slurm task prolog part (bash,
    printing export lines). Only a variable not set yet, or in a job one still carrying the
    value its login set, is set: a user's own value wins."""
    knobs = cpu_knobs(knobs)
    if not knobs:
        return []
    if shell == "sh":
        lines = [f"if [ -x {command} ] && CLUSTER_CPUS=$({command} 2>/dev/null); then",
                 "    CLUSTER_BUILD_JOBS=$CLUSTER_CPUS",
                 "    export CLUSTER_CPUS CLUSTER_BUILD_JOBS"]  # fmt: skip
        for k in knobs:
            lines.append(
                f'    [ -n "${{{k["name"]}:-}}" ] || export {k["name"]}="{_with(k["value"], "$" + KINDS[k["kind"]])}"'
            )
        return lines + ["fi"]
    if shell == "csh":
        lines = [f"if ( -x {command} ) then",
                 f"    set _cluster_cpus = `{command}`",
                 '    if ( "$_cluster_cpus" != "" ) then',
                 '        setenv CLUSTER_CPUS "$_cluster_cpus"',
                 '        setenv CLUSTER_BUILD_JOBS "$_cluster_cpus"']  # fmt: skip
        for k in knobs:
            value = _with(k["value"], "$" + KINDS[k["kind"]])
            lines.append(f'        if (! $?{k["name"]}) setenv {k["name"]} "{value}"')
            lines.append(f'        if ("${k["name"]}" == "") setenv {k["name"]} "{value}"')
        return lines + ["    endif", "    unset _cluster_cpus", "endif"]
    if shell == "prolog":
        number = {"threads": "$threads", "build": "$build"}
        lines = [f"set -- $({command} --job 2>/dev/null) || exit 0",
                 'threads=${1:-} build=${2:-}',
                 '[ -n "$threads" ] && [ -n "$build" ] || exit 0']  # fmt: skip
        for k in knobs:
            login = _with(k["value"], "${" + KINDS[k["kind"]] + ":-}")
            lines.append(
                f'case "${{{k["name"]}:-}}" in \'\'|"{login}") echo "export {k["name"]}={_with(k["value"], number[k["kind"]])}" ;; esac'
            )
        return lines + ['echo "export CLUSTER_CPUS=$threads"', 'echo "export CLUSTER_BUILD_JOBS=$build"']
    raise ValueError(f"cpu_knob_lines: shell {shell!r} is not sh, csh or prolog")


class FilterModule:
    def filters(self):
        return {
            "user_environment": user_environment,
            "cpu_knobs": cpu_knobs,
            "cpu_knob_lines": cpu_knob_lines,
        }
