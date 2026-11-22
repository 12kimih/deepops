"""roles/nfs_client/files/nfs-client-retry: the retry of the NFS mounts boot left behind.

A whole cluster powering on together leaves some client without a share (its server was
not serving yet), and boot tries an fstab mount once. An automount on /home took every
node's boot apart instead (docs/slurm-cluster/slurm-nfs.md), so the retry is this script,
run by a timer and by playbooks/utilities/nfs-mount.yml: it mounts only what is missing,
only from a server that serves, and every step is bounded.
"""

import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "roles/nfs_client/files/nfs-client-retry"
loader = importlib.machinery.SourceFileLoader("nfs_client_retry", str(SCRIPT))
spec = importlib.util.spec_from_loader("nfs_client_retry", loader)
nmm = importlib.util.module_from_spec(spec)
loader.exec_module(nmm)

FSTAB = {
    "filesystems": [
        {"target": "/home", "source": "master:/home", "options": "rw,hard,_netdev,nofail"},
        {"target": "/scratch", "source": "gpu8:/scratch", "options": "rw,hard,_netdev,nofail"},
        {"target": "/sw", "source": "master:/sw", "options": "rw,hard,_netdev,nofail"},
        {"target": "/archive", "source": "filer:/a", "options": "rw,noauto"},
        {"target": "/data", "source": "[fd00::1]:/data", "options": "rw,x-systemd.automount"},
    ]
}


class Host:
    """findmnt and systemctl over a set of mounted targets; servers that serve."""

    def __init__(self, mounted, serving, start_status=0):
        self.mounted, self.serving, self.start_status = set(mounted), set(serving), start_status
        self.started, self.probed = [], []

    def run(self, argv, timeout):
        assert timeout and timeout > 0, argv
        if argv[:2] == ["findmnt", "--fstab"]:
            return 0, json.dumps(FSTAB)
        if argv[:2] == ["findmnt", "--kernel"]:
            return 0, "/\n" + "".join(f"{t}\n" for t in sorted(self.mounted))
        if argv[0] == "systemd-escape":
            return 0, argv[-1].strip("/").replace("/", "-") + ".mount\n"
        if argv[:2] == ["systemctl", "start"]:
            target = "/" + argv[2].removesuffix(".mount").replace("-", "/")
            self.started.append(target)
            if self.start_status == 0:
                self.mounted.add(target)
            return self.start_status, ""
        raise AssertionError(argv)

    def serves(self, host, timeout=nmm.PROBE_TIMEOUT):
        self.probed.append(host)
        return host in self.serving

    def main(self, *args):
        with mock.patch.object(nmm, "run", self.run), mock.patch.object(nmm, "serves", self.serves):
            with mock.patch("builtins.print") as out:
                status = nmm.main(list(args))
        return status, [c.args[0] for c in out.call_args_list]


class MountMissing(unittest.TestCase):
    def test_only_missing_entries_of_a_serving_server_are_started(self):
        host = Host(mounted={"/home"}, serving={"master"})
        status, lines = host.main()
        self.assertEqual(host.started, ["/sw"])
        self.assertEqual(status, 1)  # /scratch is still missing
        self.assertIn("/scratch: gpu8 does not serve NFS (port 2049); not mounted", lines)
        self.assertIn("/sw: mounted", lines)

    def test_noauto_and_automount_entries_are_left_alone(self):
        host = Host(mounted={"/home", "/sw", "/scratch"}, serving={"master", "gpu8", "filer"})
        self.assertEqual(host.main(), (0, []))
        self.assertEqual(host.probed, [])

    def test_a_failed_start_is_reported_not_raised(self):
        host = Host(mounted=set(), serving={"master", "gpu8"}, start_status=124)
        status, lines = host.main()
        self.assertEqual(status, 1)
        self.assertIn("/home: mount failed (home.mount: systemctl status 124)", lines)

    def test_wait_polls_a_server_until_it_serves(self):
        host = Host(mounted={"/home", "/sw"}, serving=set())
        probes = iter([False, False, True])
        with mock.patch.object(nmm.time, "sleep") as sleep:
            host.serves = lambda h, timeout=5: next(probes)
            status, _ = host.main("--wait", "60")
        self.assertEqual((status, host.started), (0, ["/scratch"]))
        self.assertEqual(sleep.call_count, 2)

    def test_the_metric_names_every_entry(self):
        host = Host(mounted={"/home"}, serving=set())
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nfs-client.prom")
            host.main("--textfile", path)
            text = Path(path).read_text()
        self.assertIn('nfs_client_mount_missing{mountpoint="/home"} 0', text)
        self.assertIn('nfs_client_mount_missing{mountpoint="/scratch"} 1', text)
        self.assertIn('nfs_client_mount_missing{mountpoint="/sw"} 1', text)
        self.assertNotIn("/archive", text)
        self.assertNotIn("nfs_client_last_success_timestamp_seconds ", text, "still missing: no success")
        host = Host(mounted={"/home", "/sw", "/scratch"}, serving=set())
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nfs-client.prom")
            host.main("--textfile", path)
            self.assertIn("nfs_client_last_success_timestamp_seconds ", Path(path).read_text())

    def test_no_textfile_directory_is_no_failure(self):
        host = Host(mounted={"/home", "/sw", "/scratch"}, serving=set())
        self.assertEqual(host.main("--textfile", "/nonexistent/nfs-client.prom"), (0, []))

    def test_server_names(self):
        self.assertEqual(nmm.server("master:/home"), "master")
        self.assertEqual(nmm.server("[fd00::1]:/data"), "fd00::1")
        self.assertEqual(nmm.server("10.0.1.1:/a/b"), "10.0.1.1")

    def test_every_command_is_bounded(self):
        calls = []
        with mock.patch.object(
            nmm.subprocess,
            "run",
            side_effect=lambda *a, **k: calls.append(k) or mock.Mock(returncode=0, stdout=""),
        ):
            nmm.run(["true"], 7)
        self.assertEqual(calls[0]["timeout"], 7)


class Units(unittest.TestCase):
    def test_the_timer_runs_it_and_still_missing_is_no_unit_failure(self):
        service = (ROOT / "roles/nfs_client/templates/nfs-client-retry.service.j2").read_text()
        timer = (ROOT / "roles/nfs_client/templates/nfs-client-retry.timer.j2").read_text()
        self.assertIn("SuccessExitStatus=1", service)
        self.assertIn("TimeoutStartSec=", service)
        self.assertIn("OnUnitInactiveSec=1min", timer)
        defaults = (ROOT / "roles/nfs_client/defaults/main.yml").read_text()
        self.assertNotIn("x-systemd.automount", defaults.split("nfs_mount_options:")[1].splitlines()[0])

    def test_the_playbook_runs_the_same_script(self):
        mount = (ROOT / "roles/nfs_client/tasks/mount.yml").read_text()
        self.assertIn('cmd: "nfs-client-retry --wait', mount)


if __name__ == "__main__":
    unittest.main()
