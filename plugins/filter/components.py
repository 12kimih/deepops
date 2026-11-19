"""Each host's desired component states, from one registry (docs/deepops/component-state.md).

    deepops_component_registry:          # roles/deploy_defaults/vars/main.yml
      rsyslog_server:
        rules:                           # present when any rule holds
          - {switch: "{{ slurm_enable_rsyslog_server }}", group: slurm-master, first: true}
          - {switch: "{{ kube_enable_rsyslog_server }}", group: kube_control_plane, first: true}
        needs: []                        # components a present one makes present

A rule holds when its switch is on and the host is in its group (only the group's first
host with first: true). Rules of both deploys sit side by side, so a host in the Slurm
and the Kubernetes deploy keeps what either wants. A component another present
component needs is present too.
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
    return problems


def components_desired(registry, groups, host):
    """component -> present or absent for HOST."""
    problems = check(registry)
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
    return {name: "present" if name in present else "absent" for name in registry}


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
