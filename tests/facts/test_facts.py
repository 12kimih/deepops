"""The custom facts answer JSON on any host, from sysfs and /proc, in any locale.

gpus.fact printed a sentence when lspci was missing; topology.fact parsed lscpu's
labels, assumed PCI domain 0000 and printed quoted numbers; memory.fact parsed free's
localized "Mem:" label. Each now reads the kernel's files.
"""

from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest

FACTS = Path(__file__).resolve().parents[2] / "roles" / "facts" / "files"


def fact(name, **env):
    proc = subprocess.run(
        ["bash", str(FACTS / f"{name}.fact")],
        env={**os.environ, "LANG": "de_DE.UTF-8", "LC_ALL": "de_DE.UTF-8", **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def write(root, files):
    for rel, text in files.items():
        path = Path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")


class Gpus(unittest.TestCase):
    def test_nvidia_display_controllers_on_any_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(
                tmp,
                {
                    # A 3D controller on domain 0001, a VGA one, an NVIDIA bridge, another vendor's VGA.
                    "0001:3b:00.0/vendor": "0x10de",
                    "0001:3b:00.0/class": "0x030200",
                    "0001:3b:00.0/device": "0x2330",
                    "0000:01:00.0/vendor": "0x10de",
                    "0000:01:00.0/class": "0x030000",
                    "0000:01:00.0/device": "0x1db4",
                    "0000:02:00.0/vendor": "0x10de",
                    "0000:02:00.0/class": "0x068000",
                    "0000:02:00.0/device": "0x22a3",
                    "0000:03:00.0/vendor": "0x1a03",
                    "0000:03:00.0/class": "0x030000",
                    "0000:03:00.0/device": "0x2000",
                },
            )
            self.assertEqual(fact("gpus", SYSFS_PCI=tmp), {"count": 2, "device_ids": ["0x1db4", "0x2330"]})

    def test_no_pci_devices_is_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(fact("gpus", SYSFS_PCI=tmp), {"count": 0, "device_ids": []})


class Topology(unittest.TestCase):
    def cpus(self, tmp, layout, offline=()):
        """LAYOUT: one (package, siblings) per CPU, in CPU order."""
        files = {}
        for n, (package, siblings) in enumerate(layout):
            files[f"cpu{n}/topology/physical_package_id"] = str(package)
            files[f"cpu{n}/topology/core_cpus_list"] = siblings
            if n:
                files[f"cpu{n}/online"] = "0" if n in offline else "1"
        write(tmp, files)

    def test_sockets_cores_and_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Two sockets of two hyperthreaded cores; core IDs repeat across sockets.
            self.cpus(
                tmp,
                [
                    (0, "0,4"),
                    (0, "1,5"),
                    (1, "2,6"),
                    (1, "3,7"),
                    (0, "0,4"),
                    (0, "1,5"),
                    (1, "2,6"),
                    (1, "3,7"),
                ],
            )
            self.assertEqual(
                fact("topology", SYSFS_CPU=tmp),
                {
                    "cpu_topology": {
                        "sockets": 2,
                        "cores_per_socket": 2,
                        "threads_per_core": 2,
                        "physical_cores": 4,
                        "logical_cpus": 8,
                    }
                },
            )

    def test_offline_cpus_are_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.cpus(tmp, [(0, "0"), (0, "1"), (0, "2")], offline=(2,))
            self.assertEqual(fact("topology", SYSFS_CPU=tmp)["cpu_topology"]["logical_cpus"], 2)

    def test_this_host_matches_its_cpu_count(self):
        topology = fact("topology")["cpu_topology"]
        # The online CPUs as the kernel counts them: os.cpu_count() follows PYTHON_CPU_COUNT,
        # which scripts/check.sh sets for the tools it runs.
        self.assertEqual(topology["logical_cpus"], os.sysconf("SC_NPROCESSORS_ONLN"))


class Memory(unittest.TestCase):
    def test_total_from_meminfo(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, {"meminfo": "MemTotal:       263812956 kB\nMemFree:        27000000 kB"})
            self.assertEqual(fact("memory", MEMINFO=f"{tmp}/meminfo"), {"total_mb": 244748})


if __name__ == "__main__":
    unittest.main()
