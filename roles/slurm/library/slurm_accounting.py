#!/usr/bin/python
"""Make the Slurm accounting database match a declared set of QoS, accounts and users."""

DOCUMENTATION = r"""
module: slurm_accounting
short_description: Converge Slurm QoS, accounts and user associations with sacctmgr
description:
  - Reads QoS, accounts and associations with sacctmgr, compares them with I(spec),
    and runs only the sacctmgr commands that close the difference. Supports check
    mode and diff mode.
  - Entities missing from I(spec) are reported in C(unmanaged) and left alone unless
    I(prune) is true.
options:
  spec:
    description:
      - "C(qos): name -> {priority, preempt, preempt_mode, grp_tres, max_tres_per_user,
        max_wall, usage_factor, flags, description}."
      - "C(accounts): name -> {parent, fairshare, priority, qos, default_qos,
        description, organization, members}. C(fairshare) takes an integer or
        C(parent). C(members) lists user names, or {user, fairshare, priority, qos,
        default_qos} for a member that differs from the account; a member without
        C(qos) or C(default_qos) inherits the account's, as Slurm does."
      - "C(users): name -> {default_account}, needed only for a user who is a member
        of several accounts."
    type: dict
    required: true
  cluster:
    description: Cluster name; defaults to sacctmgr's (ClusterName in slurm.conf).
    type: str
  prune:
    description: Delete QoS, accounts and associations that I(spec) does not list.
    type: bool
    default: false
  priority_weights:
    description:
      - "PriorityWeight* values from slurm.conf: {qos, assoc, fairshare, age}. With
        C(require_qos_tiers: true) in I(spec), the module fails when association
        priority, fairshare and age could lift a job past the next QoS tier."
    type: dict
  priority_flags:
    description: PriorityFlags from slurm.conf (NO_NORMAL_QOS and NO_NORMAL_ASSOC matter).
    type: list
    elements: str
  sacctmgr:
    description: Path of the sacctmgr binary.
    type: str
    default: sacctmgr
"""

import shlex

from ansible.module_utils.basic import AnsibleModule

QOS_FIELDS = "name,priority,preempt,preemptmode,flags,grptres,maxtrespu,maxwall,usagefactor,description"
ASSOC_FIELDS = "account,user,parentname,fairshare,priority,qos,defaultqos,maxsubmit"
ACCOUNT_FIELDS = "account,description,organization"
BUILTIN_QOS = {"normal"}
# Slurm's top account: it can be declared to set its attributes and members, but it is
# never created, deleted or re-parented.
ROOT = "root"


# --- parsing sacctmgr -nP output -------------------------------------------------


def rows(text, fields):
    names = fields.split(",")
    return [dict(zip(names, line.split("|"))) for line in text.splitlines() if line.strip()]


def tres(text):
    """'gres/gpu:a10=6,cpu=4' -> {'gres/gpu:a10': 6, 'cpu': 4}."""
    out = {}
    for item in filter(None, (text or "").split(",")):
        key, _, value = item.partition("=")
        out[key] = int(value) if value.lstrip("-").isdigit() else value
    return out


def names(text):
    return sorted(filter(None, (text or "").split(",")))


def minutes(value):
    """Slurm time ([D-]HH:MM:SS, HH:MM:SS, MM:SS, MM, or 'UNLIMITED') -> minutes, or None."""
    if value in (None, "", "UNLIMITED", "INFINITE", -1):
        return None
    value = str(value)
    days, _, rest = value.rpartition("-") if "-" in value else ("0", "", value)
    parts = [int(p) for p in rest.split(":")]
    if len(parts) == 1:
        h, m, s = 0, parts[0], 0
    elif len(parts) == 2:
        h, m, s = (0, *parts) if "-" not in value else (parts[0], parts[1], 0)
    else:
        h, m, s = parts
    return int(days or 0) * 1440 + h * 60 + m + (1 if s else 0)


def fairshare(value):
    if value in (None, ""):
        return None
    return "parent" if str(value).lower() == "parent" else int(value)


def priority(value):
    return None if value in (None, "", -1) else int(value)


