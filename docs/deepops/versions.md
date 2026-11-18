# Component versions

Every version, container image and chart that a DeepOps role pins by default,
generated from `roles/*/defaults/main.yml` by `scripts/docs/generate.py`. The
last column shows where `config.example/` sets a different value. To change a
version, override the variable in your `config/group_vars`; to change the
default, edit the role's defaults and run `scripts/docs/generate.py`.

<!-- BEGIN GENERATED: versions -->
| Role | Variable | Default | config.example |
|---|---|---|---|
| alertmanager | `alertmanager_container` | `prom/alertmanager:v0.34.1` |  |
| deepops_hosts | `hosts_playbook_version` | `1.0.1` |  |
| easy-build-packages | `sm_files_repo_version` | `4ef7ae6cc2284f69412a8db5e10dddd92024eeab` |  |
| enroot | `enroot_version` | `4.2.0` |  |
| grafana | `grafana_container` | `grafana/grafana:13.2.4` |  |
| k8s-internal-container-registry | `container_registry_version` | `2.1.0` |  |
| mofed | `mofed_doca_version` | `3.3.0` |  |
| netapp-trident | `trident_version` | `26.02.1` |  |
| netapp-trident | `trident_helm_chart_version` | `100.2602.1` |  |
| nfs-client-provisioner | `k8s_nfs_client_chart_version` | `4.0.18` |  |
| nginx-docker-registry-cache | `nginx_docker_cache_image` | `rpardini/docker-registry-proxy:0.7.0` |  |
| nhc | `nhc_version` | `1.4.3` |  |
| nvidia-dcgm-exporter | `nvidia_dcgm_container_version` | `4.5.3-4.8.2-distroless` |  |
| nvidia-dgx-firmware | `firmware_update_tag` | `21.11.2` |  |
| nvidia-dgx-firmware | `firmware_update_container` | `nvfw-dgxa100_21.11.2_211102.tar.gz` |  |
| nvidia-gpu-operator | `gpu_operator_chart_version` | `v26.7.2` |  |
| nvidia-gpu-operator | `gpu_operator_driver_version` | `580.126.20` |  |
| nvidia-k8s-gpu-device-plugin | `k8s_gpu_plugin_chart_version` | `0.20.1` |  |
| nvidia-k8s-gpu-feature-discovery | `k8s_gpu_feature_discovery_chart_version` | `0.20.1` |  |
| nvidia-mig-manager | `mig_manager_version` | `0.15.1` |  |
| nvidia_container_toolkit | `nvidia_container_toolkit_version` | `1.19.1-1` |  |
| nvidia_driver | `nvidia_driver_branch` | `580` | `595` |
| nvidia_hpc_sdk | `hpcsdk_major_version` | `26` |  |
| nvidia_hpc_sdk | `hpcsdk_minor_version` | `3` |  |
| openmpi | `openmpi_version` | `5.0.10` |  |
| prometheus | `prometheus_container` | `prom/prometheus:v3.15.0` |  |
| prometheus-ipmi-exporter | `ipmi_exporter_container` | `quay.io/prometheuscommunity/ipmi-exporter:v1.10.1` |  |
| prometheus-node-exporter | `node_exporter_container` | `quay.io/prometheus/node-exporter:v1.12.1` |  |
| prometheus-slurm-exporter | `slurm_exporter_container` | `deepops/prometheus-slurm-exporter:2.0.0` |  |
| pyxis | `slurm_pyxis_version` | `0.24.0` |  |
| runai | `runai_kai_chart_version` | `v0.18.5` |  |
| runai | `runai_gpu_operator_min_version` | `v26.7.1` |  |
| runai | `runai_chart_version` | `3.0.20` |  |
| runai | `runai_platform_ingress_chart_version` | `1.54.2` |  |
| runai | `runai_gateway_api_version` | `v1.4.0` |  |
| runai | `runai_gateway_controller_version` | `v2.2.1` |  |
| runai | `runai_platform_prometheus_min_version` | `88.6.5` |  |
| slurm | `slurm_version` | `25.11.6` |  |
| slurm | `hwloc_version` | `2.13.0` |  |
| slurm | `pmix_version` | `5.0.10` |  |
| spack | `spack_version` | `v1.2.2` |  |
| standalone-container-registry | `standalone_container_registry_image` | `registry:3.1.2` |  |
<!-- END GENERATED: versions -->
