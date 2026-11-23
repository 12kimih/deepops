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
<!-- BEGIN GENERATED: tree:config.example -->
```text
├── config.example
│   ├── containers
│   ├── files
│   ├── group_vars
│   ├── helm
│   ├── host_vars
│   ├── playbooks
│   ├── README.md
│   ├── env.sh
│   ├── inventory
│   ├── maas-inventory.yml
│   ├── people.yml
│   ├── requirements.yml
│   └── sensitive-patterns
```
<!-- END GENERATED: tree:config.example -->

## docs
Documentation for all DeepOps functionality.
<!-- BEGIN GENERATED: tree:docs -->
```text
├── docs
│   ├── airgap
│   ├── cloud-native
│   ├── container
│   ├── deepops
│   ├── dev
│   ├── img
│   ├── k8s-cluster
│   ├── misc
│   ├── model-workflows
│   ├── ngc-ready
│   ├── pxe
│   ├── slurm-cluster
│   └── README.md
```
<!-- END GENERATED: tree:docs -->


## playbooks
Playbooks run via Ansible as part of complete Slurm or Kubernetes installation or ad-hoc plays.
<!-- BEGIN GENERATED: tree:playbooks -->
```text
├── playbooks
│   ├── bootstrap
│   ├── container
│   ├── generic
│   ├── k8s-cluster
│   ├── nvidia-dgx
│   ├── nvidia-software
│   ├── provisioning
│   ├── slurm-cluster
│   ├── utilities
│   ├── k8s-cluster.yml
│   ├── model-cache.yml
│   ├── ngc-ready-server.yml
│   └── slurm-cluster.yml
```
<!-- END GENERATED: tree:playbooks -->


## roles
Roles run as part of the various playbooks. Includes default values and tasks as well.

Every variable has one default, in one role, at the lowest precedence, so `config/`
sets only what differs and an upgrade that adds a variable never breaks it. A value
several roles read lives in a defaults-only role they depend on (`slurm_paths`,
`prometheus_paths`, `deepops_paths`, `directory_defaults`, `people_defaults`). What a
deploy includes, the switches its imports and plays read, lives in `deploy_defaults`. A play or role reading
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
│   ├── cluster_bot
│   ├── cluster_bot_access
│   ├── cluster_sudoers
│   ├── cluster_tools
│   ├── cluster_tools_lib
│   ├── component_record
│   ├── container_image
│   ├── container_service
│   ├── container_storage_gc
│   ├── coredump
│   ├── deepops_chrony
│   ├── deepops_hosts
│   ├── deepops_paths
│   ├── default_target
│   ├── deploy_defaults
│   ├── deploy_record
│   ├── directory_defaults
│   ├── disk_quota
│   ├── dns-config
│   ├── dns_resolver
│   ├── docker-login
│   ├── docker
│   ├── docker_daemon_json
│   ├── docker_restart
│   ├── docker_socket_group
│   ├── download
│   ├── easy-build
│   ├── egress_policy
│   ├── enroot
│   ├── facts
│   ├── fail2ban
│   ├── free_space
│   ├── gpu_diag_idle
│   ├── grafana
│   ├── helm_release
│   ├── host_baseline
│   ├── ingress_policy
│   ├── internal_ca
│   ├── job_notice
│   ├── job_scratch
│   ├── k8s-internal-container-registry
│   ├── kerberos_client
│   ├── kernel_modules
│   ├── ldap_server
│   ├── lmod
│   ├── local_accounts
│   ├── locale
│   ├── login_shells
│   ├── maas-controller
│   ├── maintenance
│   ├── maintenance_window
│   ├── managed_dir
│   ├── marked_files
│   ├── model_cache_defaults
│   ├── mofed
│   ├── move-home-dirs
│   ├── netapp-trident
│   ├── netplan
│   ├── network_defaults
│   ├── nfs-client-provisioner
│   ├── nfs
│   ├── nfs_client
│   ├── nfs_common
│   ├── nft_table
│   ├── nginx-docker-registry-cache-client
│   ├── nginx-docker-registry-cache
│   ├── nginx_docker_cache_defaults
│   ├── nhc
│   ├── nis_client
│   ├── nis_server
│   ├── node_user_policy
│   ├── nsswitch
│   ├── nvidia-dcgm-exporter
│   ├── nvidia-dgx-firmware
│   ├── nvidia-dgx
│   ├── nvidia-gpu-operator-node-prep
│   ├── nvidia-gpu-operator
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
│   ├── nvidia_ecc
│   ├── nvidia_hpc_sdk
│   ├── nvidia_power_limit
│   ├── nvidia_versions
│   ├── open_ondemand
│   ├── openmpi
│   ├── os_updates
│   ├── package_repos
│   ├── pam_profile
│   ├── pcie_acs
│   ├── people
│   ├── people_defaults
│   ├── podman
│   ├── prometheus-ipmi-exporter
│   ├── prometheus-node-exporter
│   ├── prometheus-slurm-exporter
│   ├── prometheus
│   ├── prometheus_paths
│   ├── proxy_defaults
│   ├── pyxis
│   ├── quiet_install
│   ├── recorded_files
│   ├── retired_settings
│   ├── root_access
│   ├── rsyslog_client
│   ├── rsyslog_defaults
│   ├── rsyslog_server
│   ├── runai
│   ├── scratch_space
│   ├── serial_console
│   ├── shared_space
│   ├── shared_space_defaults
│   ├── site_secrets
│   ├── slack_defaults
│   ├── slurm
│   ├── slurm_backup
│   ├── slurm_metrics
│   ├── slurm_paths
│   ├── slurm_usage_report
│   ├── source_build
│   ├── sshd
│   ├── sssd_client
│   ├── stale_files
│   ├── standalone-container-registry
│   ├── submit_holds
│   ├── system_accounts
│   ├── textfile_keep
│   ├── user_environment_defaults
│   └── requirements.yml
```
<!-- END GENERATED: role-tree -->

## scripts
Various scripts used to setup environments and collect information.
<!-- BEGIN GENERATED: tree:scripts -->
```text
├── scripts
│   ├── dashboards
│   ├── deepops
│   ├── docs
│   ├── generic
│   ├── k8s
│   ├── nginx-docker-cache
│   ├── slurm
│   ├── validation
│   ├── check.sh
│   ├── common.sh
│   ├── maas_deploy.sh
│   ├── maas_inventory.py
│   ├── push.sh
│   └── setup.sh
```
<!-- END GENERATED: tree:scripts -->

## src
Contains some source material such as GPU Dashboard for Grafana.
<!-- BEGIN GENERATED: tree:src -->
```text
├── src
│   ├── containers
│   └── dashboards
```
<!-- END GENERATED: tree:src -->

## submodules
Submodules linked/versioned as part of KubeSpray and Packer-mass setup.
<!-- BEGIN GENERATED: tree:submodules -->
```text
├── submodules

