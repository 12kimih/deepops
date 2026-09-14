# Ingress

Load Balancer and Ingress

- [Ingress](#ingress)
  - [Introduction](#introduction)
  - [Load Balancer](#load-balancer)
  - [Ingress controller](#ingress-controller)

## Introduction
Kubernetes provides a variety of mechanisms to expose pods in your cluster to external networks.
Two key concepts for routing traffic to your services are:

- [Load Balancers](https://kubernetes.io/docs/concepts/services-networking/#loadbalancer), which expose an external IP and route traffic to one or more pods inside the cluster.
- [Ingress controllers](https://kubernetes.io/docs/concepts/services-networking/ingress/), which provide a mapping between external HTTP routes and internal services.
  Ingress controllers are typically exposed using a Load Balancer external IP.

DeepOps provides scripts you can run to configure a simple Load Balancer and/or Ingress setup:

## Load Balancer

Modify the `IPAddressPool` in `config/helm/metallb-resources.yml` to configure the IP range that the load balancer will hand out (`config/helm/metallb.yml` holds the chart values).

Run the script to deploy the load balancer:

```bash
./scripts/k8s/deploy_loadbalancer.sh
```

This script will set up a software-based L2 Load Balancer using [MetalLB](https://metallb.universe.tf/)

## Ingress controller

When MetalLB is installed, the script deploys the Ingress controller with
`workloads/examples/k8s/ingress-loadbalancer.yml`, so it is assigned an external IP managed by the Load Balancer.

Without MetalLB (for example, when you do not control IP assignment on your subnet), it uses
`workloads/examples/k8s/ingress-nodeport.yml` instead, exposing ingress routes on node ports reachable
via the IP of any node. To use your own chart values, set `HELM_INGRESS_CONFIG` to their path.

Run the script to deploy the Ingress controller:

```bash
./scripts/k8s/deploy_ingress.sh
```

This script will set up an Ingress controller based on [NGINX](https://github.com/kubernetes/ingress-nginx).

---

The different examples and optional services included with DeepOps may use different mechanisms to provide external access.
Depending on the config, each may:

- Use an Ingress to get an HTTP route on the shared NGINX IP.
- Use the Load Balancer directly and get their own external IP.
- Use a [NodePort](https://kubernetes.io/docs/concepts/services-networking/#nodeport) config to expose themselves via a local port on the actual nodes.

For more detail on Kubernetes networking, and the different ways that services can be accessed, see the [official documentation on service networking concepts](https://kubernetes.io/docs/concepts/services-networking/).
