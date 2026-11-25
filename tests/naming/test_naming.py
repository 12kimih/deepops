"""One thing, one name (docs/deepops/naming.md): role directories, component keys,
switches, cluster tool modules, alert rule files, freshness metrics and service
accounts follow the same words. What does not yet is listed below with what it is to
become; each test asserts the list is exactly what is left, so a rename takes its line
out and a new mismatch fails."""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/lib"))
from repo_scan import tracked  # noqa: E402

ROLES = ROOT / "roles"

# Role directories named with dashes: role -> what it is to become.
DASHED_ROLES = {
    "dns-config": "dns_config", "docker-login": "docker_login", "easy-build": "easybuild",
    "k8s-internal-container-registry": "k8s_internal_container_registry", "maas-controller": "maas_controller",
    "move-home-dirs": "move_home_dirs", "netapp-trident": "netapp_trident",
    "nfs-client-provisioner": "k8s_nfs_client", "nginx-docker-registry-cache": "nginx_docker_cache",
    "nginx-docker-registry-cache-client": "nginx_docker_cache_client", "nvidia-dcgm-exporter": "dcgm_exporter",
    "nvidia-dgx": "nvidia_dgx", "nvidia-dgx-firmware": "nvidia_dgx_firmware", "nvidia-gpu-operator": "gpu_operator",
    "nvidia-gpu-operator-node-prep": "gpu_operator_node_prep", "nvidia-k8s-gpu-device-plugin": "k8s_gpu_plugin",
    "nvidia-k8s-gpu-feature-discovery": "k8s_gpu_feature_discovery", "nvidia-mig-manager": "mig_manager",
    "nvidia-network-operator": "nvidia_network_operator", "nvidia-peer-memory": "nvidia_peer_memory",
    "prometheus-ipmi-exporter": "ipmi_exporter", "prometheus-node-exporter": "node_exporter",
    "prometheus-slurm-exporter": "(retired)", "standalone-container-registry": "standalone_container_registry",
}  # fmt: skip

# Component keys whose role directory has other words: key -> role today.
KEY_NOT_ROLE = {
    "chrony": "deepops_chrony", "standalone_container_registry": "standalone-container-registry",
    "nginx_docker_cache": "nginx-docker-registry-cache", "nginx_docker_cache_client": "nginx-docker-registry-cache-client",
    "gpu_operator": "nvidia-gpu-operator", "k8s_gpu_plugin": "nvidia-k8s-gpu-device-plugin",
    "k8s_gpu_feature_discovery": "nvidia-k8s-gpu-feature-discovery", "k8s_nfs_client": "nfs-client-provisioner",
    "node_exporter": "prometheus-node-exporter", "ipmi_exporter": "prometheus-ipmi-exporter",
    "dcgm_exporter": "nvidia-dcgm-exporter", "hpcsdk": "nvidia_hpc_sdk", "cuda": "nvidia_cuda",
    "cuda_toolkit": "nvidia_cuda_toolkit", "easybuild": "easy-build", "dcgm": "nvidia_dcgm",
}  # fmt: skip

# One switch for a stack of components is a choice, not a mismatch.
STACK_SWITCHES = {"slurm_enable_monitoring": {"prometheus", "alertmanager", "grafana", "node_exporter",
                                              "ipmi_exporter", "dcgm_exporter", "slurm_metrics", "slurm_usage_report",
                                              "io_usage"}}  # fmt: skip
# Switches not named slurm_enable_<key> / kube_enable_<key>: key -> switch(es) today.
SWITCH_NOT_KEY = {
    "chrony": "chrony_install", "gpu_operator": "deepops_gpu_operator_enabled",
    "standalone_container_registry": ("kube_enable_container_registry", "slurm_enable_container_registry"),
    "k8s_nfs_client": "k8s_nfs_client_provisioner",
    "nginx_docker_cache_client": "slurm_enable_nginx_docker_cache",
    "slurm_backup": "slurm_enable_backup", "job_notice": "slurm_enable_job_notices",
    "open_ondemand": "slurm_install_open_ondemand", "lmod": "slurm_install_lmod",
    "cluster_tools": "slurm_install_cluster_tools", "nfs": ("k8s_deploy_nfs_server", "slurm_enable_nfs_server"),
    "nfs_client": "slurm_enable_nfs_client_nodes", "nvidia_driver": "slurm_cluster_install_nvidia_driver",
    "nvidia_power_limit": "slurm_cluster_configure_gpu_power_limit", "pcie_acs": "slurm_disable_pcie_acs",
    "hpcsdk": "slurm_install_hpcsdk", "cuda": "slurm_install_cuda", "cuda_toolkit": "slurm_install_cuda_toolkit",
    "easybuild": "slurm_install_easybuild", "enroot": "slurm_install_enroot", "podman": "slurm_install_podman",
    "openmpi": "slurm_cluster_install_openmpi", "nhc": "slurm_install_nhc", "dcgm": "install_dcgm",
}  # fmt: skip

# Roles whose cluster tool is a module of other words: role -> module today.
MODULE_NOT_ROLE = {
    "cluster_bot_access": "bot_serve", "container_storage_gc": "container_gc", "egress_policy": "egress_metrics",
    "fail2ban": "bans", "shared_space": "shared_data", "slurm_metrics": "metrics", "slurm_usage_report": "usage_report",
}  # fmt: skip

