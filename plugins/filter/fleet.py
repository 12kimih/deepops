"""fleet_drift: what differs between hosts that should be identical.

    {{ hosts | map('extract', hostvars, 'maintenance_fleet_state') ... }}
    {{ states | fleet_drift }}      states: {host: {"kernel": str,
                                                    "packages": {name: version},
                                                    "sysctl": {name: value}}}

Nodes upgraded at different times run different kernels and package versions, so a
job behaves differently depending on where it lands, and a fix applied "everywhere"
is not. One line per difference, naming which hosts have which value. A package
counts only where it is installed on two or more hosts: a package only one host has
(the controller's database) is a role difference, not drift.
"""


def _spread(values):
    """{host: value} -> "v1 on h1,h2; v2 on h3", or None when all agree."""
    by_value = {}
    for host, value in sorted(values.items()):
        by_value.setdefault(str(value), []).append(host)
    if len(by_value) < 2:
        return None
    return "; ".join(
        f"{v} on {','.join(hs)}" for v, hs in sorted(by_value.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    )


def fleet_drift(states):
    lines = []
    spread = _spread({h: s.get("kernel", "?") for h, s in states.items()})
    if spread:
        lines.append(f"kernel: {spread}")
    names = sorted({n for s in states.values() for n in s.get("sysctl", {})})
    for name in names:
        spread = _spread({h: s.get("sysctl", {}).get(name, "unset") for h, s in states.items()})
        if spread:
            lines.append(f"sysctl {name}: {spread}")
    installed = {}
    for host, state in states.items():
        for name, version in state.get("packages", {}).items():
            installed.setdefault(name, {})[host] = version
    for name in sorted(installed):
        if len(installed[name]) > 1:
            spread = _spread(installed[name])
            if spread:
                lines.append(f"package {name}: {spread}")
    return lines


class FilterModule:
    def filters(self):
        return {"fleet_drift": fleet_drift}
