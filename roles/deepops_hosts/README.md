<!-- Vendored into DeepOps from https://github.com/DeepOps/ansible-role-hosts at commit c21a676088c07db3db3ac9332cf584eeb068e3fd. -->
# Ansible role `deepops_hosts`

An Ansible role for managing the hosts file (`/etc/hosts`). Specifically, the responsibilities of this role are to:

- Add the default localhost entry;
- Add entries for basic IPv6 addresses, e.g. ip6-localnet (optional);
- Add entries for Ansible managed hosts (optional);
- Add entries specified in Yaml (optional, see below);
- Add entries specified in text files (optional).

## Selecting the correct network interface

When a host has multiple network interfaces, an entry in the hosts file will be generated for each interface with the interface name as suffix.
If `hosts_network_interface` is selected, that IP address will also have a fully qualified domain name included.  If `hosts_default_domain` is
set, the FQDN will be generated using it.  Otherwise, `ansible_fqdn` will be used.  However, the value of `ansible_fqdn` can depend on the
existing /etc/hosts file which makes this depend on /etc/hosts already being correct in order to make a correct /etc/hosts, a catch-22.

```
10.10.0.1      node01.example.com node01-eth0 node01
10.30.0.1      node01-eth1
192.168.0.50   node01-ib0
```

If `hosts_network_interface` is not set, `ansible_hostname` and `ansible_fqdn` will be named according to the interface providing the default route.
In cases where `hosts_network_interface` is not set and there is no default route, only the interface-based names will be generated.

`hosts_network_interface` can alternatively be set as a mapping of host name to interface (e.g. `{ head: eth0 }`); hosts not listed use the default-route interface.

The network interfaces lo, docker, nodelocaldns, br- and veth are automatically excluded.  The variable `hosts_exclude_interfaces` can
be used to change the excluded interfaces.

`hosts_interface_domain` allows including a different FQDN for specific interfaces, either cluster wide or per-host

## Requirements

No specific requirements

## Role Variables

None of the variables below are required. When not set, the default setting is applied.

| Variable                                 | Default                              | Comments                                                                                                          |
| :---                                     | :---                                 | :---                                                                                                              |
| `hosts_file_src`                         | `""`                                 | When set, this file becomes the `00-deepops.hosts` fragment in place of the generated entries; other components' fragments are still assembled in |
| `hosts_add_basic_ipv6`                   | false                                | If true, basic IPv6 entries are added (e.g. localhost6, ip6-localnet, etc.)                                       |
| `hosts_add_ansible_managed_hosts`        | false                                | If true, an entry for hosts managed by Ansible is added. (1)                                                      |
| `hosts_add_ansible_managed_hosts_groups` | ['all']                              | Control which host entries are created when using `hosts_add_ansible_managed_hosts` |
| `hosts_entries`                          | []                                   | A list of dicts with custom entries to be added to the hosts file. See below for an example.                      |
| `hosts_file_snippets`                    | []                                   | A list of files containing host file snippets to be added to the hosts file verbatim.                             |
| `hosts_ip_protocol`                      | `ipv4`                               | When adding Ansible managed hosts, this specifies the IP protocol (`ipv4` or `ipv6`)                              |
| `hosts_network_interface`                | ''                                   | When adding Ansible managed hosts, this specifies the network interface for which the IP address is used for the default name |
| `hosts_default_domain`                   | ''                                   | Domain used for the FQDN of the default interface entry (otherwise `ansible_fqdn`)                                |
| `hosts_interface_domain`                 | ''                                   | Domain per interface, or per host per interface, for the interface entries                                        |
| `hosts_exclude_interfaces`               | [lo, docker, nodelocaldns, br-, veth]| Interface name prefixes left out of the hosts file                                                                |
|                                          |                                      |                                                                                                                   |

An interface with several addresses is listed by the one inside `cluster_networks` (roles/network_defaults).

`/etc/hosts` is assembled from `/etc/hosts.d/*.hosts` in name order: this role's part
(`00-deepops.hosts`), then each component's. Another role adds its lines with
`include_role: deepops_hosts, tasks_from: fragment` (`hosts_fragment_name`,
`hosts_fragment_lines`, `[]` removes them), never by editing `/etc/hosts`, which the next
deploy rewrites.

(1) When setting `hosts_add_ansible_managed_hosts`, an entry for the current host will also be added.

Individual hosts file entries can be added with `hosts_entries`, a list of dicts with keys `name`, `ip` and (optional) `aliases`. Example:

```yaml
hosts_entries:
  - name: slashdot
    ip: 216.34.181.45
  - name: gns1
    ip: 8.8.8.8
    aliases:
      - googledns1
      - googlens1
  - name: gns2
    ip: 8.8.4.4
    aliases:
      - googledns2
      - googlens2
```

## Dependencies

`retired_settings` and `network_defaults` (meta/main.yml).

## Example Playbook

`playbooks/generic/hosts.yml`, which `playbooks/slurm-cluster.yml` imports.

## Contributing

This copy is maintained in DeepOps: changes go through this repository and its checks (docs/deepops/testing.md).

## License

BSD, see <LICENSE.md>

## Contributors

This role was inspired by the work of [soplakanets](https://github.com/soplakanets/ansible-role-hosts/) (including the [contribution by astrorafael](https://github.com/soplakanets/ansible-role-hosts/pull/1/files)) and [mivok](https://github.com/mivok/ansible-hosts/).

- [Bert Van Vreckem](https://github.com/bertvv/) (maintainer)
- [Dheeraj Dwivedi](https://github.com/dheerajdwivedi)
- [Ernestas Poskus](https://github.com/ernestas-poskus)
- [Mohammed Naser](https://github.com/mnaser)
