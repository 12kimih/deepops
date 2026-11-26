"""os-updates-metrics keeps its "pending since" clock across a run that measured nothing.

The alert counts days since security updates were first seen pending. A run where apt
could not simulate the upgrade knows nothing about them; resetting the clock there
restarted the count after every transient apt failure.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from sandbox_path import sandbox_path  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[2] / "roles/os_updates/files/os-updates-metrics"
# What the script runs besides the package tools each test fakes (dpkg, dpkg-query,
# apt-get, apt): never the host's, whose packages and pending reboot are not the test's.
TOOLS = ("sh", "awk", "cat", "chmod", "date", "grep", "mkdir", "mktemp", "mv", "python3", "rm", "sed")
STUBS = {"dpkg": "exit 0\n", "dpkg-query": "exit 0\n", "apt-get": "exit 0\n", "apt": "exit 0\n"}


def environment(bin_, state, run=None, flag=None, **stubs):
    """The script's environment: the fake package tools (STUBS, overridden by STUBS) in
    BIN_ and TOOLS only on PATH, and every host path it reads or writes in the test's
    directories (a reboot flag that does not exist unless FLAG is given)."""
    bin_.mkdir(exist_ok=True)
    for name, body in {**STUBS, **stubs}.items():
        (bin_ / name).write_text("#!/bin/sh\n" + body)
        (bin_ / name).chmod(0o755)
    return {
        **os.environ,
        "PATH": sandbox_path(bin_, TOOLS),
        "OS_UPDATES_STATE": str(state),
        "OS_UPDATES_RUN": str(run or state),
        "OS_UPDATES_REBOOT_FLAG": str(flag or Path(state, "no-reboot-required")),
    }


class PendingSinceTests(unittest.TestCase):
    def run_with(self, apt_script):
        env = environment(self.bin, self.state, **{"apt-get": apt_script})
        subprocess.run(["sh", str(SCRIPT), str(self.textfile)], env=env, check=True, capture_output=True)
        since = self.state / "security-pending-since"
        return since.read_text().strip() if since.exists() else None

    def test_the_clock_survives_an_apt_failure_and_ends_when_nothing_is_pending(self):
        with tempfile.TemporaryDirectory() as d:
            self.bin, self.state, self.textfile = Path(d, "bin"), Path(d, "state"), Path(d, "textfile")
            first = self.run_with("echo 'Inst libfoo (1 Ubuntu:24.04/noble-security [amd64])'\n")
            self.assertIsNotNone(first)
            self.assertEqual(self.run_with("exit 100\n"), first, "an apt failure keeps the clock")
            self.assertEqual(
                self.run_with("echo 'Inst libfoo (1 Ubuntu:24.04/noble-security [amd64])'\n"), first
            )
            self.assertIsNone(self.run_with("exit 0\n"), "nothing pending ends it")


class PackageInfoTests(unittest.TestCase):
    def test_every_installed_package_is_one_series_and_removed_ones_are_not(self):
        with tempfile.TemporaryDirectory() as d:
            bin_, state, textfile = Path(d, "bin"), Path(d, "state"), Path(d, "textfile")
            query = "printf 'ii |libc6:amd64|2.39-0ubuntu8.6\\nrc |oldpkg|1.0\\nii |linux-image-generic|6.8.0-149.149\\n'\n"
            env = environment(bin_, state, **{"dpkg-query": query})
            subprocess.run(["sh", str(SCRIPT), str(textfile)], env=env, check=True, capture_output=True)
            lines = [
                line
                for line in (textfile / "os-updates.prom").read_text().splitlines()
                if line.startswith("os_updates_package_info")
            ]
            self.assertEqual(
                lines,
                [
                    'os_updates_package_info{package="libc6:amd64",version="2.39-0ubuntu8.6",source="repo"} 1',
                    'os_updates_package_info{package="linux-image-generic",version="6.8.0-149.149",source="repo"} 1',
                ],
            )

    def test_a_package_no_source_offers_is_local(self):
        # A .deb installed from a URL (enroot, a vendor tool): apt-upgrade cannot level it,
        # only the role that installs it; apt list marks it [installed,local].
        with tempfile.TemporaryDirectory() as d:
            bin_, state, textfile = Path(d, "bin"), Path(d, "state"), Path(d, "textfile")
            query = "printf 'ii |enroot|4.2.0-1\\nii |libc6:amd64|2.39-0ubuntu8.6\\n'\n"
            listed = "printf 'Listing...\\nenroot/now 4.2.0-1 amd64 [installed,local]\\nlibc6/noble,now 2.39 amd64 [installed]\\n'\n"
            env = environment(bin_, state, **{"dpkg-query": query, "apt": listed})
            subprocess.run(["sh", str(SCRIPT), str(textfile)], env=env, check=True, capture_output=True)
            text = (textfile / "os-updates.prom").read_text()
            self.assertIn(
                'os_updates_package_info{package="enroot",version="4.2.0-1",source="local"} 1', text
            )
            self.assertIn('package="libc6:amd64",version="2.39-0ubuntu8.6",source="repo"', text)


class ComponentTests(unittest.TestCase):
    """The host's component record as series, so an alert names a package's component."""

    RECORD = {
        "components": {
            "enroot": {"state": "present", "version": "4.2.1-1"},
            "half": {"state": "installing", "version": "2"},
            'odd"name': {"state": "present", "version": ""},
        },
        "items": {
            "package:enroot": ["enroot"],
            "package:enroot+caps": ["enroot", "pyxis"],
            "unit:x": ["enroot"],
        },
    }

    def series(self, record_text=None, named=True, *args, **stubs):
        with tempfile.TemporaryDirectory() as d:
            bin_, state, textfile = Path(d, "bin"), Path(d, "state"), Path(d, "textfile")
            env = environment(bin_, state, **stubs)
            record = Path(d, "deepops_components.fact")
            if record_text is not None:
                record.write_text(record_text)
            if named:
                env["OS_UPDATES_COMPONENT_RECORD"] = str(record)
            subprocess.run(
                ["sh", str(SCRIPT), *args, str(textfile)], env=env, check=True, capture_output=True
            )
            packages = textfile / "os-updates.prom"
            if packages.exists():
                self.assertNotIn(
                    "component", packages.read_text(), "the record's series have a file of their own"
                )
            text = (textfile / "os-updates-components.prom").read_text()
            return [
                line for line in text.splitlines() if "_component_info{" in line or "_source_info{" in line
            ]

    def test_present_components_and_each_packages_components(self):
        self.assertEqual(
            self.series(json.dumps(self.RECORD)),
            [
                'os_updates_component_info{component="enroot",version="4.2.1-1"} 1',
                'os_updates_component_info{component="odd\\"name",version=""} 1',
                'os_updates_package_component_info{package="enroot",component="enroot"} 1',
                'os_updates_package_component_info{package="enroot+caps",component="enroot"} 1',
                'os_updates_package_component_info{package="enroot+caps",component="pyxis"} 1',
            ],
        )

    def test_recorded_sources_follow_the_versions_one_group_each(self):
        record = json.loads(json.dumps(self.RECORD))
        record["components"]["enroot"]["source"] = "aaaa"
        record["components"]["half"]["source"] = "bbbb"  # installing: not current
        record["record_source"] = "rrrr"
        self.assertEqual(
            self.series(json.dumps(record))[2:4],
            [
                'os_updates_component_source_info{component="component_record",source="rrrr"} 1',
                'os_updates_component_source_info{component="enroot",source="aaaa"} 1',
            ],
        )

    def test_the_record_alone_is_published_at_once_without_measuring(self):
        # what component_record runs after each write: no package tool is asked
        never = "echo asked >&2; exit 1\n"
        stubs = {"apt-get": never, "apt": never, "dpkg": never, "dpkg-query": never}
        self.assertEqual(
            self.series(json.dumps(self.RECORD), True, "--components", **stubs),
            self.series(json.dumps(self.RECORD)),
        )

    def test_no_record_or_a_broken_one_is_no_series_and_no_failure(self):
        self.assertEqual(self.series(None), [])
        self.assertEqual(self.series("{not json"), [])
        self.assertEqual(self.series(json.dumps(self.RECORD), named=False), [])


class BootRepublishTests(unittest.TestCase):
    def test_the_boot_republish_does_not_report_the_reboot_it_followed_as_pending(self):
        # Measured with a reboot pending, then republished after the reboot (the flag on
        # /run is gone): the kept copy said 1, which kept OsRebootPending firing.
        with tempfile.TemporaryDirectory() as d:
            bin_, state, run, textfile = (Path(d, n) for n in ("bin", "state", "run", "textfile"))
            run.mkdir()
            flag = run / "reboot-required"
            env = environment(bin_, state, run=run, flag=flag)

            def published(*args):
                subprocess.run(
                    ["sh", str(SCRIPT), *args, str(textfile)], env=env, check=True, capture_output=True
                )
                return dict(
                    line.split(" ", 1)
                    for line in (textfile / "os-updates.prom").read_text().splitlines()
                    if line.startswith("os_updates_reboot_required")
                )

            flag.touch()
            before = published()
            self.assertEqual(before["os_updates_reboot_required"], "1")
            flag.unlink()
            (run / "os-updates-reboot-required-since").unlink()  # /run is empty after a reboot
            (textfile / "os-updates-components.prom").unlink()  # the textfile tmpfs is empty too
            after = published("--publish")
            self.assertEqual(
                after, {"os_updates_reboot_required": "0", "os_updates_reboot_required_since_seconds": "0"}
            )
            self.assertTrue((textfile / "os-updates-components.prom").exists(), "both files go back")


if __name__ == "__main__":
    unittest.main()
