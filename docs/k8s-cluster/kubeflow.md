# Kubeflow

[Kubeflow](https://www.kubeflow.org/docs/) is a K8s native tool that eases the Deep Learning and Machine Learning lifecycle.

- [Kubeflow](#kubeflow)
  - [Introduction](#introduction)
  - [Installation](#installation)
  - [Login information](#login-information)
  - [Other usage](#other-usage)
  - [Kubeflow Admin](#kubeflow-admin)
    - [Uninstalling](#uninstalling)
    - [Modifying Kubeflow configuration](#modifying-kubeflow-configuration)
  - [Debugging common issues](#debugging-common-issues)
    - [No DefaultStorageClass defined or ready](#no-defaultstorageclass-defined-or-ready)

## Introduction

Kubeflow allows users to request specific resources (such as number of GPUs and CPUs), specify Docker images, and easily launch and develop through Jupyter models. Kubeflow makes it easy to create persistent home directories, mount data volumes, and share notebooks within a team.

Kubeflow also offers a full deep learning [pipeline](https://www.kubeflow.org/docs/pipelines/overview/pipelines-overview/) platform that allows you to run, track, and version experiments. Pipelines can be used to deploy code to production and can include all steps in the training process (data prep, training, tuning, etc.) each done through different Docker images. For some examples reference the [examples](../../workloads/examples) directory.

Additionally Kubeflow offers [hyper-parameter tuning](https://github.com/kubeflow/katib) options.

Kubeflow is an [open source project](https://github.com/kubeflow/kubeflow) and is regularly evolving and adding [new features](https://github.com/kubeflow/kubeflow/blob/master/ROADMAP.md).

`deploy_kubeflow.sh` does not install the standalone [MPI Operator](https://github.com/kubeflow/mpi-operator); that step is disabled pending [#737](https://github.com/NVIDIA/deepops/issues/737).

## Installation

Deploy Kubernetes by following the [DeepOps Kubernetes Deployment Guide](README.md)

Kubeflow requires a DefaultStorageClass to be defined. By default DeepOps installs the `nfs-client-provisioner` using the [nfs-client-provisioner.yml playbook](../../playbooks/k8s-cluster/nfs-client-provisioner.yml). This playbook can be re-run manually. As an NFS alternative [Ceph](../../scripts/k8s/deploy_rook.sh), [Trident](../../playbooks/k8s-cluster/netapp-trident.yml) or an alternative StorageClass can be used.

Deploy Kubeflow:

```bash
./scripts/k8s/deploy_kubeflow.sh
```

See the [install docs](https://www.kubeflow.org/docs/started/k8s/overview/) for additional install configuration options.

A local checkout of the [Kubeflow manifests](https://github.com/kubeflow/manifests) (`KUBEFLOW_MANIFESTS_VERSION`, default `v1.7.0`) will be saved to `./config/kubeflow-install/manifests`.

Kubeflow is exposed through the NodePort of the `istio-ingressgateway` service; `./scripts/k8s/deploy_kubeflow.sh -p` prints the URL.

## Login information

The default username is `deepops@example.com` and the default password is `deepops`.

This can be modified before deploying Kubeflow by editing `./config/files/kubeflow/dex-config-map.yaml`.

## Other usage

For the most up-to-date usage information run `./scripts/k8s/deploy_kubeflow.sh -h`.

```console
./scripts/k8s/deploy_kubeflow.sh -h
Usage:
-h    This message.
-p    Print out the connection info for Kubeflow.
-c    Only clone the Kubeflow manifests repo, but do not deploy Kubeflow.
-d    Delete Kubeflow from your system (skipping the CRDs and istio-system namespace that may have been installed with Kubeflow.
-D    Deprecated, same as -d. Previously 'Fully Delete Kubeflow from your system along with all Kubeflow CRDs the istio-system namespace. WARNING, do not use this option if other components depend on istio.'
-x    Deprecated, multi-user auth is now the default.
-w    Wait for Kubeflow homepage to respond (also polls for various Kubeflow Deployments to have an available status).
```

## Kubeflow Admin

### Uninstalling

To uninstall and re-install Kubeflow run:

```bash
./scripts/k8s/deploy_kubeflow.sh -d
# The deploy refuses to overwrite an existing manifests checkout
rm -rf ./config/kubeflow-install/manifests
./scripts/k8s/deploy_kubeflow.sh
```

`-d` deletes the `kubeflow`, `knative-eventing` and `knative-serving` namespaces; it leaves `istio-system` and `cert-manager` in place because other applications commonly use them.

### Modifying Kubeflow configuration

To modify the Kubeflow manifests, you can first clone the manifests directory without deploying Kubeflow:

```bash
./scripts/k8s/deploy_kubeflow.sh -c
```

And then make changes as needed in the manifests directory at `./config/kubeflow-install/manifests`.

The script will not deploy over an existing checkout, so apply the edited manifests yourself with [kustomize](https://github.com/kubernetes-sigs/kustomize) v5 (the script uses v5.1.0, `KUSTOMIZE_URL`):

```bash
cd ./config/kubeflow-install/manifests
while ! kustomize build example | kubectl apply -f -; do sleep 10; done
```

## Debugging common issues

### No DefaultStorageClass defined or ready

A common issue with Kubeflow installation is that no DefaultStorageClass has been defined or that Ceph has not been deployed correctly.

This can be identified if most of the Kubeflow Pods are running and the MySQL pod and several others remain in a Pending state. The GUI may also load and throw a "Profile Error". Run the following to debug further:

```bash
kubectl get pods -n kubeflow
```

> NOTE: Everything should be in a running state.

If `nfs-client-provisioner` was used as the Default StorageClass verify it is running and set:

```bash
helm list | grep nfs-client
kubectl get storageclass | grep default
```

> NOTE: If NFS is being used, the helm application should be in a `deployed` state and `nfs-client` should be the default StorageClass.

If Ceph was installed, verify it is running:

```bash
./scripts/k8s/deploy_rook.sh -w
kubectl get storageclass | grep default
```

> NOTE: If Ceph is being used, `deploy_rook.sh -w` should exit after several seconds and Ceph should be the default StorageClass.

To correct this issue:

1. Uninstall Rook/Ceph: `./scripts/k8s/deploy_rook.sh -d`
2. Uninstall Kubeflow: `./scripts/k8s/deploy_kubeflow.sh -d`
3. Re-install Rook/ceph: `./scripts/k8s/deploy_rook.sh`
4. Poll for Ceph to initialize (wait for this script to exit): `./scripts/k8s/deploy_rook.sh -w`
5. Re-install Kubeflow: `./scripts/k8s/deploy_kubeflow.sh`