def current_qos(text):
    found = {}
    for r in rows(text, QOS_FIELDS):
        found[r["name"]] = {
            "priority": int(r["priority"] or 0),
            "preempt": names(r["preempt"]),
            "preempt_mode": (r["preemptmode"] or "cluster").lower(),
            "flags": names(r["flags"]),
            "grp_tres": tres(r["grptres"]),
            "max_tres_per_user": tres(r["maxtrespu"]),
            "max_wall": minutes(r["maxwall"]),
            "usage_factor": float(r["usagefactor"] or 1),
            "description": r["description"],
        }
    return found


def current_assocs(assoc_text, account_text):
    info = {r["account"]: r for r in rows(account_text, ACCOUNT_FIELDS)}
    accounts, users = {}, {}
    for r in rows(assoc_text, ASSOC_FIELDS):
        entry = {
            "fairshare": fairshare(r["fairshare"]),
            "priority": priority(r["priority"]),
            "qos": names(r["qos"]),
            "default_qos": r["defaultqos"] or None,
            "max_submit_jobs": priority(r["maxsubmit"]),
        }
        if r["user"]:
            users.setdefault(r["user"], {})[r["account"]] = entry
        else:
            meta = info.get(r["account"], {})
            accounts[r["account"]] = {
                **entry,
                "parent": r["parentname"] or None,
                "description": meta.get("description"),
                "organization": meta.get("organization"),
            }
    return accounts, users


# --- desired state ---------------------------------------------------------------


def desired_qos(spec):
    out = {}
    for name, q in (spec.get("qos") or {}).items():
        q = q or {}
        out[name] = {
            "priority": int(q.get("priority", 0)),
            "preempt": sorted(q.get("preempt") or []),
            "preempt_mode": str(q.get("preempt_mode", "cluster")).lower(),
            "flags": sorted(q.get("flags") or []),
            "grp_tres": {k: int(v) for k, v in (q.get("grp_tres") or {}).items()},
            "max_tres_per_user": {k: int(v) for k, v in (q.get("max_tres_per_user") or {}).items()},
            "max_wall": minutes(q.get("max_wall")),
            "usage_factor": float(q.get("usage_factor", 1)),
            "description": q.get("description"),
        }
    return out


def desired_accounts(spec):
    out = {}
    for name, a in (spec.get("accounts") or {}).items():
        a = a or {}
        out[name] = {
            "parent": None if name == ROOT else a.get("parent", ROOT),
            "fairshare": fairshare(a.get("fairshare", 1)),
            "priority": priority(a.get("priority")),
            "qos": sorted(a["qos"]) if "qos" in a else None,
            "default_qos": a.get("default_qos"),
            "max_submit_jobs": priority(a.get("max_submit_jobs")),
            "description": a.get("description"),
            "organization": a.get("organization"),
        }
    return out


def inherited(account, key, accounts, current_accounts):
    """An account's value of key as Slurm resolves it: its own, else its parent's."""
    seen = set()
    while account and account not in seen:
        seen.add(account)
        a = accounts.get(account)
        if a is None:
            return (current_accounts.get(account) or {}).get(key)
        if a[key] is not None:
            return a[key]
        account = a["parent"]
    return None


def members(spec):
    """(user, account, options) for every account's members; a member is a name or a dict."""
    for account, a in (spec.get("accounts") or {}).items():
        for m in (a or {}).get("members") or []:
            opts = {"user": m} if isinstance(m, str) else dict(m)
            yield opts.pop("user"), account, opts


