"""Each host's desired component states, from one registry (docs/deepops/component-state.md).

    deepops_component_registry:          # roles/deploy_defaults/vars/main.yml
      rsyslog_server:
        rules:                           # present when any rule holds
          - {switch: "{{ slurm_enable_rsyslog_server }}", group: slurm-master, first: true}
          - {switch: "{{ kube_enable_rsyslog_server }}", group: kube_control_plane, first: true}
        needs: []                        # components a present one makes present
        removal: explicit                # optional: never removed by a switch or group
        role: prometheus-node-exporter   # optional: the role, where it is not the key
        managed: "{{ docker_install }}"  # optional: false leaves it to the site (kept)
        retired: "why, since when"       # optional: absent everywhere (no rules, no needs)

A component whose managed is false is the site's own (Docker on DGX OS, say): the map
says unmanaged everywhere, whatever wants it, and every phase passes kept to its role.

The key is the component's variable prefix (<key>_state, <key>_items,
deepops_components.<key>); role names its role directory where that differs.

A rule holds when its switch is on and the host is in its group (only the group's first
host with first: true). Rules of both deploys sit side by side, so a host in the Slurm
and the Kubernetes deploy keeps what either wants. A component another present
component needs is present too.

A component with removal: explicit (the driver, authentication, Slurm, MUNGE, the NFS
server: removing them reboots nodes, locks users out or hangs every NFS client) is
never removed because a rule stopped holding: it is kept (its role does nothing) unless
the site names it in deepops_component_removals, which makes it absent on every host no
rule wants it on (and present where one does).

A retired component (retired: says why and since when) is a code fact, not a site
choice: it is absent on every host, whatever removal says, so its role's absent path
runs everywhere, adopting what deployments before component records left. Only a
component the site manages (managed false) stays unmanaged. It has no rules or needs and
nothing needs it; a deploy imports its play in the removal phase only. Once every site
has converged, one commit deletes its entry, role and play (with retired_settings
entries for any of its settings a site may still set).

"""

from pathlib import Path

TRUE = {"true", "yes", "on", "1"}


def _on(value):
    return value is True or (isinstance(value, str) and value.strip().lower() in TRUE)


def check(registry, roles=None):
    """Problems in a registry, as messages; empty means valid. With ROLES (the roles
    directory), each component's role must be there too."""
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
        if "retired" in (c or {}):
            if not isinstance(c["retired"], str) or not c["retired"].strip():
                problems.append(f"{name}.retired: say why and since when")
            if c.get("rules") or c.get("needs"):
                problems.append(f"{name}.retired: a retired component has no rules or needs")
        retired_needs = [n for n in (c or {}).get("needs") or [] if "retired" in (registry.get(n) or {})]
        if retired_needs and "retired" not in (c or {}):
            problems.append(f"{name}.needs: {', '.join(retired_needs)} is retired")
        if roles and not (Path(roles) / component_role(registry, name)).is_dir():
            problems.append(f"{name}.role: no role {component_role(registry, name)}")
    return problems


def component_role(registry, name):
    """The role directory of component NAME: its role, else its key."""
    return (registry[name] or {}).get("role") or name


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
        if not _on((registry[name] or {}).get("managed", True)):
            return "unmanaged"
        if "retired" in (registry[name] or {}):
            return "absent"
        if name in present:
            return "present"
        if (registry[name] or {}).get("removal") == "explicit" and name not in removals:
            return "kept"
        return "absent"

    return {name: state(name) for name in registry}


PHASES = ("converge", "install", "remove", "require")


def component_phase(state, phase="converge"):
    """What a play does with a component in PHASE: converge applies the desired state,
    install only installs (an absent component waits for remove), remove only removes
    (a deploy runs it last, in reverse dependency order). require installs it whatever
    the map says, for a playbook that needs it on its own hosts (ngc-ready-server.yml).
    A component the site manages (unmanaged) is kept in every phase; kept means do
    nothing."""
    if phase not in PHASES:
        from ansible.errors import AnsibleFilterError

        raise AnsibleFilterError(f"deepops_phase must be one of {', '.join(PHASES)}, not {phase}")
    if phase == "install" and state == "absent":
        return "kept"
    if phase == "remove" and state != "absent":
        return "kept"
    if state == "unmanaged":
        return "kept"
    if phase == "require":
        return "present"
    return state


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


def component_hosts(registry, groups, component, removals=()):
    """The hosts where COMPONENT is installed (present, or kept: an explicit-removal
    component a rule stopped wanting stays installed), in inventory order. What another
    host serves or watches follows the same map: Prometheus scrapes each exporter there."""
    hosts = list(dict.fromkeys(h for g in groups.values() for h in (g or [])))
    installed = ("present", "kept")
    return [h for h in hosts if components_desired(registry, groups, h, removals).get(component) in installed]


class FilterModule:
    def filters(self):
        return {
            "components_desired": components_desired,
            "components_order": components_order,
            "component_phase": component_phase,
            "component_hosts": component_hosts,
        }
