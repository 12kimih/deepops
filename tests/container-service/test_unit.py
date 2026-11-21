"""roles/container_service: a container's unit goes and comes back with Docker.

PartOf=docker.service stops it when docker.service stops, and Restart=always does not
undo a stop; starting docker.service started nothing back, so a Docker stop and start
(by hand, or the docker role's socket repair) left every container down (2026-11-22).
WantedBy=docker.service starts it with Docker, and a unit that gains it is re-enabled,
since enabling an enabled unit adds no new link."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import load, same_name  # noqa: E402

TEMPLATE = (ROOT / "roles/container_service/templates/container.service.j2").read_text()
TASKS = load("roles/container_service/tasks/main.yml")


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

    def test_a_changed_unit_is_enabled_afresh(self):
        task = next(
            t
            for t in TASKS
            if same_name(t.get("name"), "Configure the service {{ _container_service.unit }}")
        )
        self.assertEqual(task["systemd_unit"]["relink"], "{{ container_service_unit_file is changed }}")
        install = next(
            t for t in TASKS if same_name(t.get("name"), "Install the unit {{ _container_service.unit }}")
        )
        self.assertEqual(install["register"], "container_service_unit_file")

    def test_nft_tables_come_back_with_nftables_too(self):
        nft = (ROOT / "roles/nft_table/templates/nft-table.service.j2").read_text()
        self.assertIn("PartOf=nftables.service", nft)
        self.assertIn("WantedBy=sysinit.target nftables.service", nft)


if __name__ == "__main__":
    unittest.main()