def desired_users(spec, accounts, current_accounts):
    out = {}
    for user, account, opts in members(spec):
        assocs = out.setdefault(user, {"associations": {}})["associations"]
        if account in assocs:
            raise ValueError(f"{user} is listed twice in {account}")
        assocs[account] = {
            "fairshare": fairshare(opts.get("fairshare", 1)),
            "priority": priority(opts.get("priority")),
            "qos": sorted(opts["qos"])
            if "qos" in opts
            else inherited(account, "qos", accounts, current_accounts) or [],
            "default_qos": opts.get("default_qos")
            or inherited(account, "default_qos", accounts, current_accounts),
            "max_submit_jobs": priority(opts.get("max_submit_jobs")),
        }
    overrides = spec.get("users") or {}
    for user in set(overrides) - set(out):
        raise ValueError(f"users.{user} is not a member of any account")
    for user, u in out.items():
        default = (overrides.get(user) or {}).get("default_account")
        if default is None and len(u["associations"]) > 1:
            raise ValueError(
                f"{user} is in {', '.join(sorted(u['associations']))}: set users.{user}.default_account"
            )
        if default is not None and default not in u["associations"]:
            raise ValueError(f"users.{user}.default_account {default} is not one of its accounts")
        u["default_account"] = default or next(iter(u["associations"]))
    return out


# --- planning ----------------------------------------------------------------------


def fmt_tres(want, have):
    """grptres value that sets `want` and clears keys only `have` holds (-1 clears)."""
    merged = {**{k: -1 for k in have if k not in want}, **want}
    return ",".join(f"{k}={v}" for k, v in sorted(merged.items()))


def fmt_minutes(value):
    if value is None:
        return "-1"
    d, rem = divmod(value, 1440)
    return f"{d}-{rem // 60:02d}:{rem % 60:02d}:00"


def same_text(want, have):
    """Slurm stores QoS and account descriptions lowercased, so compare without case."""
    return (want or "").casefold() == (have or "").casefold()


def qos_changes(want, have):
    sets = []
    if have is None or want["priority"] != have["priority"]:
        sets.append(f"priority={want['priority']}")
    if have is None or want["preempt"] != have["preempt"]:
        sets.append("preempt=" + ",".join(want["preempt"]))
    if have is not None and want["preempt_mode"] != have["preempt_mode"]:
        sets.append(f"preemptmode={want['preempt_mode']}")
    if have is None or want["flags"] != have["flags"]:
        sets.append("flags=" + (",".join(want["flags"]) if want["flags"] else "-1"))
    for key, field in (("grp_tres", "grptres"), ("max_tres_per_user", "maxtrespu")):
        before = (have or {}).get(key, {})
        if want[key] != before and (want[key] or before):
            sets.append(f"{field}={fmt_tres(want[key], before)}")
    if want["max_wall"] != (have or {}).get("max_wall"):
        sets.append(f"maxwall={fmt_minutes(want['max_wall'])}")
    if want["usage_factor"] != (have or {}).get("usage_factor", 1.0):
        sets.append(f"usagefactor={want['usage_factor']}")
    if want["description"] is not None and not same_text(
        want["description"], (have or {}).get("description")
    ):
        sets.append(f"description={want['description']}")
    return sets


def assoc_sets(want, have, account_level):
    sets = []
    if want["fairshare"] != (have or {}).get("fairshare"):
        sets.append(f"fairshare={want['fairshare'] if want['fairshare'] is not None else -1}")
    if want["priority"] != (have or {}).get("priority"):
        sets.append(f"priority={want['priority'] if want['priority'] is not None else -1}")
    if want["qos"] is not None and want["qos"] != (have or {}).get("qos"):
        sets.append("qos=" + ",".join(want["qos"]))
    if want["max_submit_jobs"] != (have or {}).get("max_submit_jobs"):
        value = want["max_submit_jobs"]
        sets.append(f"maxsubmitjobs={value if value is not None else -1}")
    if want["default_qos"] is not None and want["default_qos"] != (have or {}).get("default_qos"):
        sets.append(f"defaultqos={want['default_qos']}")
    if account_level:
        for key in ("description", "organization"):
            if want[key] is not None and not same_text(want[key], (have or {}).get(key)):
                sets.append(f"{key}={want[key]}")
    return sets


def ordered_accounts(accounts):
    """Parents before children, so an account's parent exists when it is created."""
    done, order = set(), []

    def visit(name, path=()):
        if name in done or name not in accounts:
            return
        if name in path:
            raise ValueError(f"account parent loop: {' -> '.join(path + (name,))}")
        visit(accounts[name]["parent"], path + (name,))
        done.add(name)
        order.append(name)

    for name in sorted(accounts):
        visit(name)
    return order


