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
import sys
import shutil
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import unrecorded  # noqa: E402
import local_scratch  # noqa: E402
from ansible_yaml import role_defaults, role_variables  # noqa: E402

ANSIBLE = ROOT / ".venv" / "bin" / "ansible-playbook"
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


def limits(role):
    """The CPU and memory limits the role's unit gives its container (roles/container_service),
    so an image that cannot start within them fails here, not on a host."""
    defaults = role_defaults(role)
    (cpus,) = [v for k, v in defaults.items() if k.endswith("_max_cpu")]
    (memory,) = [v for k, v in defaults.items() if k.endswith("_max_memory")]
    return [f"--cpus={cpus}"] * bool(cpus) + [f"--memory={memory}", f"--memory-swap={memory}"] * bool(memory)


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
        except urllib.error.HTTPError as e:  # a server still starting (503): closed
            with e:
                if time.time() > deadline:
                    raise
        except (urllib.error.URLError, ConnectionError, OSError):
            if time.time() > deadline:
                raise
        time.sleep(1)


class Container:
    """docker run --rm -d, published on 127.0.0.1 at a free port; stopped on exit."""

    def __init__(self, image, port, *args, mounts=(), limits=()):
        self.image, self.port, self.args, self.mounts, self.limits = image, port, args, mounts, limits

    def __enter__(self):
        mounts = [a for m in self.mounts for a in ("--mount", m)]
        self.id = docker(
            "run", "--rm", "-d", "-u", f"{os.getuid()}:{os.getgid()}", "-p", f"127.0.0.1::{self.port}",
            *self.limits, *mounts, self.image, *self.args,
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
        # Bind-mounted into the containers: where the Docker daemon sees it too.
        cls.tmp = local_scratch.directory("monitoring-smoke", docker=True)
        cls.render()

    @classmethod
    def tearDownClass(cls):
        local_scratch.remove(cls.tmp)

    @classmethod
    def render(cls):
        """The roles' templates, rendered by Ansible with their variables and SITE: each
        role's defaults and vars as a play sees them, its meta dependencies' included
        (tests/lib role_variables): a hand-kept list of files missed a shared-defaults
        role a role came to depend on, and defaults alone missed a dependency's vars."""
        out = cls.tmp / "rendered"
        values = {}
        for role in ("prometheus", "alertmanager", "grafana"):
            values.update(role_variables(role))
        defaults = cls.tmp / "defaults.yml"
        defaults.write_text(yaml.safe_dump(values))
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
                "vars_files": [str(defaults)],
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
        env = unrecorded.env(ANSIBLE_FILTER_PLUGINS=str(ROOT / "plugins/filter"))
        env.pop("ANSIBLE_CONFIG", None)
        result = subprocess.run(
            [str(ANSIBLE), "-i", "localhost,", "-c", "local", "-e", "@site.json", str(playbook)],
            capture_output=True, text=True, env=env, cwd=cls.tmp, timeout=unrecorded.TIMEOUT,
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
        check = docker("run", "--rm", *limits("prometheus"), "-u", f"{os.getuid()}", "--mount", f"type=bind,src={config},dst=/etc/prometheus,readonly",
                       "--entrypoint", "promtool", image, "check", "config", "/etc/prometheus/prometheus.yml",
                       check=False)  # fmt: skip
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        with Container(
            image, 9090, "--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/tmp/data",
            mounts=[f"type=bind,src={config},dst=/etc/prometheus,readonly"], limits=limits("prometheus"),
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
        check = docker("run", "--rm", *limits("alertmanager"), "-u", f"{os.getuid()}", "--mount", f"type=bind,src={config},dst=/etc/alertmanager,readonly", "--entrypoint", "amtool", image,
                       "check-config", "/etc/alertmanager/alertmanager.yml", check=False)  # fmt: skip
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        # Warnings (a tunnel, an exposed listener) reach Slack without the mention that
        # pages; only critical alerts carry it.
        for labels, receiver in (
            (["alertname=EgressTunnelRunning", "severity=warning", "host=n1"], "slack"),
            (["alertname=HostDown", "severity=critical", "host=n1"], "slack-critical"),
        ):
            route = docker("run", "--rm", *limits("alertmanager"), "-u", f"{os.getuid()}", "--mount", f"type=bind,src={config},dst=/etc/alertmanager,readonly", "--entrypoint", "amtool",
                           image, "config", "routes", "test", "--config.file=/etc/alertmanager/alertmanager.yml", *labels,
                           check=False)  # fmt: skip
            self.assertEqual(route.stdout.strip(), receiver, route.stdout + route.stderr)
        with Container(
            image, 9093, "--config.file=/etc/alertmanager/alertmanager.yml", "--storage.path=/tmp/data",
            mounts=[f"type=bind,src={config},dst=/etc/alertmanager,readonly"], limits=limits("alertmanager"),
        ) as am:  # fmt: skip
            wait_http(am.url + "/-/ready")

    def test_a_slack_message_lists_what_fires_and_a_resolved_one_is_short(self):
        """roles/alertmanager/files/slack.tmpl rendered by this amtool on alert data: one
        message per kind of alert (group_by alertname), only what still fires listed, a
        description they share once, and a resolved group one struck line per host."""
        image = default("alertmanager", "alertmanager_container")
        data = self.tmp / "slack-cases"
        data.mkdir()
        shutil.copy(ROOT / "roles/alertmanager/files/slack.tmpl", data)

        def alert(host, status):
            return {"status": status, "labels": {"alertname": "HostDown", "severity": "critical", "host": host},
                    "annotations": {"summary": f"{host} is down", "description": "No scrape for 5 minutes."}}  # fmt: skip

        cases = {
            "firing": ("firing", [alert(h, "firing") for h in ("n1", "n2", "n3")],
                       ":small_red_triangle: Critical: HostDown (3) || *n1 is down*\n*n2 is down*\n*n3 is down*\nNo scrape for 5 minutes."),
            "mixed": ("firing", [alert("n1", "firing"), alert("n2", "resolved"), alert("n3", "resolved")],
                      ":small_red_triangle: Critical: HostDown on n1 || *n1 is down*\nNo scrape for 5 minutes.\n_2 resolved since_"),
            "resolved": ("resolved", [alert(h, "resolved") for h in ("n1", "n2", "n3")],
                         ":heavy_check_mark: Resolved: HostDown (3) || ~n1 is down~\n~n2 is down~\n~n3 is down~"),
        }  # fmt: skip
        for name, (status, alerts, expected) in cases.items():
            (data / f"{name}.json").write_text(json.dumps({
                "receiver": "slack", "status": status, "alerts": alerts, "groupLabels": {"alertname": "HostDown"},
                "commonLabels": {"alertname": "HostDown", "severity": "critical"},
                "commonAnnotations": {"description": "No scrape for 5 minutes."}, "externalURL": "http://am",
            }))  # fmt: skip
            with self.subTest(case=name):
                out = docker("run", "--rm", *limits("alertmanager"), "-u", f"{os.getuid()}", "--network", "none",
                             "--mount", f"type=bind,src={data},dst=/t,readonly", "--entrypoint", "amtool", image,
                             "template", "render", "--template.glob=/t/slack.tmpl", f"--template.data=/t/{name}.json",
                             '--template.text={{ template "slack.title" . }} || {{ template "slack.text" . }}',
                             check=False)  # fmt: skip
                self.assertEqual(out.stdout.strip(), expected, out.stderr)

    def test_grafana_provisions_every_dashboard(self):
        image = default("grafana", "grafana_container")
        etc = self.tmp / "grafana-etc"
        shutil.copytree(self.rendered / "grafana", etc)
        expected = set()
        # Each role's dashboards in its own directory under grafana_dashboards_dir, which
        # lies in the configuration the container mounts at /etc/grafana.
        dashboards = etc / default("prometheus_paths", "grafana_dashboards_dir").split("}}/", 1)[-1]
        for folder, names in default("prometheus_paths", "grafana_dashboard_sets").items():
            (dashboards / folder).mkdir(parents=True)
            for path in (ROOT / "src/dashboards" / name for name in names):
                shutil.copy(path, dashboards / folder)
                expected.add(json.loads(path.read_text())["uid"])
        port = default("grafana", "grafana_port")
        with Container(
            image,
            port,
            mounts=[
                f"type=bind,src={etc},dst=/etc/grafana,readonly",
                # Its database in memory, as on a host's local disk: on the shared home
                # the first start's migrations (small synced writes) took 35 s alone and
                # past two minutes beside a full check, at any CPU count; 6 s here at 1.
                "type=tmpfs,dst=/var/lib/grafana,tmpfs-mode=1777",
            ],
            limits=limits("grafana"),
        ) as grafana:
            wait_http(grafana.url + "/api/health", timeout=60)  # 6 s at its CPU limit
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
        image = default("nvidia-dcgm-exporter", "dcgm_exporter_container")
        counters = (
            ROOT
            / "roles/nvidia-dcgm-exporter/files"
            / default("nvidia-dcgm-exporter", "dcgm_exporter_counters_file")
        )
        copy = self.tmp / "dcgm-counters.csv"
        shutil.copy(counters, copy)
        copy.chmod(0o644)
        # The unit's own mount and arguments (tasks/present.yml, Run dcgm-exporter), then the check.
        check = docker("run", "--rm", *limits("nvidia-dcgm-exporter"), "--mount", f"type=bind,src={copy},dst=/etc/dcgm-exporter/custom-counters.csv,readonly", image,
                       "--collectors", "/etc/dcgm-exporter/custom-counters.csv",
                       "--address", f":{default('prometheus_paths', 'dcgm_exporter_port')}",
                       "config", "validate", check=False)  # fmt: skip
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("0 errors, 0 warnings", check.stdout + check.stderr)


if __name__ == "__main__":
    unittest.main()
