"""Each host's desired component states, from one registry (docs/deepops/component-state.md).

    deepops_component_registry:          # roles/deploy_defaults/vars/main.yml
      rsyslog_server:
        rules:                           # present when any rule holds
          - {switch: "{{ slurm_enable_rsyslog_server }}", group: slurm-master, first: true}
          - {switch: "{{ kube_enable_rsyslog_server }}", group: kube_control_plane, first: true}
        needs: []                        # components a present one makes present
        removal: explicit                # optional: never removed by a switch or group

A rule holds when its switch is on and the host is in its group (only the group's first
host with first: true). Rules of both deploys sit side by side, so a host in the Slurm
and the Kubernetes deploy keeps what either wants. A component another present
component needs is present too.

A component with removal: explicit (the driver, authentication, Slurm, MUNGE, the NFS
server: removing them reboots nodes, locks users out or hangs every NFS client) is
never removed because a rule stopped holding: it is kept (its role does nothing) unless
the site names it in deepops_component_removals, which makes it absent on every host no
rule wants it on (and present where one does).

"""

TRUE = {"true", "yes", "on", "1"}


def _on(value):
    return value is True or (isinstance(value, str) and value.strip().lower() in TRUE)


def check(registry):
    """Problems in a registry, as messages; empty means valid."""
    problems = []
    for name, c in (registry or {}).items():
        for i, rule in enumerate((c or {}).get("rules") or []):
            if not isinstance(rule, dict) or "switch" not in rule or not rule.get("group"):
                problems.append(f"{name}.rules[{i}]: needs switch and group")
        for need in (c or {}).get("needs") or []:
            if need not in registry:
                problems.append(f"{name}.needs: unknown component {need}")
        if (c or {}).get("removal", "implicit") not in ("implicit", "explicit"):
            problems.append(f"{name}.removal: implicit or explicit")
    return problems


def components_desired(registry, groups, host, removals=()):
    """component -> present, absent or kept for HOST; REMOVALS names the explicit-removal
    components the site wants absent."""
    problems = check(registry)
    # The list is cluster-wide: a named component goes where no rule wants it and stays
    # where one does. Naming one that is not removal: explicit would mean nothing.
    problems += [
        f"deepops_component_removals: {r} is not a component with removal: explicit"
        for r in removals
        if (registry.get(r) or {}).get("removal") != "explicit"
    ]
    if problems:
        from ansible.errors import AnsibleFilterError

        raise AnsibleFilterError("invalid component registry:\n  " + "\n  ".join(problems))
    present = set()
    for name, c in registry.items():
        for rule in (c or {}).get("rules") or []:
            members = list(groups.get(rule["group"]) or [])
            if rule.get("first"):
                members = members[:1]
            if _on(rule["switch"]) and host in members:
                present.add(name)
    todo = list(present)
    while todo:
        for need in (registry[todo.pop()] or {}).get("needs") or []:
            if need not in present:
                present.add(need)
                todo.append(need)

    def state(name):
        if name in present:
            return "present"
        if (registry[name] or {}).get("removal") == "explicit" and name not in removals:
            return "kept"
        return "absent"

    return {name: state(name) for name in registry}


def components_order(registry):
    """The registry's components with every one after what it needs (removal runs in
    the reverse); fails on a cycle."""
    order, state = [], {}

    def visit(name, path):
        if state.get(name) == "done":
            return
        if state.get(name) == "visiting":
            from ansible.errors import AnsibleFilterError

            raise AnsibleFilterError("component needs form a cycle: " + " -> ".join(path + [name]))
        state[name] = "visiting"
        for need in (registry[name] or {}).get("needs") or []:
            visit(need, path + [name])
        state[name] = "done"
        order.append(name)

    for name in registry:
        visit(name, [])
    return order


class FilterModule:
    def filters(self):
        return {"components_desired": components_desired, "components_order": components_order}