def plan(spec, cur_qos, cur_accounts, cur_users, cluster=None, prune=False):
    """The sacctmgr argument lists that make the database match spec, and what is unmanaged."""
    where_cluster = [f"cluster={cluster}"] if cluster else []
    cmds = []
    want_qos = desired_qos(spec)
    want_accounts = desired_accounts(spec)
    want_users = desired_users(spec, want_accounts, cur_accounts)

    undefined = {
        q
        for a in list(want_accounts.values())
        + [x for u in want_users.values() for x in u["associations"].values()]
        for q in (a["qos"] or []) + ([a["default_qos"]] if a["default_qos"] else [])
        if q not in want_qos and q not in cur_qos
    }
    undefined |= {p for q in want_qos.values() for p in q["preempt"] if p not in want_qos}
    if undefined:
        raise ValueError(f"QoS used but not defined: {', '.join(sorted(undefined))}")

    # QoS: create every new one first, so preempt lists can name each other.
    for name in sorted(want_qos):
        if name not in cur_qos:
            cmds.append(["add", "qos", name])
    for name in sorted(want_qos):
        sets = qos_changes(want_qos[name], cur_qos.get(name))
        if sets:
            cmds.append(["modify", "qos", "where", f"name={name}", "set", *sets])

    for name in ordered_accounts(want_accounts):
        want, have = want_accounts[name], cur_accounts.get(name)
        if name == ROOT:
            pass  # exists in every cluster; only its attributes below are converged
        elif have is None:
            cmds.append(["add", "account", name, f"parent={want['parent']}", *where_cluster])
        elif want["parent"] != have["parent"]:
            cmds.append(
                [
                    "modify",
                    "account",
                    "where",
                    f"name={name}",
                    *where_cluster,
                    "set",
                    f"parent={want['parent']}",
                ]
            )
        sets = assoc_sets(want, have, account_level=True)
        if sets:
            cmds.append(["modify", "account", "where", f"name={name}", *where_cluster, "set", *sets])

    for name in sorted(want_users):
        want, have = want_users[name], cur_users.get(name, {})
        for account in sorted(want["associations"]):
            if account not in have:
                cmds.append(["add", "user", name, f"account={account}", *where_cluster])
            sets = assoc_sets(want["associations"][account], have.get(account), account_level=False)
            if sets:
                cmds.append(
                    [
                        "modify",
                        "user",
                        "where",
                        f"name={name}",
                        f"account={account}",
                        *where_cluster,
                        "set",
                        *sets,
                    ]
                )
    # Resolved against the users' current default accounts in main(), once they are read.
    for name in sorted(want_users):
        if want_users[name]["default_account"]:
            cmds.append(("default", name, want_users[name]["default_account"]))

    unmanaged = {
        "qos": sorted(set(cur_qos) - set(want_qos) - BUILTIN_QOS),
        "accounts": sorted(set(cur_accounts) - set(want_accounts) - {ROOT}),
        "associations": sorted(
            f"{u}@{a}"
            for u, assocs in cur_users.items()
            for a in assocs
            if (u, a) != (ROOT, ROOT) and a not in want_users.get(u, {}).get("associations", {})
        ),
    }
    if prune:
        for item in unmanaged["associations"]:
            user, account = item.split("@")
            cmds.append(["delete", "user", "where", f"name={user}", f"account={account}", *where_cluster])
        for name in reversed(unmanaged["accounts"]):
            cmds.append(["delete", "account", "where", f"name={name}", *where_cluster])
        for name in unmanaged["qos"]:
            cmds.append(["delete", "qos", "where", f"name={name}"])
    return cmds, unmanaged


