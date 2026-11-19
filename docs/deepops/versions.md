# Component versions

Every version, container image and chart that a DeepOps role pins by default,
generated from `roles/*/defaults/main.yml` by `scripts/docs/generate.py`. The
last column shows where `config.example/` sets a different value. To change a
version, override the variable in your `config/group_vars`; to change the
default, edit the role's defaults and run `scripts/docs/generate.py`.

<!-- BEGIN GENERATED: versions -->
| Role | Variable | Default | config.example |
|---|---|---|---|
| alertmanager | `alertmanager_container` | `prom/alertmanager:v0.35.0@sha256:e638797febd0` |  |
| deploy_defaults | `ngc_ready_cuda_container` | `nvcr.io/nvidia/cuda:13.2.1-base-ubuntu24.04` |  |
| deploy_defaults | `ngc_ready_pytorch_container` | `nvcr.io/nvidia/pytorch:26.05-py3` |  |
| deploy_defaults | `ngc_ready_tensorflow_container` | `nvcr.io/nvidia/tensorflow:25.02-tf2-py3` |  |
| docker | `docker_engine_version` | `29.10.0` |  |
| easy-build | `easybuild_version` | `5.4.2` |  |
| easy-build | `easybuild_setuptools_version` | `82.0.1` |  |
| enroot | `enroot_version` | `4.2.1` |  |
| grafana | `grafana_container` | `grafana/grafana:13.3.1@sha256:0615ec4fbd40` |  |
| k8s-internal-container-registry | `container_registry_version` | `2.1.0` |  |
| mofed | `mofed_doca_version` | `3.3.0` |  |
| netapp-trident | `trident_version` | `26.02.1` |  |
| netapp-trident | `trident_helm_chart_version` | `100.2602.1` |  |
| nfs-client-provisioner | `k8s_nfs_client_chart_version` | `4.0.18` |  |
| nginx-docker-registry-cache | `nginx_docker_cache_image` | `rpardini/docker-registry-proxy:0.8.0@sha256:fe958e7a4b7e` |  |
| nhc | `nhc_version` | `1.5.1` |  |
| nvidia-dcgm-exporter | `nvidia_dcgm_container` | `nvcr.io/nvidia/k8s/dcgm-exporter:4.6.1-4.8.4-distroless@sha256:148b0c025e5f` |  |
| nvidia-dgx | `dgx_os6_driver_branch` | `550` |  |
| nvidia-dgx-firmware | `firmware_update_tag` | `21.11.2` |  |
| nvidia-dgx-firmware | `firmware_update_container` | `nvfw-dgxa100_21.11.2_211102.tar.gz` |  |
| nvidia-gpu-operator | `gpu_operator_chart_version` | `v26.7.2` |  |
| nvidia-gpu-operator | `gpu_operator_driver_version` | `580.126.20` |  |
| nvidia-k8s-gpu-device-plugin | `k8s_gpu_plugin_chart_version` | `0.20.1` |  |
| nvidia-k8s-gpu-feature-discovery | `k8s_gpu_feature_discovery_chart_version` | `0.20.1` |  |
| nvidia-mig-manager | `mig_manager_version` | `0.15.1` |  |
| nvidia_container_toolkit | `nvidia_container_toolkit_version` | `1.20.2-1` |  |
| nvidia_hpc_sdk | `hpcsdk_major_version` | `26` |  |
| nvidia_hpc_sdk | `hpcsdk_minor_version` | `3` |  |
| nvidia_versions | `nvidia_driver_branch` | `580` | `595` |
| openmpi | `openmpi_version` | `5.0.10` |  |
| prometheus | `prometheus_container` | `prom/prometheus:v3.16.0@sha256:e04987641b57` |  |
| prometheus-ipmi-exporter | `ipmi_exporter_container` | `quay.io/prometheuscommunity/ipmi-exporter:v1.10.1@sha256:0662cadaf268` |  |
| prometheus-node-exporter | `node_exporter_container` | `quay.io/prometheus/node-exporter:v1.12.1@sha256:1b4e4438faca` |  |
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
| standalone-container-registry | `standalone_container_registry_image` | `registry:3.1.2@sha256:82f6ff4a7f98` |  |
<!-- END GENERATED: versions -->
