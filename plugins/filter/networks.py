"""Address and port tests for firewall roles, on the standard library alone.

networks | networks_invalid             -> the entries that are not a CIDR or address
networks | networks_of_version(4)       -> the IPv4 ones, normalized (6: the IPv6 ones)
'192.0.2.7' | ip_in_networks(networks)  -> whether any network holds the address
22 | port_in_ports([22, '60000-60010'])  -> whether a port is listed or in a range
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
            "ip_in_networks": ip_in_networks,
            "port_in_ports": port_in_ports,
        }
