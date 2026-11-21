"""Networks, addresses and ports, on the standard library alone.

networks | networks_invalid             -> the entries that are not a CIDR or address
networks | networks_of_version(4)       -> the IPv4 ones, normalized (6: the IPv6 ones)
networks | networks_netmask             -> the IPv4 ones as "NETMASK NETWORK" (securenets)
'192.0.2.7' | ip_in_networks(networks)  -> whether any network holds the address
addresses | addresses_in_networks(networks) -> those a network holds, in order
22 | port_in_ports([22, '60000-60010'])  -> whether a port is listed or in a range
hostvars | cluster_addresses(hosts, networks)
                                        -> {host: its address inside networks, or ""}

A host's cluster address (roles/network_defaults) is its inventory cluster_address when
set, else the first of its gathered addresses inside the networks: the default route's
address first, then the others in the order the facts list them. Another host's role
defaults are not in hostvars, so a role reads its peers' addresses through this filter.
"""

import ipaddress


def _network(value):
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError:
        return None


def networks_invalid(networks):
    return [n for n in networks or [] if _network(n) is None]


def networks_of_version(networks, version):
    return [str(net) for net in map(_network, networks or []) if net and net.version == int(version)]


def ip_in_networks(address, networks):
    try:
        ip = ipaddress.ip_address(str(address).strip())
    except ValueError:
        return False
    return any(
        net is not None and ip.version == net.version and ip in net for net in map(_network, networks or [])
    )


def addresses_in_networks(addresses, networks):
    return [a for a in addresses or [] if ip_in_networks(a, networks)]


def networks_netmask(networks):
    return [
        f"{net.netmask} {net.network_address}"
        for net in map(_network, networks or [])
        if net and net.version == 4
    ]


def _address(host_vars, networks):
    """One host's cluster address from its variables (a hostvars entry)."""
    pinned = host_vars.get("cluster_address") if hasattr(host_vars, "get") else None
    if pinned:
        return str(pinned)
    facts = (host_vars.get("ansible_facts") if hasattr(host_vars, "get") else None) or {}
    candidates = [
        (facts.get("default_ipv4") or {}).get("address"),
        (facts.get("default_ipv6") or {}).get("address"),
        *(facts.get("all_ipv4_addresses") or []),
        *(facts.get("all_ipv6_addresses") or []),
    ]
    return next(iter(addresses_in_networks([str(a) for a in candidates if a], networks)), "")


def cluster_addresses(hostvars, hosts, networks):
    return {host: (_address(hostvars[host], networks) if host in hostvars else "") for host in hosts or []}


def port_in_ports(port, ports):
    port = int(port)
    for entry in ports or []:
        low, _, high = str(entry).partition("-")
        if int(low) <= port <= int(high or low):
            return True
    return False


class FilterModule:
    def filters(self):
        return {
            "networks_invalid": networks_invalid,
            "networks_of_version": networks_of_version,
            "networks_netmask": networks_netmask,
            "cluster_addresses": cluster_addresses,
            "ip_in_networks": ip_in_networks,
            "addresses_in_networks": addresses_in_networks,
            "port_in_ports": port_in_ports,
        }
