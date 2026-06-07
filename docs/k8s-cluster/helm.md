# Helm

Some services are installed using [Helm](https://helm.sh/), a package manager for Kubernetes.

- [Helm](#helm)
  - [Manual Install](#manual-install)


## Manual Install

Install the Helm client by following the instructions for the OS on your provisioning system: https://helm.sh/docs/intro/install/

If you're using Linux, the script `scripts/k8s/install_helm.sh` will set up Helm for the current user.

Helm 3 (the current major version) is **client-only** -- the old server-side
component (Tiller) was removed, so there is nothing to deploy or initialize in
the cluster. Once the `helm` binary is on your `PATH` you can use it directly
against the cluster (its kubeconfig is read from `~/.kube/config`).