def priority_layout(spec, weights, flags):
    """QoS tier values and the most any job can add on top within a tier.

    Job priority adds weight x factor per term. With NO_NORMAL_QOS / NO_NORMAL_ASSOC the
    QoS and association factors are the raw priorities; otherwise they are normalised
    to the highest one (0..1). Fairshare and age factors are always 0..1.
    """
    qos = sorted({q["priority"] for q in desired_qos(spec).values()})
    w = {k: int(weights.get(k) or 0) for k in ("qos", "assoc", "fairshare", "age")}
    top_qos = max(qos) if qos and max(qos) else 1
    tiers = [p * w["qos"] if "NO_NORMAL_QOS" in flags else p / top_qos * w["qos"] for p in qos]
    accounts = desired_accounts(spec)
    assoc = [a["priority"] or 0 for a in accounts.values()]
    assoc += [opts.get("priority") or 0 for _, _, opts in members(spec)]
    top_assoc = max(assoc, default=0)
    assoc_max = top_assoc * w["assoc"] if "NO_NORMAL_ASSOC" in flags else (w["assoc"] if top_assoc else 0)
    return tiers, assoc_max + w["fairshare"] + w["age"]


def check_qos_tiers(spec, weights, flags):
    """An error when a QoS tier can be overtaken from the tier below, else None."""
    tiers, within = priority_layout(spec, weights, flags)
    gaps = [b - a for a, b in zip(tiers, tiers[1:])]
    if gaps and min(gaps) <= within:
        return (
            f"QoS tiers overlap: the smallest gap between tiers is {min(gaps):g}, but association "
            f"priority, fairshare and age can add up to {within:g} within a tier"
        )
    return None


# --- module ----------------------------------------------------------------------


def main():
    module = AnsibleModule(
        argument_spec={
            "spec": {"type": "dict", "required": True},
            "cluster": {"type": "str"},
            "prune": {"type": "bool", "default": False},
            "sacctmgr": {"type": "str", "default": "sacctmgr"},
            "priority_weights": {"type": "dict", "default": {}},
            "priority_flags": {"type": "list", "elements": "str", "default": []},
        },
        supports_check_mode=True,
    )
    spec = module.params["spec"]
    if spec.get("require_qos_tiers"):
        error = check_qos_tiers(spec, module.params["priority_weights"], module.params["priority_flags"])
        if error:
            module.fail_json(msg=error)
    sacctmgr, cluster = module.params["sacctmgr"], module.params["cluster"]

    def read(*args):
        rc, out, err = module.run_command([sacctmgr, "-nP", *args])
        if rc != 0:
            module.fail_json(msg=f"sacctmgr {' '.join(args)} failed: {err.strip()}")
        return out

    where = [f"cluster={cluster}"] if cluster else []
    cur_qos = current_qos(read("show", "qos", f"format={QOS_FIELDS}"))
    cur_accounts, cur_users = current_assocs(
        # wopl: limits only where they are set, not propagated from parents; otherwise an
        # inherited max_submit_jobs or priority reads as the child's own and gets cleared.
        read("show", "assoc", *where, "wopl", f"format={ASSOC_FIELDS}"),
        read("show", "account", f"format={ACCOUNT_FIELDS}"),
    )
    default_accounts = {
        r["user"]: r["defaultaccount"]
        for r in rows(read("show", "user", "format=user,defaultaccount"), "user,defaultaccount")
    }
    try:
        planned, unmanaged = plan(
            module.params["spec"], cur_qos, cur_accounts, cur_users, cluster, module.params["prune"]
        )
    except ValueError as exc:
        module.fail_json(msg=str(exc))

    cmds = []
    for cmd in planned:
        if isinstance(cmd, tuple):  # ("default", user, account): only when it differs
            _, user, account = cmd
            if default_accounts.get(user) != account:
                cmds.append(["modify", "user", "where", f"name={user}", "set", f"defaultaccount={account}"])
        else:
            cmds.append(cmd)

    lines = [shlex.join(["sacctmgr", "-i", *c]) for c in cmds]
    result = {"changed": bool(cmds), "commands": lines, "unmanaged": unmanaged}
    if module._diff:
        result["diff"] = {"before": "", "after": "\n".join(lines) + ("\n" if lines else "")}
    if not module.check_mode:
        for cmd in cmds:
            rc, out, err = module.run_command([sacctmgr, "-i", *cmd])
            if rc != 0 and "Nothing new added" not in (out + err) and "Nothing modified" not in (out + err):
                module.fail_json(msg=f"sacctmgr {' '.join(cmd)} failed: {(err or out).strip()}", **result)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
