# RAPIDS Dask

RAPIDS with Dask

- [RAPIDS Dask](#rapids-dask)
  - [Introduction](#introduction)
  - [Installation](#installation)
    - [Kubeflow](#kubeflow)
    - [Stand-alone](#stand-alone)

## Introduction
[Dask](https://dask.org) allows distributed computation in Python.
[RAPIDS](https://rapids.ai/) adds gpu acceleration to machine learning.

Dask has tight kubernetes integration that allows you to scale up/down your Dask cluster either from within your python code or using the `kubectl` utility.

## Installation

### Kubeflow

If Kubeflow has already been installed using the [DeepOps Kubeflow Deployment Guide](kubeflow.md) there are no additional K8S setup steps required.

When deploying through Kubeflow, it is necessary to ensure that a proper Docker image, entrypoint, and cmd have been specified; or Kubeflow will not properly start Jupyter and the service will immediately fail. See the [Dask Kubernetes](../../workloads/examples/k8s/dask-rapids/docker/Dockerfile) Dockerfile for an example.

### Stand-alone

Deploy Kubernetes by following the [DeepOps Kubernetes Deployment Guide](README.md)

Deploy the [LoadBalancer](ingress.md#load-balancer)

Deploy Dask (the example deploy script and its config live under
[`workloads/examples/k8s/dask-rapids`](../../workloads/examples/k8s/dask-rapids)):

> This example is unmaintained and does not run on a current cluster as is: `deploy.sh` installs the
> retired `stable/dask` Helm chart and uses `kubectl get --export`, removed in Kubernetes 1.18. It also
> reads `config/helm/rapids-dask.yml` and `config/k8s/rapids-dask-sa.yml` relative to the working
> directory, while the bundled copies are in `helm/` and `k8s/`.

```bash
cd workloads/examples/k8s/dask-rapids

# Optionally, modify the chart configuration
vi helm/rapids-dask.yml

# Optionally, modify the K8S resources
vi k8s/rapids-dask-sa.yml

# Deploy
./deploy.sh
```

> For more configuration options, see: https://github.com/rmccorm4/charts/tree/update-stable-dask/stable/dask
> For more information about scaling dask in kubernetes see the included example notebooks.
