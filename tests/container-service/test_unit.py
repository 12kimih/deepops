"""roles/container_service: a container's unit goes and comes back with Docker.

PartOf=docker.service stops it when docker.service stops, and Restart=always does not
undo a stop; starting docker.service started nothing back, so a Docker stop and start
(by hand, or the docker role's socket repair) left every container down (2026-11-22).
WantedBy=docker.service starts it with Docker; systemd_unit enables an enabled unit
again, so a unit that gains it gets the link."""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import render, role_defaults  # noqa: E402
from repo_scan import tracked  # noqa: E402

TEMPLATE = (ROOT / "roles/container_service/templates/container.service.j2").read_text()


def install_section():
    return TEMPLATE.split("[Install]", 1)[1]


class Unit(unittest.TestCase):
    def test_docker_starting_starts_what_its_stop_stopped(self):
        self.assertIn("PartOf=docker.service", TEMPLATE)
        wanted_by = [
            line.split("=", 1)[1].split()
            for line in install_section().splitlines()
            if line.startswith("WantedBy=")
        ]
        self.assertEqual(wanted_by, [["multi-user.target", "docker.service"]])

    def test_nft_tables_come_back_with_nftables_too(self):
        nft = (ROOT / "roles/nft_table/templates/nft-table.service.j2").read_text()
        self.assertIn("PartOf=nftables.service", nft)
        self.assertIn("WantedBy=sysinit.target nftables.service", nft)

    def test_a_dead_api_fails_the_unit_visibly(self):
        self.assertIn("ExecStartPre=/usr/bin/docker info --format ok", TEMPLATE)
        self.assertIn("StartLimitBurst=5", TEMPLATE.split("[Service]")[0])
        self.assertIn("StartLimitIntervalSec=300", TEMPLATE.split("[Service]")[0])

    def unit(self, **service):
        keys = yaml.safe_load((ROOT / "roles/container_service/defaults/main.yml").read_text())[
            "container_service_keys"
        ]
        values = {
            "_container_service": {**keys, "unit": "docker.x.service", "image": "x@sha256:1", **service}
        }
        return render(TEMPLATE, {**role_defaults("deepops_paths"), **values, "ansible_managed": "managed"})

    def test_a_unit_it_needs_at_boot_orders_it(self):
        """dcgm-exporter's --device nvidia.com/gpu=all reads the CDI specification that
        nvidia-cdi-refresh writes at boot: started first, it could find none."""
        after = [
            line
            for line in self.unit(after=["nvidia-cdi-refresh.service"]).splitlines()
            if line.startswith("After=")
        ]
        self.assertEqual(after, ["After=docker.service network-online.target nvidia-cdi-refresh.service"])
        plain = self.unit()
        self.assertIn("After=docker.service network-online.target\nRequires=docker.service\n", plain)
        dcgm = (ROOT / "roles/nvidia-dcgm-exporter/tasks/present.yml").read_text()
        self.assertIn("after: [nvidia-cdi-refresh.service]", dcgm)

    def test_limits_render_when_set(self):
        limited = self.unit(cpus="0.5", memory="512m")
        self.assertIn("  --cpus=0.5 \\\n", limited)
        # swap included: --memory alone lets the container swap as much again
        self.assertIn("  --memory=512m --memory-swap=512m \\\n", limited)
        self.assertNotIn("--cpus", self.unit())
        self.assertNotIn("--memory", self.unit())

    def test_every_service_has_its_limits(self):
        """Each container service caps its CPUs and memory from its role's own defaults
        (<prefix>_max_cpu, <prefix>_max_memory), which a site can raise or clear: none
        runs unbounded beside the jobs and the logins, or hides a limit in options."""
        found = {}
        for path in tracked("roles/*/tasks/*.yml"):
            for service in services(yaml.safe_load(path.read_text()) or []):
                role = path.parts[-3]
                found[role] = service
                options = str(service.get("options", ""))  # a list, or a Jinja expression
                self.assertNotRegex(options, "--(cpus|memory)", f"{role}: limits in options")
                defaults = role_defaults(role)
                for key, suffix in (("cpus", "_max_cpu"), ("memory", "_max_memory")):
                    names = [n for n in defaults if n.endswith(suffix)]
                    self.assertEqual(len(names), 1, f"{role}: one {suffix} default")
                    self.assertEqual(service.get(key), "{{ %s }}" % names[0], f"{role}: {key}")
        self.assertGreaterEqual(len(found), 9, sorted(found))


def services(tasks):
    """The container_service requests in a task list (blocks included)."""
    for task in tasks:
        if not isinstance(task, dict):
            continue
        yield from services(task.get("block") or [])
        include = task.get("ansible.builtin.include_role") or {}
        if include.get("name") == "container_service" and "tasks_from" not in include:
            yield task["vars"]["container_service"]


if __name__ == "__main__":
    unittest.main()
