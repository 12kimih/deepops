# Helm

Some services are installed using [Helm](https://helm.sh/), a package manager for Kubernetes.

- [Helm](#helm)
  - [Manual Install](#manual-install)


## Manual Install

Install the Helm client by following the instructions for the OS on your provisioning system: https://helm.sh/docs/intro/install/

If you're using Linux, `scripts/k8s/install_helm.sh` installs the Helm version the bundled Kubespray pins, checksum-verified, into `/usr/local/bin` (`HELM_INSTALL_DIR`; `HELM_DOWNLOAD_BASE` for a mirror).

Helm 3 (the current major version) is **client-only** -- the old server-side
component (Tiller) was removed, so there is nothing to deploy or initialize in
the cluster. Once the `helm` binary is on your `PATH` you can use it directly
against the cluster (its kubeconfig is read from `~/.kube/config`).
