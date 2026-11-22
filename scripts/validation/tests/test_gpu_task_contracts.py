"""Tests for the GPU tasks that have to escape the Slurm login GPU cgroup.

The Slurm login setup puts a DeviceAllow guard on the ssh service, so anything
Ansible runs over its ssh connection sees no GPUs. Every privileged GPU command
therefore runs in a transient systemd unit. These tests pin that contract and
exercise the MIG capability probe against a mocked nvidia-smi, so the difference
between "this GPU does not support MIG" and "the query never ran" is covered.

Run with: python3 -m unittest discover scripts/validation/tests
"""

import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests" / "lib"))
import repo_scan  # noqa: E402
from ansible_yaml import canonical, same_name  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
TRANSIENT_SERVICE = "systemd-run --wait --pipe --collect --quiet --"
PROBE_NAME = "check for MIG capable devices"
MIG_PLAYBOOK = "playbooks/nvidia-software/nvidia-mig.yml"
MIG_ROLE = "roles/nvidia-mig-manager/tasks/main.yml"


def load(path):
    return canonical(yaml.safe_load((ROOT / path).read_text(encoding="utf-8")))


def gpu_commands(path):
    """The (task, normalized command) pairs of PATH's command and shell tasks (and their
    change_ forms) that drive the GPUs (finding nvidia-smi on PATH does not)."""
    pairs = []
    for task in repo_scan.tasks(ROOT / path):
        name, args = repo_scan.module(task)
        if name not in {"command", "shell"} | repo_scan.CHANGES:
            continue
        # The raw text of a free-form command: its words are shell, not key=value pairs.
        raw = next((v for k, v in task.items() if repo_scan.canonical(k) == name), None)
        command = raw if isinstance(raw, str) else args.get("cmd") or args.get("argv")
        # The same command in any form: a string, cmd:, or an argv list.
        if isinstance(command, list):
            command = " ".join(str(arg) for arg in command)
        if not isinstance(command, str):
            continue
        command = " ".join(command.split())
        if (
            re.search(r"(?<!which )(?<!command -v )\bnvidia-smi\b", command)
            or "nvidia-mig-parted apply" in command
            or "nvidia-mig-parted assert" in command
        ):
            pairs.append((task, command))
    return pairs


def all_tasks(path):
    """Every task in a playbook or task file, whatever module it uses."""
    found = []

    def visit(value):
        if isinstance(value, dict):
            # A task carries a name and a module; `include_role: {name: x}` does not.
            if isinstance(value.get("name"), str) and len(value) > 1:
                found.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(load(path))
    return found


def task_named(path, name):
    matches = [task for task in all_tasks(path) if same_name(task.get("name"), name)]
    assert len(matches) == 1, f"{path}: {name}"
    return matches[0]


def conditions(path, name):
    """Every `when` a task runs under: its own and those of the blocks around it."""
    found = []

    def walk(tasks, inherited):
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            own = task.get("when") or []
            here = inherited + (own if isinstance(own, list) else [own])
            if same_name(task.get("name"), name) and "block" not in task:
                found.append(set(here))
            for key in ("block", "rescue", "always"):
                walk(task.get(key), here)

    walk(yaml.safe_load((ROOT / path).read_text()), [])
    assert len(found) == 1, f"{path}: {name}"
    return found[0]


