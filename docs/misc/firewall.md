# Firewall

- [Firewall](#firewall)
  - [Introduction](#introduction)
  - [Kubernetes](#kubernetes)
    - [Control plane services](#control-plane-services)
    - [Worker node services](#worker-node-services)
    - [NodePort services](#nodeport-services)
    - [Load Balancer and Ingress](#load-balancer-and-ingress)
    - [Examining an already-running cluster](#examining-an-already-running-cluster)
  - [Slurm](#slurm)
    - [Slurm services](#slurm-services)
    - [MPI traffic](#mpi-traffic)
  - [Host firewall (roles/ingress_policy, roles/egress_policy)](#host-firewall-rolesingress_policy-rolesegress_policy)

## Introduction

Your required firewall configuration will depend on which components of DeepOps you choose to set up.
Different configuration will be needed depending on whether you use Kubernetes or Slurm, how you configure Ingress, etc.
If you're deploying a service not listed below, please check the service setup script or the documentation for the service.

## Kubernetes

In addition to the list below, see
the [Kubernetes documentation](https://kubernetes.io/docs/setup/independent/install-kubeadm/#check-required-ports) for a list of inbound ports required for standing up the minimal cluster services.

### Control plane services

- `tcp/6443`: Kubernetes API server, open to all nodes in cluster
  - Also needs to be open to any hosts that need to access the cluster via `kubectl`
- `tcp/2379-2380`: etcd API, open to control plane nodes
- `tcp/10250`: Kubelet API, open to self and control plane nodes
- `tcp/10259`: kube-scheduler, open to self
- `tcp/10257`: kube-controller-manager, open to self

### Worker node services

- `tcp/10250`: Kubelet API, open to self and control plane
- `tcp/10256`: health check service for kube-proxy

### NodePort services

- `tcp/30000-32767`: default port range for NodePort services

If you choose to expose services via the NodePort mechanism, those services will be assigned a random port from the port range.
These ports will need to be open to any hosts that will need to open the services in question.
This port range can also be configured using the `--service-node-port-range` flag.
If you want to specify a particular port for a given service, in order to control specific access, you can do that using the `nodePort` field in your service configuration.

Pre-configured NodePorts of the monitoring stack (its Helm values, `config.example/helm/monitoring.yml`, or the site's copy in `config/helm/`):

- `tcp/30500`: Prometheus
- `tcp/30400`: AlertManager
- `tcp/30200`: Grafana

If a service is not listed here, it will use a random port from the range.

### Load Balancer and Ingress

- `tcp/80`: default HTTP access, open to all nodes using Ingress
- `tcp/443`: default HTTPS access, open to all nodes using Ingress

The [MetalLB load balancer](/docs/k8s-cluster/ingress.md#load-balancer) defines an IP range for new services that request a load balancer.
The firewall on your worker nodes should allow access to these destination IPs.

The [NGINX ingress controller](/docs/k8s-cluster/ingress.md#ingress-controller) will listen by default on ports 80 and 443.
These ports will need to be open to all nodes that need to access services with configured ingress.

### Examining an already-running cluster

To list the NodePorts and LoadBalancers in use on a running cluster, you can run:

```bash
kubectl get services --all-namespaces | grep -v ClusterIP
```

(We grep out `ClusterIP` results as those are only available to pods within the cluster.)

Similarly, to list the configured ingresses (which will use the HTTP/HTTPS ingress controller), run:

```bash
kubectl get ingresses --all-namespaces
```

## Slurm

### Slurm services

- **slurmctld**: listens on `tcp/6817` by default, or configured with `SlurmctldPort` in `slurm.conf`.
  Should be open to all nodes in the cluster.
- **slurmdbd** (optional): listens on `tcp/6819` by default, or configured with `DbdPort` in `slurmdbd.conf`.
  Should be open to the `slurmctld` host, and to any hosts accessing accounting information.
- **slurmd**: listens on `tcp/6818` by default, or configured with `SlurmdPort` in `slurm.conf`.
  Should be open to all nodes in the cluster.
- **srun**: Instances of `srun` will listen on random ports by default.
  To restrict the ports used by `srun`, use `SrunPortRange` in `slurm.conf`.
  See the man page for `slurm.conf` to determine how many ports will be needed by `srun`.

### MPI traffic

Many common HPC applications use the [Message Passing Interface (MPI)](https://en.wikipedia.org/wiki/Message_Passing_Interface) for communication in multi-node jobs.
While the specifics of network configuration will vary depending on which MPI implementation you use, many implementations assume there is no firewall and will open random ports for both TCP and UDP as needed.

In general, the best practice for an MPI compute cluster is to have a separate, dedicated high-speed network for inter-node communication, with the firewall disabled.
This is often best for performance as well, as it removes any source of contention or jitter for the application.

If you are using a single network for both MPI and management traffic, you should generally allow all TCP and UDP traffic between the nodes in the cluster.

## Host firewall (roles/ingress_policy, roles/egress_policy)

Two nftables tables, one per direction, each its own component: `roles/ingress_policy`
(`slurm_enable_ingress_policy`) lets only the cluster's networks (`cluster_networks`),
trusted interfaces and each host's open ports reach it; `roles/egress_policy`
(`slurm_enable_egress_policy`) limits what users may reach outside the cluster. The
ingress policy replaced the former `ufw-enable.yml` and disables ufw when it takes
over. See [Egress policy](../slurm-cluster/egress-policy.md).

`config.example/playbooks/ufw-disable.yml` stops and disables UFW on all nodes, for a
fresh Ubuntu install whose UFW would block Slurm, munge or NFS.

Ports published with `docker run -p` bypass the input filter, because Docker forwards
them to the container instead of delivering them to the host. Bind such ports to
`127.0.0.1` or an internal address, or use host networking.
