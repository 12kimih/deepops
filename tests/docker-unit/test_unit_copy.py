"""roles/docker refuses a copy of docker.service in /etc that the site has not retired:
it shadows the package's unit, so no package update reaches it (a kubespray-era copy
kept a 2026-06 unit under Docker 29)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import condition, load, same_name  # noqa: E402

TASKS = load("roles/docker/tasks/present.yml")
REFUSE = next(
    t for t in TASKS if same_name(t.get("name"), "Refuse a copy of the Docker unit the site has not retired")
)


def passes(exists, retired):
    return condition(
        REFUSE["assert"]["that"],
        {"docker_unit_copy": {"stat": {"exists": exists}}, "docker_retired_unit_files": retired},
    )


class UnitCopy(unittest.TestCase):
    def test_no_copy_passes(self):
        self.assertTrue(passes(False, []))

    def test_a_copy_the_site_retires_passes(self):
        self.assertTrue(passes(True, ["/etc/systemd/system/docker.service"]))

    def test_a_copy_nobody_named_stops_the_run(self):
        self.assertFalse(passes(True, []))
        self.assertFalse(passes(True, ["/etc/systemd/system/docker.service.d/docker-options.conf"]))


if __name__ == "__main__":
    unittest.main()