class TransientExecutionContract(unittest.TestCase):
    """Nothing that touches a GPU may run directly in the ssh context."""

    def test_every_gpu_command_runs_in_a_transient_unit(self):
        # Every task and playbook: a hand list missed what moved into a role.
        seen = 0
        for path in (p.relative_to(ROOT).as_posix() for p in repo_scan.ansible_files()):
            for task, command in gpu_commands(path):
                seen += 1
                with self.subTest(path=path, task=task.get("name")):
                    self.assertIn(TRANSIENT_SERVICE, command)
                    driven = "nvidia-smi" if "nvidia-smi" in command else "nvidia-mig-parted"
                    self.assertLess(command.index(TRANSIENT_SERVICE), command.index(driven))
        self.assertGreater(seen, 15, "the scan finds the GPU commands at all")

    def test_mig_administration_is_covered_too(self):
        # The probe is useless on its own: once it succeeds, apply and assert
        # drive the very same GPUs and need the same isolation.
        # assert runs twice: before apply (is the layout in place?) and after (did it take?).
        commands = [command for _, command in gpu_commands(MIG_PLAYBOOK)]
        for fragment, count in (
            ("nvidia-smi --query-gpu=mig.mode.current", 1),
            ("nvidia-mig-parted apply", 1),
            ("nvidia-mig-parted assert", 2),
        ):
            with self.subTest(fragment=fragment):
                matching = [c for c in commands if fragment in c]
                self.assertEqual(len(matching), count, fragment)
                for command in matching:
                    self.assertIn(TRANSIENT_SERVICE, command)

    def test_mig_operations_run_privileged(self):
        plays = yaml.safe_load((ROOT / MIG_PLAYBOOK).read_text(encoding="utf-8"))
        self.assertEqual(len(plays), 1)
        self.assertIs(plays[0]["become"], True)
        self.assertIs(task_named(MIG_ROLE, PROBE_NAME)["become"], True)

    def test_gpu_clock_arguments_avoid_shell_interpolation(self):
        path = "playbooks/utilities/nvidia-set-gpu-clocks.yml"
        for name in (
            "set the gpu clock to a specified amount",
            "reset the gpu clock to the default",
        ):
            with self.subTest(name=name):
                task = task_named(path, name)
                self.assertTrue({"command", "change_command"} & set(task))
                self.assertFalse({"shell", "change_shell"} & set(task))


class MigApplyFailureContract(unittest.TestCase):
    """A MIG layout that could not be applied must not pass as applied."""

    def test_apply_and_assert_surface_their_failures(self):
        for name in ("Apply MIG configuration", "Assert MIG configuration was applied"):
            with self.subTest(name=name):
                task = task_named(MIG_PLAYBOOK, name)
                self.assertNotIn("failed_when", task)
                self.assertNotIn("ignore_errors", task)

    def test_installation_requires_a_successful_capability_probe(self):
        # Without this the Red Hat branch installed the MIG manager on nodes
        # whose capability was never established.
        for name, family in (
            ("Install MIG Manager (apt)", "Debian"),
            ("Install MIG Manager (dnf)", "RedHat"),
        ):
            with self.subTest(name=name):
                self.assertLessEqual(
                    {
                        "has_mig.rc == 0",
                        "(mig_manager_installed.stdout | trim) != 'installed ' ~ mig_manager_version ~ '-' ~ mig_manager_release",
                        f'ansible_facts.os_family == "{family}"',
                    },
                    conditions(MIG_ROLE, name),
                )

    def test_a_failed_probe_is_reported_rather_than_skipped(self):
        # rc 2 is the probe's own "the query never ran"; the gpus fact (PCI bus) keeps
        # nodes without NVIDIA hardware out of it.
        for path in (MIG_PLAYBOOK, MIG_ROLE):
            with self.subTest(path=path):
                guard = task_named(path, "fail when the MIG capability probe could not run")
                self.assertEqual(
                    set(guard["when"]),
                    {
                        "has_mig.rc == 2",
                        "ansible_facts.ansible_local['gpus']['count'] | default(0) | int > 0",
                    },
                )


