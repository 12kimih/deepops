# Network Ports

Requirements for open network ports

- [Network Ports](#network-ports)
  - [Kubernetes deployments](#kubernetes-deployments)
  - [Slurm deployments](#slurm-deployments)
  - [Ports deepops configures](#ports-deepops-configures)

DeepOps can be used to deploy a wide variety of network services, many of which are highly customizable, or which can be enabled or disabled for any given cluster deployment.
Additionally, for performance reasons, we typically test DeepOps in an environment where no host-based firewall restricts traffic _between_ cluster nodes.
(Though a firewall may be used to manage external access to the cluster.)

Because of this, we don't currently maintain a comprehensive list of network ports used by the software that DeepOps deploys.
The best way to obtain a comprehensive list of open ports is to deploy first in a test environment
(e.g., using the [virtual cluster functionality](../../virtual)),
and check what ports are required for the particular set of software you wish to deploy.

This page documents a subset of commonly required ports, either by linking to documentation for the software components (e.g., Slurm and Kubernetes),
listing known ports for major services such as NFS,
or listing known ports that we configure within DeepOps itself.

## Kubernetes deployments

- [Kubernetes Ports and Protocols](https://kubernetes.io/docs/reference/ports-and-protocols/)
- SSH (all hosts): 22/tcp
- NFS:
  - All hosts: 111/tcp, 111/udp
  - NFS server: 2049/tcp, 2049/udp
  - [NFS firewall guide](https://tldp.org/HOWTO/NFS-HOWTO/security.html#FIREWALLS)
- The ports deepops' roles configure (the registries, rsyslog): [below](#ports-deepops-configures)
- Monitoring
  - Prometheus, Grafana and Alertmanager web interfaces: the `nodePort` each service
    has in `config/helm/monitoring.yml` (Kubernetes NodePorts)
  - Prometheus node exporter: 9100/tcp (the monitoring chart's default)
  - DCGM exporter: 9400/tcp (the GPU Operator's default)

## Slurm deployments

- [Slurm Network Configuration Guide](https://slurm.schedmd.com/network.html)
- SSH (all hosts): 22/tcp
- NFS:
  - All hosts: 111/tcp, 111/udp
  - NFS server: 2049/tcp, 2049/udp
  - [NFS firewall guide](https://tldp.org/HOWTO/NFS-HOWTO/security.html#FIREWALLS)
- The ports deepops' roles configure: [below](#ports-deepops-configures)

## Ports deepops configures

Each is its role's default; config can change it, and a role runs only where its
component is placed:

<!-- BEGIN GENERATED: ports -->
| Port | Setting | Role |
|---|---|---|
| 443/tcp | `open_ondemand_port` | `open_ondemand_defaults` |
| 514/tcp | `rsyslog_client_tcp_port` | `rsyslog_client` |
| 514/tcp | `rsyslog_server_tcp_port` | `rsyslog_server` |
| 514/udp | `rsyslog_server_udp_port` | `rsyslog_server` |
| 3000/tcp | `grafana_port` | `grafana` |
| 3128/tcp | `nginx_docker_cache_port` | `nginx_docker_cache_defaults` |
| 5000/tcp | `standalone_container_registry_port` | `standalone-container-registry` |
| 6817/tcp | `slurm_slurmctld_port` | `slurm_paths` |
| 8080/tcp | `slurm_exporter_port` | `prometheus-slurm-exporter` |
| 9090/tcp | `prometheus_port` | `prometheus_paths` |
| 9093/tcp | `alertmanager_port` | `prometheus_paths` |
| 9100/tcp | `node_exporter_port` | `prometheus_paths` |
| 9290/tcp | `ipmi_exporter_port` | `prometheus_paths` |
| 9400/tcp | `dcgm_exporter_port` | `prometheus_paths` |
| 31500/tcp | `container_registry_node_port` | `k8s-internal-container-registry` |
<!-- END GENERATED: ports -->