# Alert rule files that are a component's but not named as it: file -> component.
RULE_FILE_NOT_KEY = {"backup.yml": "slurm_backup", "container-storage.yml": "container_storage_gc"}
# Rule files about the cluster as a whole, not one component.
GENERAL_RULE_FILES = {
    "components.yml",
    "gpu.yml",
    "host.yml",
    "monitoring.yml",
    "power.yml",
    "slurm.yml",
    "targets.yml",
    "tls.yml",
}

# Freshness metrics (time() - X in the rules) not named <prefix>_last_success_timestamp_seconds.
FRESHNESS_NOT_STANDARD = {
    "egress_policy_metrics_timestamp_seconds", "os_updates_metrics_timestamp_seconds",
    "slurm_metrics_updated_timestamp_seconds",
}  # fmt: skip
# Gauges of when a state began, read as an age: not a writer's freshness.
SINCE_METRICS = re.compile(r"\w+_since_seconds")

# Service accounts whose home is neither /var/lib/<name> nor /home/<name>: name -> home.
ACCOUNT_HOME_NOT_NAME = {"slurm": "/local/slurm", "slurmbuild": "{{ deepops_cache_dir }}/build/slurm-deb"}


def registry():
    """{key: {role, switches}} of the component registry, read as text (it is Jinja)."""
    text = (ROLES / "deploy_defaults/vars/main.yml").read_text()
    block = text.split("deepops_component_registry:", 1)[1].split("\ndeepops_components:", 1)[0]
    out, key = {}, None
    for line in block.splitlines():
        m = re.match(r"^  ([a-z_0-9]+):\s*$", line)
        if m:
            key = m.group(1)
            out[key] = {"role": key, "switches": set(), "retired": False}
        elif key:
            if r := re.match(r"^    role: (\S+)", line):
                out[key]["role"] = r.group(1)
            if s := re.search(r'switch: "\{\{ ([a-z0-9_]+) \}\}"', line):
                out[key]["switches"].add(s.group(1))
            if re.match(r"^    retired:", line):
                out[key]["retired"] = True
    return out


class Naming(unittest.TestCase):
    def test_role_directories_are_snake_case(self):
        dashed = {p.relative_to(ROLES).parts[0] for p in tracked("roles")}
        dashed = {name for name in dashed if "-" in name}
        self.assertEqual(dashed, set(DASHED_ROLES))

    def test_a_component_key_is_its_role(self):
        mismatched = {k: c["role"] for k, c in registry().items() if not c["retired"] and c["role"] != k}
        self.assertEqual(mismatched, KEY_NOT_ROLE)

    def test_a_switch_is_slurm_enable_key(self):
        off = {}
        for key, c in registry().items():
            odd = sorted(
                switch for switch in c["switches"]
                if switch not in (f"slurm_enable_{key}", f"kube_enable_{key}")
                and key not in STACK_SWITCHES.get(switch, ())
            )  # fmt: skip
            if odd:
                off[key] = odd[0] if len(odd) == 1 else tuple(odd)
        self.assertEqual(off, SWITCH_NOT_KEY)

    def test_a_cluster_tool_is_named_as_its_role(self):
        found = {}
        for tasks in (p for p in tracked("roles") if p.parent.name == "tasks" and p.suffix == ".yml"):
            role = tasks.parts[-3]
            for module in re.findall(r"module: \"?cluster_tools\.([a-z_]+)", tasks.read_text()):
                if module != role and role not in ("cluster_tools", "cluster_tools_lib"):
                    found[role] = module
        self.assertEqual(found, MODULE_NOT_ROLE)

    def test_a_components_alert_rules_are_named_as_it(self):
        keys = {k.replace("_", "-") for k in registry()} | {
            "os-updates",
            "people",
        }  # os_updates and people: roles, not components
        odd = {}
        for path in tracked("roles/prometheus/files/rules"):
            if path.name in GENERAL_RULE_FILES or path.stem in keys:
                continue
            odd[path.name] = RULE_FILE_NOT_KEY.get(path.name, "?")
        self.assertEqual(odd, RULE_FILE_NOT_KEY)

    def test_freshness_is_last_success_timestamp(self):
        reads = set()
        for path in tracked("roles/prometheus/files/rules"):
            reads |= set(re.findall(r"time\(\) - ([a-z_]+_seconds)", path.read_text()))
        odd = {
            m
            for m in reads
            if not m.endswith("_last_success_timestamp_seconds") and not SINCE_METRICS.fullmatch(m)
        }
        self.assertEqual(odd, FRESHNESS_NOT_STANDARD)

    def test_a_service_accounts_home_is_its_name(self):
        accounts = yaml.safe_load((ROLES / "system_accounts/defaults/main.yml").read_text())[
            "system_accounts"
        ]
        odd = {
            n: a["home"] for n, a in accounts.items() if a.get("home") not in (f"/var/lib/{n}", f"/home/{n}")
        }
        self.assertEqual(odd, ACCOUNT_HOME_NOT_NAME)


if __name__ == "__main__":
    unittest.main()
