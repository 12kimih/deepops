# Kubernetes Usage

Kubernetes Usage Guide

- [Kubernetes Usage](#kubernetes-usage)
  - [Introduction](#introduction)
  - [Simple Commands](#simple-commands)
  - [Simple PyTorch Job](#simple-pytorch-job)
  - [Using NGC Containers with Kubernetes and Launching Jobs](#using-ngc-containers-with-kubernetes-and-launching-jobs)

## Introduction

Most of the following examples can be configured and executed through the Kubernetes Dashboard. For a basic run-through on how to leverage the Kubernetes Dashboard, please see the [official documentation](https://kubernetes.io/docs/tasks/access-application-cluster/web-ui-dashboard/). The following examples use `kubectl` on a control-plane node instead.

## Simple Commands

Get a list of the nodes in the cluster:

```bash
kubectl get nodes
```

Get a list of running pods in the cluster:

```bash
kubectl get pods --all-namespaces
```

## Simple PyTorch Job

1. Run the job.

   A simple PyTorch Job can be run via `kubectl` using the following yml:

   ```bash
   kubectl create -f workloads/examples/k8s/pytorch-job.yml
   ```

   Take a look at the yml and observe that:

   - we are pulling a pytorch container from the NGC registry
   - a single GPU resource is requested
   - the Kubernetes object we are creating is a `job` which spawns `pod` and runs this pod to completion a single time

2. Check on the job.

   ```bash
   kubectl get jobs
   ```

3. Monitor the pod that's spawned from the job.

   ```bash
   kubectl get pods
   ```

   Follow the logs for the pod:

   ```bash
   kubectl logs -f <pytorch-job-pod>
   ```

4. Delete the job (and the corresponding pod).

   ```bash
   kubectl delete job pytorch-job
   ```

## Using NGC Containers with Kubernetes and Launching Jobs

[NVIDIA GPU Cloud (NGC)](https://docs.nvidia.com/ngc/ngc-introduction) hosts a catalog of GPU-optimized containers -- deep learning frameworks such as PyTorch and TensorFlow, plus HPC and inference software -- for single- and multi-GPU systems. They are delivered ready-to-run, including all necessary dependencies such as the CUDA runtime and NVIDIA libraries.

To access the NGC container registry via Kubernetes, add a secret which will be employed when Kubernetes asks NGC to pull container images from it.

1. Generate an NGC API Key, which will be used for the Kubernetes secret.

   - Sign in at https://ngc.nvidia.com/
   - Open **Setup** from the account menu and generate an API key

2. Using the NGC API Key, create a Kubernetes secret so that Kubernetes will be able to pull container images from the NGC registry. Create the secret by running the following command on a control-plane node (substitute the registered email account and secret in the appropriate locations).

   ```bash
   kubectl create secret docker-registry nvcr.dgxkey --docker-server=nvcr.io --docker-username=\$oauthtoken --docker-email=<email> --docker-password=<NGC API Key>
   ```

3. Check that the secret exists.

   ```bash
   kubectl get secrets
   ```

4. You can now use the secret to pull custom NGC images by using the `imagePullSecrets` attribute. For example:

   ```yml
   apiVersion: batch/v1
   kind: Job
   metadata:
     name: pytorch-job
   spec:
     backoffLimit: 5
     template:
       spec:
         imagePullSecrets:
           - name: nvcr.dgxkey
         containers:
           - name: pytorch-container
             image: nvcr.io/nvidia/pytorch:19.02-py3
             command: ["/bin/sh"]
             args: ["-c", "python /workspace/examples/upstream/mnist/main.py"]
             resources:
               limits:
                 nvidia.com/gpu: 1
         restartPolicy: Never
   ```
