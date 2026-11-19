# Architecture

Directory structure and explanation of this projects architecture

- [Architecture](#architecture)
  - [config.example](#configexample)
  - [docs](#docs)
  - [playbooks](#playbooks)
  - [roles](#roles)
  - [scripts](#scripts)
  - [src](#src)
  - [submodules](#submodules)
  - [virtual](#virtual)
  - [workloads](#workloads)

## config.example
Template folder for `config` folder generated after running `scripts/setup.sh`.
```bash
.
├── config.example
│   ├── containers
│   ├── files
│   ├── group_vars
│   ├── helm
│   ├── host_vars
│   └── playbooks
```

## docs
Documentation for all DeepOps functionality.
```bash
├── docs
│   ├── airgap
│   ├── cloud-native
│   ├── container
│   ├── deepops
│   ├── dev
│   ├── img
│   ├── k8s-cluster
│   ├── misc
│   ├── ngc-ready
│   ├── pxe
│   └── slurm-cluster
```


## playbooks
Playbooks run via Ansible as part of complete Slurm or Kubernetes installation or ad-hoc plays.
```bash
├── playbooks
│   ├── bootstrap
│   ├── container
│   ├── generic
│   ├── k8s-cluster
│   ├── nvidia-dgx
│   ├── nvidia-software
│   ├── provisioning
│   ├── slurm-cluster
│   └── utilities
```


## roles
Roles run as part of the various playbooks. Includes default values and tasks as well.

Every variable has one default, in one role, at the lowest precedence, so `config/`
sets only what differs and an upgrade that adds a variable never breaks it. A value
several roles read lives in a defaults-only role they depend on (`slurm_paths`,
`prometheus_paths`, `deepops_paths`, `nis_defaults`). What a deploy includes, the
switches its imports and plays read, lives in `deploy_defaults`. A play or role reading
another role's default loads that role (a play lists it in its roles, as Kubespray's
plays list `kubespray_defaults`; a role names it in its meta dependencies), and no
reader writes `| default(...)` for a defined variable;
`scripts/validation/tests/test_shared_role_defaults.py` enforces all of this.
`scripts/deepops/settings.py` models what each host resolves from these defaults and
`config/` (see [configuration](configuration.md#finding-variables-to-change)).
<!-- BEGIN GENERATED: role-tree -->
```text
├── roles
│   ├── ai_agent_rules
│   ├── alertmanager
│   ├── autofs
│   ├── bmc
│   ├── build_record
│   ├── cachefilesd
│   ├── cluster_sudoers
│   ├── cluster_tools
│   ├── cluster_tools_lib
│   ├── container_image
│   ├── deepops_chrony
│   ├── deepops_hosts
│   ├── deepops_paths
│   ├── default_target
│   ├── deploy_defaults
│   ├── deploy_record
│   ├── dns-config
│   ├── docker
│   ├── docker-login
│   ├── docker_daemon_json
│   ├── docker_socket_group
│   ├── download
│   ├── easy-build
│   ├── easy-build-packages
│   ├── egress_policy
│   ├── enroot
│   ├── facts
│   ├── free_space
│   ├── grafana
│   ├── host_baseline
│   ├── k8s-internal-container-registry
│   ├── kerberos_client
│   ├── lmod
│   ├── locale
│   ├── maas-controller
│   ├── maintenance
│   ├── managed_dir
│   ├── mofed
│   ├── move-home-dirs
│   ├── netapp-trident
│   ├── netplan
│   ├── nfs
│   ├── nfs-client-provisioner
│   ├── nginx-docker-registry-cache
│   ├── nhc
│   ├── nis_client
│   ├── nis_defaults
│   ├── nis_server
│   ├── node_user_policy
│   ├── nvidia-dcgm-exporter
│   ├── nvidia-dgx
│   ├── nvidia-dgx-firmware
│   ├── nvidia-gpu-operator
│   ├── nvidia-gpu-operator-node-prep
│   ├── nvidia-k8s-gpu-device-plugin
│   ├── nvidia-k8s-gpu-feature-discovery
│   ├── nvidia-mig-manager
│   ├── nvidia-network-operator
│   ├── nvidia-peer-memory
│   ├── nvidia_container_toolkit
│   ├── nvidia_cuda
│   ├── nvidia_cuda_toolkit
│   ├── nvidia_dcgm
│   ├── nvidia_driver
│   ├── nvidia_hpc_sdk
│   ├── nvidia_power_limit
│   ├── nvidia_versions
│   ├── open_ondemand
│   ├── openmpi
│   ├── os_updates
│   ├── package_repos
│   ├── people
│   ├── podman
│   ├── prometheus
│   ├── prometheus-ipmi-exporter
│   ├── prometheus-node-exporter
│   ├── prometheus-slurm-exporter
│   ├── prometheus_paths
│   ├── proxy_defaults
│   ├── pyxis
│   ├── quiet_install
│   ├── recorded_files
│   ├── retired_settings
│   ├── roce_backend
│   ├── root_access
│   ├── rsyslog_client
│   ├── rsyslog_server
│   ├── runai
│   ├── serial_console
│   ├── slurm
│   ├── slurm_backup
│   ├── slurm_metrics
│   ├── slurm_paths
│   ├── slurm_usage_report
│   ├── spack
│   └── standalone-container-registry
```
<!-- END GENERATED: role-tree -->

## scripts
Various scripts used to setup environments and collect information.
```bash
├── scripts
│   ├── airgap
│   ├── deepops
│   ├── generic
│   ├── k8s
│   ├── nginx-docker-cache
│   └── slurm
```

## src
Contains some source material such as GPU Dashboard for Grafana.
```bash
├── src
│   ├── containers
│   ├── dashboards
│   └── repo
```

## submodules
Submodules linked/versioned as part of KubeSpray and Packer-mass setup.
```bash
├── submodules
│   ├── kubespray
│   └── packer-maas
```

## virtual
Various resources for setting up virtual envs used with DeepOps
```bash
├── virtual
│   ├── scripts
│   └── vars_files
```

## workloads
Sample workloads which one can use after setting up their cluster.
```bash
└── workloads
    ├── bit
    ├── examples
    ├── jenkins
    └── services
```
