"""An exclusive job's GPUs go to max and back to default, every setting each GPU has.

Two helpers did this with one copy of the same query each. The clock one ran under
set -e, so a GPU without application clocks (nvidia-smi status 3) stopped the reset
before the rest of it, and N/A clocks failed the whole helper. The epilog slept 5 s
after every exclusive job, residual processes or not.
"""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import render  # noqa: E402

TEMPLATES = ROOT / "roles" / "slurm" / "templates" / "etc" / "slurm"

# nvidia-smi over the environment: QUERY and LIST answer --query-gpu and -L,
# APPS answers --query-compute-apps, RC_<setting> (pl, ac, rac) is a setting's status;
# each call is logged to $CALLS.
NVIDIA_SMI = """#!/bin/bash
echo "$*" >> "$CALLS"
case "$1" in
--query-gpu=*) printf '%b' "$QUERY"; exit "${QUERY_RC:-0}" ;;
--query-compute-apps=*) printf '%b' "${APPS:-}"; exit 0 ;;
-L) printf '%b' "$LIST"; exit 0 ;;
-i) var="RC_${3#-}"; exit "${!var:-0}" ;;
esac
exit 2
"""

LIST = "GPU 0: A (UUID: a)\\nGPU 1: A (UUID: b)\\n"


class Helper(unittest.TestCase):
    def run_levels(self, level, query, cap=None, **env):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "bin").mkdir()
            (tmp / "bin" / "nvidia-smi").write_text(NVIDIA_SMI)
            (tmp / "bin" / "nvidia-smi").chmod(0o755)
            conf = tmp / "power.conf"
            if cap is not None:
                conf.write_text(f"NVIDIA_POWER_LIMIT_WATTS={cap}\n")
            helper = tmp / "set_gpu_levels.sh"
            helper.write_text(
                render(
                    (TEMPLATES / "shared" / "bin" / "set_gpu_levels.sh").read_text(),
                    {"nvidia_power_limit_conf_file": str(conf)},
                )
            )
            helper.chmod(0o755)
            calls = tmp / "calls"
            calls.touch()
            proc = subprocess.run(
                [str(helper), level],
                env={
                    **os.environ,
                    "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
                    "CALLS": str(calls),
                    "QUERY": query,
                    "LIST": LIST,
                    **env,
                },
                capture_output=True,
                text=True,
                check=False,
            )
            settings = sorted(c for c in calls.read_text().splitlines() if c.startswith("-i "))
        return proc.returncode, settings

    def test_max_sets_power_and_clocks_on_every_gpu(self):
        rc, settings = self.run_levels("max", "0, 400.00, 1593, 1410\\n1, 400.00, 1593, 1410\\n")
        self.assertEqual(rc, 0)
        self.assertEqual(
            settings,
            ["-i 0 -ac 1593,1410", "-i 0 -pl 400.00", "-i 1 -ac 1593,1410", "-i 1 -pl 400.00"],
        )

    def test_the_cap_is_the_ceiling(self):
        rc, settings = self.run_levels("max", "0, 400.00, 1593, 1410\\n1, 400.00, 1593, 1410\\n", cap=300)
        self.assertEqual(rc, 0)
        self.assertIn("-i 0 -pl 300", settings)
        rc, settings = self.run_levels("default", "0, 250.00\\n1, 250.00\\n", cap=300)
        self.assertIn("-i 1 -pl 250.00", settings)

    def test_a_setting_a_gpu_lacks_is_left_alone(self):
        # Application clocks N/A in the query: never set. Status 3: not supported, no failure.
        rc, settings = self.run_levels("max", "0, 400.00, [N/A], [N/A]\\n1, [N/A], 1593, 1410\\n", RC_ac="3")
        self.assertEqual(rc, 0)
        self.assertEqual(settings, ["-i 0 -pl 400.00", "-i 1 -ac 1593,1410"])

    def test_default_resets_every_gpu_even_when_one_setting_fails(self):
        rc, settings = self.run_levels("default", "0, 300.00\\n1, 300.00\\n", RC_rac="3")
        self.assertEqual(rc, 0)
        self.assertEqual(settings, ["-i 0 -pl 300.00", "-i 0 -rac", "-i 1 -pl 300.00", "-i 1 -rac"])
        rc, settings = self.run_levels("default", "0, 300.00\\n1, 300.00\\n", RC_pl="1")
        self.assertEqual(rc, 1)
        self.assertEqual(settings, ["-i 0 -pl 300.00", "-i 0 -rac", "-i 1 -pl 300.00", "-i 1 -rac"])

    def test_nothing_is_set_without_a_row_for_every_gpu(self):
        self.assertEqual(self.run_levels("max", "0, 400.00, 1593, 1410\\n"), (1, []))
        self.assertEqual(self.run_levels("max", "", QUERY_RC="15"), (1, []))
        self.assertEqual(self.run_levels("max", "0, 400.00, 1593, 1410\\nGPU1, 1, 1, 1\\n"), (1, []))

    def test_a_level_is_required(self):
        self.assertEqual(self.run_levels("", "")[0], 2)


class Epilog(unittest.TestCase):
    def run_epilog(self, apps):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for sub in ("bin", "shared/bin", "slurm/bin"):
                (tmp / sub).mkdir(parents=True)
            (tmp / "bin" / "nvidia-smi").write_text(NVIDIA_SMI)
            for name, body in (
                ("logger", "exit 0"),
                ("shared/bin/set_gpu_levels.sh", 'echo "levels $*" >> "$CALLS"'),
            ):
                (tmp / "bin" / name if "/" not in name else tmp / name).write_text(f"#!/bin/bash\n{body}\n")
            (tmp / "slurm" / "bin" / "scontrol").write_text('#!/bin/bash\necho "scontrol $*" >> "$CALLS"\n')
            for path in (tmp / "bin").iterdir():
                path.chmod(0o755)
            (tmp / "shared/bin/set_gpu_levels.sh").chmod(0o755)
            (tmp / "slurm/bin/scontrol").chmod(0o755)
            epilog = tmp / "epilog"
            epilog.write_text(
                render(
                    (TEMPLATES / "epilog.d" / "50-exclusive-gpu").read_text(),
                    {"slurm_config_dir": str(tmp), "slurm_install_prefix": str(tmp / "slurm")},
                )
            )
            epilog.chmod(0o755)
            calls = tmp / "calls"
            calls.touch()
            start = time.monotonic()
            proc = subprocess.run(
                [str(epilog)],
                env={
                    **os.environ,
                    "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
                    "CALLS": str(calls),
                    "APPS": apps,
                    "SLURMD_NODENAME": "node1",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            elapsed = time.monotonic() - start
            lines = calls.read_text().splitlines()
        return proc.returncode, lines, elapsed

    def test_a_clean_job_does_not_wait(self):
        rc, calls, elapsed = self.run_epilog("")
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["--query-compute-apps=pid --format=csv,noheader", "levels default"])
        self.assertLess(elapsed, 3)

    def test_a_process_that_survives_drains(self):
        # The stub keeps reporting the process: it survived the kill.
        rc, calls, _ = self.run_epilog("999999999\\n")
        self.assertEqual(rc, 0)
        self.assertEqual(calls.count("--query-compute-apps=pid --format=csv,noheader"), 2)
        self.assertIn(
            "scontrol update nodename=node1 state=drain reason=Residual GPU processes found",
            calls,
        )
        self.assertEqual(calls[-1], "levels default")


if __name__ == "__main__":
    unittest.main()
