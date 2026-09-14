# Proxy K8s Cluster

- [Proxy K8s Cluster](#proxy-k8s-cluster)
  - [Playbook k8s-cluster.yml](#playbook-k8s-clusteryml)
    - [Edit proxy.sh](#edit-proxysh)
    - [Using Kubernetes](#using-kubernetes)

Using Proxy with DeepOps (k8s Cluster Install)

Not all environments can freely download and install software for security reasons which is no surprise. However, setting up a proxy for all tasks may not be the best option for all environments. The ansible playbooks and deepops scripts were modified to leverage a proxy if its available but only during setup/provisioning. It's important not only the `HTTP_PROXY` and `HTTPS_PROXY` be configured to gain access to packages, software, and k8s config files, but `NO_PROXY` be also set. Hosts listed in the `NO_PROXY` variable are _not_ used when a HTTP request is made. Why this is important is because services and commands like `kubectl` use the HTTP protocol to access k8s services. It's out of scope to list those services here, best to refer to k8s documentation.

Proxy format: http://user:password@proxyIP:Proxy:Port/

## Playbook k8s-cluster.yml

To prepare using proxies for the installation of DeepOps via the k8s-cluster.yml playbook, there are 2 additional steps required. After downloading deepops via git, edit the script `proxy.sh` before executing step `#2 Set up your provisioning machine`. Then after the Kubernetes cluster is up and running use the proxies to complete the additional steps found in `Using Kubernetes`. Below are the details.

### Edit proxy.sh

Manually edit the file and provide the necessary values for all 3 variables - HTTPS_PROXY, HTTP_PROXY, NO_PROXY. The NO_PROXY variable should have a comma separated list of hostnames, IP addresses, domain names, or a mixture of both. Asterisks can be used as wildcards.

```bash
# Example Proxy details
export http_proxy="http://10.0.2.5:3128"
export https_proxy="http://10.0.2.5:3128"
export no_proxy="localhost,cluster.local,127.0.0.1,::1,10.0.2.10,10.0.2.20,10.0.2.30"
```

When the file holds uncommented values, `scripts/setup.sh` runs its downloads and installs through the proxy. The playbooks do not read it: to proxy them as well, set `http_proxy`, `https_proxy`, `no_proxy` and `proxy_env` in `config/group_vars/all.yml`, where commented examples are provided.

### Using Kubernetes

Before executing the scripts to continue setting up and installing services you can:

1. Run the `scripts/deepops/proxy.sh` script to setup environment variables. Then continue running all the scripts necessary for your environment.

2. Rather than setup the variables for your current shell, you can run each script to use the proxy without impacting your current env. For example:
   `. scripts/deepops/proxy.sh && scripts/k8s/deploy_rook.sh`
