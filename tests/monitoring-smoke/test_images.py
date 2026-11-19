"""Boot the monitoring images the roles pin on the configuration the roles render.

A version bump is proven here before it reaches a host: each image is the one its role
default pins (the same line a bump edits), and each runs on files the role's own
templates render through Ansible, so a renamed setting, a removed flag or a dashboard
the new Grafana cannot load fails this suite, not a deploy.

  prometheus    promtool checks the rendered prometheus.yml; Prometheus boots on it
                with the alert rules and loads every rule group without error.
  alertmanager  amtool checks the rendered alertmanager.yml (Slack and heartbeat on);
                Alertmanager boots on it.
  grafana       Grafana boots on the rendered grafana.ini and provisioning, and
                provisions every dashboard deepops ships, in the managed folder.
  dcgm-exporter `config validate` accepts the counters file with the flags the unit
                passes (no GPU needed): a release that renames or refuses a field fails
                here, as 4.9.0 refused the clock-event bitmap 4.8 exported.

Containers publish on 127.0.0.1 only, run with --rm and are stopped when a test ends.
Slow (a few image pulls and boots): it runs in the full check, not the commit hook.
Skips when docker is not available.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / ".venv" / "bin" / "ansible-playbook"
DASHBOARDS = {
    "deepops": [ROOT / "src/dashboards" / name for name in ("gpu-nodes.json", "node-exporter-hosts.json")],
    "slurm": [ROOT / "roles/prometheus-slurm-exporter/files/slurm-scheduler.json"],
    "slurm_metrics": [ROOT / "roles/slurm_metrics/files/slurm-gpu-allocation.json"],
}
TEST_PASSWORD = "smoke-test-only"
# What a site sets for the optional parts, so those parts render (and are checked) too.
SITE = {
    "grafana_cfg_pass": TEST_PASSWORD,
    "grafana_root_url": "https://portal.example.com/grafana/",
    "grafana_auth_proxy_header": "X-WEBAUTH-USER",
    "alertmanager_slack_api_url": "https://hooks.slack.example/services/T/B/X",
    "alertmanager_heartbeat_url": "https://hc.example/ping/x",
}


def default(role, name):
    return yaml.safe_load((ROOT / "roles" / role / "defaults" / "main.yml").read_text())[name]


def docker(*args, check=True, timeout=300):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=timeout)


def wait_http(url, timeout=120, auth=None):
    request = urllib.request.Request(url)
    if auth:
        import base64

        request.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    deadline = time.time() + timeout
    while True:
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.read()
        except (urllib.error.URLError, ConnectionError, OSError):
            if time.time() > deadline:
                raise
            time.sleep(1)


class Container:
    """docker run --rm -d, published on 127.0.0.1 at a free port; stopped on exit."""

    def __init__(self, image, port, *args, volumes=()):
        self.image, self.port, self.args, self.volumes = image, port, args, volumes

    def __enter__(self):
        volumes = [a for v in self.volumes for a in ("-v", v)]
        self.id = docker(
            "run", "--rm", "-d", "-u", f"{os.getuid()}:{os.getgid()}", "-p", f"127.0.0.1::{self.port}",
            *volumes, self.image, *self.args,
        ).stdout.strip()  # fmt: skip
        mapping = docker("port", self.id, str(self.port)).stdout.split()[0]
        self.url = "http://" + mapping
        return self

    def logs(self):
        return docker("logs", self.id, check=False).stderr[-3000:]

    def __exit__(self, *exc):
        docker("stop", "-t", "5", self.id, check=False)


@unittest.skipUnless(shutil.which("docker"), "docker not available")
class MonitoringImages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="monitoring-smoke-"))
        cls.tmp.chmod(0o700)
        cls.render()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def render(cls):
        """The roles' templates, rendered by Ansible with their defaults and SITE."""
        out = cls.tmp / "rendered"
        defaults = [
            ROOT / "roles" / r / "defaults/main.yml"
            for r in (
                "deepops_paths",
                "slurm_paths",
                "prometheus_paths",
                "prometheus",
                "alertmanager",
                "grafana",
            )
        ]
        templates = {
            "prometheus/prometheus.yml": "roles/prometheus/templates/prometheus.yml.j2",
            "alertmanager/alertmanager.yml": "roles/alertmanager/templates/alertmanager.yml.j2",
            "grafana/grafana.ini": "roles/grafana/templates/grafana.ini.j2",
            "grafana/provisioning/datasources/prometheus.yml": "roles/grafana/templates/prometheus-datasource.yml.j2",
            "grafana/provisioning/dashboards/prometheus.yml": "roles/grafana/templates/prometheus-dashboard.yml.j2",
        }
        play = [
            {
                "hosts": "localhost",
                "gather_facts": False,
                "vars_files": [str(p) for p in defaults],
                "tasks": [
                    {
                        "ansible.builtin.file": {
                            "path": str((out / dest).parent),
                            "state": "directory",
                            "mode": "0700",
                        }
                    }
                    for dest in templates
                ]
                + [
                    {
                        "ansible.builtin.template": {
                            "src": str(ROOT / src),
                            "dest": str(out / dest),
                            "mode": "0644",
                        }
                    }
                    for dest, src in templates.items()
                ],
            }
        ]
        playbook = cls.tmp / "render.yml"
        playbook.write_text(yaml.safe_dump(play))
        # Extra vars: above the role defaults loaded as vars_files.
        (cls.tmp / "site.json").write_text(json.dumps(SITE))
        # Run from the scratch directory: Ansible's defaults, plus the repository's filters.
        env = {**os.environ, "ANSIBLE_FILTER_PLUGINS": str(ROOT / "plugins/filter")}
        env.pop("ANSIBLE_CONFIG", None)
        result = subprocess.run(
            [str(ANSIBLE), "-i", "localhost,", "-c", "local", "-e", "@site.json", str(playbook)],
            capture_output=True, text=True, env=env, cwd=cls.tmp, timeout=300,
        )  # fmt: skip
        if result.returncode:
            raise AssertionError("rendering the role templates failed:\n" + result.stdout[-3000:])
        cls.rendered = out

    def test_prometheus_loads_its_config_and_every_rule_group(self):
        image = default("prometheus", "prometheus_container")
        config = self.tmp / "prometheus"
        shutil.copytree(self.rendered / "prometheus", config)
        shutil.copytree(ROOT / "roles/prometheus/files/rules", config / "rules")
        (config / "endpoints").mkdir()
        check = docker("run", "--rm", "-u", f"{os.getuid()}", "-v", f"{config}:/etc/prometheus:ro",
                       "--entrypoint", "promtool", image, "check", "config", "/etc/prometheus/prometheus.yml",
                       check=False)  # fmt: skip
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        with Container(
            image, 9090, "--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/tmp/data",
            volumes=[f"{config}:/etc/prometheus:ro"],
        ) as prom:  # fmt: skip
            wait_http(prom.url + "/-/ready")
            groups = json.loads(wait_http(prom.url + "/api/v1/rules"))["data"]["groups"]
            errors = [r for g in groups for r in g["rules"] if r.get("health") == "err"]
            self.assertTrue(groups, prom.logs())
            self.assertEqual(errors, [])

    def test_alertmanager_accepts_its_config_and_boots(self):
        image = default("alertmanager", "alertmanager_container")
        config = self.tmp / "alertmanager"
        shutil.copytree(self.rendered / "alertmanager", config)
        (config / "templates").mkdir()
        shutil.copy(ROOT / "roles/alertmanager/files/slack.tmpl", config / "templates")
        check = docker("run", "--rm", "-u", f"{os.getuid()}", "-v", f"{config}:/etc/alertmanager:ro", "--entrypoint", "amtool", image,
                       "check-config", "/etc/alertmanager/alertmanager.yml", check=False)  # fmt: skip
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        # Warnings (a tunnel, an exposed listener) reach Slack without the mention that
        # pages; only critical alerts carry it.
        for labels, receiver in (
            (["alertname=EgressTunnelRunning", "severity=warning", "host=n1"], "slack"),
            (["alertname=HostDown", "severity=critical", "host=n1"], "slack-critical"),
        ):
            route = docker("run", "--rm", "-u", f"{os.getuid()}", "-v", f"{config}:/etc/alertmanager:ro", "--entrypoint", "amtool",
                           image, "config", "routes", "test", "--config.file=/etc/alertmanager/alertmanager.yml", *labels,
                           check=False)  # fmt: skip
            self.assertEqual(route.stdout.strip(), receiver, route.stdout + route.stderr)
        with Container(
            image, 9093, "--config.file=/etc/alertmanager/alertmanager.yml", "--storage.path=/tmp/data",
            volumes=[f"{config}:/etc/alertmanager:ro"],
        ) as am:  # fmt: skip
            wait_http(am.url + "/-/ready")

    def test_grafana_provisions_every_dashboard(self):
        image = default("grafana", "grafana_container")
        etc, data = self.tmp / "grafana-etc", self.tmp / "grafana-data"
        shutil.copytree(self.rendered / "grafana", etc)
        expected = set()
        for folder, files in DASHBOARDS.items():
            (data / "dashboards" / folder).mkdir(parents=True)
            for path in files:
                shutil.copy(path, data / "dashboards" / folder)
                expected.add(json.loads(path.read_text())["uid"])
        port = default("grafana", "grafana_port")
        with Container(
            image, port, volumes=[f"{etc}:/etc/grafana:ro", f"{data}:/var/lib/grafana"]
        ) as grafana:
            wait_http(grafana.url + "/api/health")
            auth = f"{default('grafana', 'grafana_cfg_user')}:{TEST_PASSWORD}"
            for _ in range(60):  # provisioning finishes shortly after the health check
                found = json.loads(wait_http(grafana.url + "/api/search?type=dash-db", auth=auth))
                if expected <= {d["uid"] for d in found}:
                    break
                time.sleep(1)
            self.assertEqual(expected - {d["uid"] for d in found}, set(), grafana.logs())
            folder = default("grafana", "grafana_dashboard_folder_uid")
            self.assertEqual({d["folderUid"] for d in found if d["uid"] in expected}, {folder})

    def test_dcgm_exporter_accepts_its_counters(self):
        image = default("nvidia-dcgm-exporter", "nvidia_dcgm_container")
        counters = (
            ROOT
            / "roles/nvidia-dcgm-exporter/files"
            / default("nvidia-dcgm-exporter", "nvidia_dcgm_container_custom_metrics_file")
        )
        copy = self.tmp / "dcgm-counters.csv"
        shutil.copy(counters, copy)
        copy.chmod(0o644)
        # The unit's own arguments (templates/docker.dcgm-exporter.service.j2), then the check.
        check = docker("run", "--rm", "-v", f"{copy}:/etc/dcgm-exporter/custom-counters.csv:ro", image,
                       "--collectors", "/etc/dcgm-exporter/custom-counters.csv",
                       "--address", f":{default('nvidia-dcgm-exporter', 'nvidia_dcgm_exporter_port')}",
                       "config", "validate", check=False)  # fmt: skip
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("0 errors, 0 warnings", check.stdout + check.stderr)


if __name__ == "__main__":
    unittest.main()
