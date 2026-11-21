"""roles/container_service: a container's unit goes and comes back with Docker.

PartOf=docker.service stops it when docker.service stops, and Restart=always does not
undo a stop; starting docker.service started nothing back, so a Docker stop and start
(by hand, or the docker role's socket repair) left every container down (2026-11-22).
WantedBy=docker.service starts it with Docker; systemd_unit enables an enabled unit
again, so a unit that gains it gets the link."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

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


if __name__ == "__main__":
    unittest.main()
