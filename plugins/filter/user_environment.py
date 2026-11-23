"""The user environment (roles/user_environment_defaults) as one list of entries.

defaults | user_environment(site) -> [{name, login, job}, ...] sorted by name

An entry is NAME: value (the same in a login and a job) or NAME: {login, job} (job: the
value inside a Slurm job where it differs). The site's entry of a name replaces the
default's; a null or empty value drops it. login or job empty: not set there. A value
starting with ~/ is under the user's home. The same function gives the profile.d files
(roles/cluster_tools), the task prolog part (roles/slurm) and the docs' table
(scripts/docs/site_facts.py), so the three cannot disagree.
"""

import re

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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
        out.append({"name": str(name), "login": login, "job": job if job != login else ""})
    return out


class FilterModule:
    def filters(self):
        return {"user_environment": user_environment}