```
<!-- END GENERATED: tree:submodules -->

## virtual
Various resources for setting up virtual envs used with DeepOps
<!-- BEGIN GENERATED: tree:virtual -->
```text
├── virtual
│   ├── scripts
│   ├── vars_files
│   ├── .gitignore
│   ├── README.md
│   ├── Vagrantfile-centos7
│   ├── Vagrantfile-centos7-full
│   ├── Vagrantfile-centos7-large-slurm
│   ├── Vagrantfile-centos8
│   ├── Vagrantfile-centos8-full
│   ├── Vagrantfile-centos8-large-slurm
│   ├── Vagrantfile-ubuntu18.04
│   ├── Vagrantfile-ubuntu18.04-full
│   ├── Vagrantfile-ubuntu18.04-large-slurm
│   ├── Vagrantfile-ubuntu20.04
│   ├── Vagrantfile-ubuntu20.04-full
│   ├── Vagrantfile-ubuntu20.04-large-slurm
│   ├── Vagrantfile-ubuntu22.04
│   ├── Vagrantfile-ubuntu22.04-full
│   ├── Vagrantfile-ubuntu22.04-large-slurm
│   ├── cluster_up.sh
│   ├── k8s_environment.sh
│   ├── vagrant_shutdown.sh
│   ├── vagrant_startup.sh
│   ├── virtual_inventory
│   ├── virtual_inventory_full
│   └── virtual_inventory_large_slurm
```
<!-- END GENERATED: tree:virtual -->

## workloads
Sample workloads which one can use after setting up their cluster.
<!-- BEGIN GENERATED: tree:workloads -->
```text
├── workloads
│   ├── bit
│   ├── examples
│   ├── jenkins
│   └── services
```
<!-- END GENERATED: tree:workloads -->
