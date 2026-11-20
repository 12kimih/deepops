"""roles/docker_daemon_json/files/flag-conflicts.py: options dockerd would get twice."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "roles/docker_daemon_json/files/flag-conflicts.py"

# A kubespray-era unit and its drop-in, as found on a controller.
UNIT = """[Service]
Type=notify
Environment="DOCKER_STORAGE_OPTIONS=-s overlay2"
ExecStart=/usr/bin/dockerd $DOCKER_OPTS $DOCKER_STORAGE_OPTIONS $DOCKER_DNS_OPTIONS
"""
DROPIN = """[Service]
Environment="DOCKER_OPTS= --iptables=True --exec-opt native.cgroupdriver=systemd \\
--insecure-registry=reg:5000 --registry-mirror=http://reg:5000 \\
--data-root=/var/lib/docker --log-opt max-size=50m --log-opt max-file=5"
"""
PACKAGE = """[Service]
ExecStart=/usr/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock
"""


def check(units, daemon, keys=()):
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, text in enumerate(units):
            p = Path(tmp) / f"u{i}"
            p.write_text(text)
            paths.append(str(p))
        dj = Path(tmp) / "daemon.json"
        dj.write_text(json.dumps(daemon))
        out = subprocess.run([sys.executable, "-I", str(SCRIPT)], input=json.dumps(
            {"daemon_json": str(dj), "keys": list(keys), "units": paths}), capture_output=True, text=True, check=True)  # fmt: skip
        got = json.loads(out.stdout)
        return got["conflicts"], [Path(f).name for f in got["files"]]


class FlagConflicts(unittest.TestCase):
    def test_the_kubespray_drop_in_against_the_keys_about_to_be_written(self):
        conflicts, files = check([UNIT, DROPIN], {"live-restore": True},
                                 ["insecure-registries", "registry-mirrors", "log-opts", "storage-driver"])  # fmt: skip
        self.assertEqual(conflicts, ["insecure-registries", "log-opts", "registry-mirrors", "storage-driver"])
        self.assertEqual(files, ["u0", "u1"])

    def test_the_packages_unit_conflicts_with_nothing_we_set(self):
        self.assertEqual(check([PACKAGE], {"log-driver": "local"}, ["insecure-registries"])[0], [])

    def test_hosts_in_daemon_json_against_fd(self):
        self.assertEqual(check([PACKAGE], {"hosts": ["unix:///run/docker.sock"]})[0], ["hosts"])

    def test_a_later_execstart_wins(self):
        self.assertEqual(check([UNIT, DROPIN, PACKAGE], {}, ["log-opts"])[0], [])


if __name__ == "__main__":
    unittest.main()
