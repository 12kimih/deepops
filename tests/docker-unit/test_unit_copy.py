"""roles/docker and docker.service's unit files: a copy in /etc the site has not retired
is refused (it shadows the package's unit, so no package update reaches it: a
kubespray-era copy kept a 2026-06 unit under Docker 29), and a restart that left the API
socket file gone is repaired (the retired unit's dockerd removed it on stopping)."""

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


REPAIR = next(t for t in TASKS if same_name(t.get("name"), "Recreate the Docker API socket"))


def repairs(exists, state="started", containerized=False, preview_installs=False):
    return condition(
        REPAIR["when"],
        {
            "docker_api_probe": {"rc": 0 if exists else 1},
            "docker_service_state": state,
            "deepops_containerized": containerized,
            "docker_preview_installs": preview_installs,
        },
    )


class ApiSocket(unittest.TestCase):
    def test_a_missing_socket_file_is_recreated_in_order(self):
        self.assertTrue(repairs(False))
        steps = [
            (next(k for k in t if k != "name"), t[next(k for k in t if k != "name")]) for t in REPAIR["block"]
        ]
        services = [(m, a) for m, a in steps if m.endswith("systemd_unit")]
        self.assertEqual(
            [(args["name"], args["state"]) for _, args in services],
            [("docker.service", "stopped"), ("docker.socket", "restarted"), ("docker.service", "started")],
        )

    def test_a_socket_still_missing_after_the_repair_stops_the_run(self):
        last = REPAIR["block"][-1]
        self.assertEqual(last["assert"]["that"], "docker_api_again.rc == 0")

    def test_nothing_to_do_with_the_socket_there_or_docker_meant_off(self):
        self.assertFalse(repairs(True))
        self.assertFalse(repairs(False, state="stopped"))
        self.assertFalse(repairs(False, containerized=True))
        self.assertFalse(repairs(False, preview_installs=True), "a preview that only installs Docker")


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