class MigProbeBehaviour(unittest.TestCase):
    """Run the probe as written, against a mocked nvidia-smi."""

    def probe(self, path):
        probe = task_named(path, PROBE_NAME)
        self.assertIs(probe["failed_when"], False)
        self.assertIs(probe["changed_when"], False)
        return probe["shell"]

    def run_probe(self, path, mode, ssh_device_guard=False):
        """Execute the probe's shell exactly as Ansible would."""
        script = self.probe(path)
        with tempfile.TemporaryDirectory() as tmp:
            mocks = Path(tmp)

            # systemd-run drops the ssh cgroup: it consumes its own options up to
            # the '--' separator and runs the rest outside the device guard.
            (mocks / "systemd-run").write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    while [ "$1" != "--" ]; do
                      case "$1" in
                        --wait|--pipe|--collect|--quiet) shift ;;
                        *) echo "systemd-run: unexpected argument $1" >&2; exit 1 ;;
                      esac
                    done
                    shift
                    SSH_DEVICE_GUARD=0 exec "$@"
                    """
                ),
                encoding="utf-8",
            )
            # nvidia-smi reports a missing device on stdout and a broken driver
            # on stderr, so the probe must not depend on which stream carried it.
            (mocks / "nvidia-smi").write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    if [ "${SSH_DEVICE_GUARD:-0}" = "1" ]; then
                      echo "No devices were found"
                      exit 6
                    fi
                    case "$MODE" in
                      mig)      printf 'Enabled\\nDisabled\\n'; exit 0 ;;
                      no-mig)   printf 'N/A\\nN/A\\n'; exit 0 ;;
                      noisy)    printf 'N/A\\nWARNING: infoROM is corrupted\\n'; exit 0 ;;
                      hidden)   echo "No devices were found"; exit 6 ;;
                      nodriver) echo "NVIDIA-SMI has failed to communicate with the driver" >&2
                                exit 9 ;;
                      missing)  exit 127 ;;
                    esac
                    """
                ),
                encoding="utf-8",
            )
            for mock in ("systemd-run", "nvidia-smi"):
                (mocks / mock).chmod(0o755)

            return subprocess.run(
                ["/bin/bash", "-c", script],
                env={
                    **os.environ,
                    "PATH": f"{mocks}:{os.environ['PATH']}",
                    "MODE": mode,
                    "SSH_DEVICE_GUARD": "1" if ssh_device_guard else "0",
                },
                text=True,
                capture_output=True,
                check=False,
            )

    def assert_operational_failure(self, result):
        """What the playbook reads as "the query never ran"."""
        self.assertEqual(result.returncode, 2)

    def assert_unsupported_device(self, result):
        """What the playbook reads as "these GPUs do not do MIG"."""
        self.assertEqual(result.returncode, 1)

    def test_mig_capable_devices_are_detected(self):
        for path in (MIG_PLAYBOOK, MIG_ROLE):
            with self.subTest(path=path):
                result = self.run_probe(path, "mig")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.split(), ["Enabled", "Disabled"])

    def test_devices_without_mig_support_skip_configuration(self):
        for path in (MIG_PLAYBOOK, MIG_ROLE):
            with self.subTest(path=path):
                self.assert_unsupported_device(self.run_probe(path, "no-mig"))

    def test_only_a_real_mig_mode_counts_as_a_capability(self):
        # nvidia-smi prints warnings alongside the values it was asked for, and
        # 'grep -v N/A' accepted those as a MIG mode.
        for path in (MIG_PLAYBOOK, MIG_ROLE):
            with self.subTest(path=path):
                self.assert_unsupported_device(self.run_probe(path, "noisy"))

    def test_a_failed_query_does_not_look_like_a_missing_capability(self):
        # Whether the diagnostic went to stdout, to stderr or nowhere at all,
        # a query that did not answer has to be distinguishable.
        for path in (MIG_PLAYBOOK, MIG_ROLE):
            for mode in ("hidden", "nodriver", "missing"):
                with self.subTest(path=path, mode=mode):
                    self.assert_operational_failure(self.run_probe(path, mode))

    def test_the_probe_escapes_an_active_ssh_device_guard(self):
        # The regression this PR is about: with the guard already applied, the
        # bare command sees no devices, and the transient unit still does.
        for path in (MIG_PLAYBOOK, MIG_ROLE):
            with self.subTest(path=path):
                result = self.run_probe(path, "mig", ssh_device_guard=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_probe_is_stable_across_repeated_runs(self):
        # Convergence runs after the login isolation is in place have to reach
        # the same answer as the run that applied it.
        for path in (MIG_PLAYBOOK, MIG_ROLE):
            with self.subTest(path=path):
                first = self.run_probe(path, "mig", ssh_device_guard=False)
                second = self.run_probe(path, "mig", ssh_device_guard=True)
                self.assertEqual(first.returncode, second.returncode)
                self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main()
