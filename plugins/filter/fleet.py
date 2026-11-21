"""fleet_drift: what differs between hosts that should be identical.

    {{ hosts | map('extract', hostvars, 'maintenance_fleet_state') ... }}
    {{ states | fleet_drift }}      states: {host: {"kernel": str,
                                                    "packages": {name: version},
                                                    "sysctl": {name: value},
                                                    "nvidia_loaded": str,
                                                    "nvidia_on_disk": str,
                                                    "reboot_pending": str,
                                                    "machine_id": str}}

Nodes upgraded at different times run different kernels and package versions, so a
job behaves differently depending on where it lands, and a fix applied "everywhere"
is not. One line per difference, naming which hosts have which value. A package
counts only where it is installed on two or more hosts: a package only one host has
(the controller's database) is a role difference, not drift.

Equal package versions do not mean equal running code: a host also drifts when it
runs other than what it has installed, a pending reboot (reboot_pending: the
packages that asked for it, or "yes") or an NVIDIA module loaded at another version or
flavor than the one on disk (nvidia_loaded vs nvidia_on_disk, "<version> <open|
proprietary>" from plugins/filter/nvidia.py, "" where none). The loaded
module is compared like a package: between the hosts that have one.

One value must differ instead: /etc/machine-id. Hosts cloned from one image share it
until it is regenerated, and systemd (journald, networkd's DHCP identity, D-Bus) then
takes them for one machine.
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
    by_id = {}
    for host, state in sorted(states.items()):
        if state.get("machine_id"):
            by_id.setdefault(state["machine_id"], []).append(host)
    for hosts in sorted(hs for hs in by_id.values() if len(hs) > 1):
        lines.append(
            f"machine-id shared by {','.join(hosts)}: on each but one, "
            "rm /etc/machine-id && systemd-machine-id-setup, then reboot"
        )
    loaded = {h: s["nvidia_loaded"] for h, s in states.items() if s.get("nvidia_loaded")}
    if len(loaded) > 1:
        spread = _spread(loaded)
        if spread:
            lines.append(f"nvidia module loaded: {spread}")
    for host, state in sorted(states.items()):
        on_disk = state.get("nvidia_on_disk", "")
        if state.get("nvidia_loaded") and on_disk and state["nvidia_loaded"] != on_disk:
            lines.append(f"nvidia module on {host}: {state['nvidia_loaded']} loaded, {on_disk} installed")
        if state.get("reboot_pending"):
            lines.append(f"reboot pending on {host}: {state['reboot_pending']}")
    return lines


class FilterModule:
    def filters(self):
        return {"fleet_drift": fleet_drift}
