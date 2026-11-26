"""plugins/action/systemd_unit.py run by Ansible: a unit this run would install is
previewed under --check instead of failing, and everything else is systemd_service's."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402

PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
MISSING = "deepops-test-not-installed.service"
PLAY = f"""
- hosts: localhost
  gather_facts: false
  connection: local
  tasks:
    - name: start
      systemd_unit: {{name: {MISSING}, state: started, enabled: true, daemon_reload: true}}
    - name: stop
      systemd_unit: {{name: {MISSING}, state: stopped, enabled: false}}
"""


def enabled_unit():
    """A service unit enabled on this host."""
    out = subprocess.run(
        ["systemctl", "list-unit-files", "--type=service", "--state=enabled", "--no-legend", "--plain"],
        capture_output=True, text=True, timeout=bounds.CHILD,
    ).stdout  # fmt: skip
    return next((line.split()[0] for line in out.splitlines() if line.split()), None)


def run(*extra, play=PLAY):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "play.yml").write_text(play)
        env = unrecorded.env(
            ANSIBLE_ACTION_PLUGINS=str(ROOT / "plugins" / "action"),
            ANSIBLE_LOAD_CALLBACK_PLUGINS="1",
            ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json",
            ANSIBLE_LOCALHOST_WARNING="false",
        )
        done = subprocess.run([str(PLAYBOOK), "-i", "localhost,", "play.yml", *extra], cwd=tmp, env=env,
                              capture_output=True, text=True, timeout=unrecorded.TIMEOUT)  # fmt: skip
    tasks = json.loads(done.stdout)["plays"][0]["tasks"]
    return {t["task"]["name"]: t["hosts"]["localhost"] for t in tasks}


class LinksCreated(unittest.TestCase):
    """An enabled unit is enabled again: only systemctl's report of links it created is a
    change (a unit whose [Install] gained a target gets that link)."""

    def test_only_created_links_are_a_change(self):
        sys.path.insert(0, str(ROOT / "plugins" / "action"))
        from systemd_unit import links_created

        created = "Created symlink /etc/systemd/system/docker.service.wants/x.service -> /etc/systemd/system/x.service."
        self.assertEqual(links_created({"rc": 0, "stdout": "", "stderr": created}), [created])
        self.assertEqual(links_created({"rc": 0, "stdout": "", "stderr": ""}), [])
        self.assertEqual(
            links_created({"rc": 1, "stdout": "", "stderr": "Refusing to operate on alias name"}), []
        )


# What systemd-analyze verify default.target printed on a GPU node (ARA 344,
# 2026-11-26), where slurmd then never started at boot.
GPU_NODE = """\
power-policy.service: Found ordering cycle on nvidia-power-limit.service/start
power-policy.service: Found dependency on multi-user.target/start
power-policy.service: Found dependency on power-policy.service/start
power-policy.service: Job nvidia-power-limit.service/start deleted to break ordering cycle starting with power-policy.service/start
/usr/lib/systemd/system/foo.service:7: Unknown key name 'Foo' in section 'Service', ignoring.
"""


class OrderingCycles(unittest.TestCase):
    def test_the_cycle_and_its_units_and_nothing_else(self):
        sys.path.insert(0, str(ROOT / "plugins" / "action"))
        from systemd_unit import ordering_cycles

        found = ordering_cycles(GPU_NODE)
        self.assertEqual(len(found), 4, found)
        self.assertTrue(found[0].startswith("power-policy.service: Found ordering cycle"))
        self.assertEqual(ordering_cycles("foo.service:7: Unknown key name 'Foo'\n"), [])


FAKE_SYSTEMCTL = """#!/bin/sh
# systemctl show -P PROP UNIT: PROP's next value from $FAKE/PROP (one line a call, the
# last repeated), as a unit's state moves while it is watched.
[ "$1" = show ] || exit 1
f="$FAKE/$3"; n="$FAKE/.$3.n"
i=$(cat "$n" 2>/dev/null || echo 1); echo $((i + 1)) > "$n"
line=$(sed -n "${i}p" "$f"); [ -n "$line" ] || line=$(tail -n 1 "$f")
echo "$line"
"""


class Settle(unittest.TestCase):
    """2026-11-26: the cluster bot failed at import on every start; systemd said
    started, the deploy said ok, and it restarted 40 times."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "plugins" / "action"))
        import systemd_unit

        self.su = systemd_unit

    def watch(self, **props):
        with tempfile.TemporaryDirectory() as fake:
            for tool, text in (
                ("systemctl", FAKE_SYSTEMCTL),
                ("journalctl", "#!/bin/sh\necho 'ModuleNotFoundError: No module named days'\n"),
            ):
                path = Path(fake) / tool
                path.write_text(text)
                path.chmod(0o755)
            for prop, values in {
                "Type": "simple",
                "NRestarts": "0",
                "SubState": "running",
                "ActiveState": "active",
                **props,
            }.items():
                Path(fake, prop).write_text(values + "\n")
            env = {"PATH": f"{fake}:/usr/bin:/bin", "FAKE": fake}
            done = subprocess.run(["sh", "-c", self.su.SETTLE_SCRIPT, "sh", "x.service", "3"], env=env,
                                  capture_output=True, text=True, timeout=bounds.CHILD)  # fmt: skip
            return done.returncode, done.stdout

    def test_a_service_that_stays_up_passes(self):
        rc, out = self.watch()
        self.assertEqual((rc, out.strip()), (0, "active/running"))
        self.assertIsNone(self.su.settle_failure(rc, out))

    def test_a_restart_loop_fails_with_its_journal(self):
        rc, out = self.watch(ActiveState="activating", SubState="auto-restart")
        failure = self.su.settle_failure(rc, out)
        self.assertEqual(failure["state"], "activating/auto-restart")
        self.assertIn("No module named days", failure["journal"][0])

    def test_a_restart_while_watched_fails(self):
        rc, out = self.watch(NRestarts="0\n0\n1")
        self.assertEqual(self.su.settle_failure(rc, out)["state"], "restarted 1 time(s)")

    def test_a_oneshot_is_not_watched(self):
        self.assertEqual(self.watch(Type="oneshot", ActiveState="failed"), (0, "oneshot\n"))

    def test_only_a_started_service_is_watched(self):
        watched = self.su.watched
        self.assertTrue(watched({"name": "bot", "state": "restarted"}, {"changed": True}))
        self.assertTrue(watched({"name": "bot.service", "state": "started"}, {"changed": True}))
        self.assertFalse(watched({"name": "bot", "state": "started"}, {"changed": False}), "already running")
        self.assertFalse(watched({"name": "x.timer", "state": "started"}, {"changed": True}))
        self.assertFalse(watched({"name": "bot", "state": "stopped"}, {"changed": True}))
        self.assertFalse(watched({"name": "bot", "state": "restarted"}, {"changed": True, "failed": True}))


@unittest.skipUnless(Path("/run/systemd/system").is_dir(), "needs systemd")
class SystemdUnit(unittest.TestCase):
    def test_a_unit_not_installed_yet_is_previewed(self):
        got = run("--check", "--diff")
        start, stop = got["start"], got["stop"]
        self.assertTrue(start["changed"] and not start.get("failed"), start)
        self.assertEqual(
            start["diff"]["prepared"],
            f"would reload systemd\nwould enable {MISSING}\nwould start {MISSING}\n",
        )
        self.assertFalse(stop["changed"] or stop.get("failed"), "stopping what is not there is done")

    def test_an_enabled_unit_under_check_stays_unchanged(self):
        unit = enabled_unit()
        if not unit:
            self.skipTest("no enabled service unit")
        play = f"""
- hosts: localhost
  gather_facts: false
  connection: local
  tasks:
    - name: enabled
      systemd_unit: {{name: {unit}, enabled: true}}
"""
        got = run("--check", "--diff", play=play)
        self.assertFalse(got["enabled"].get("changed") or got["enabled"].get("failed"), got["enabled"])

    def enable_with(self, report):
        """Enable an enabled unit for real, systemd-analyze printing REPORT."""
        unit = enabled_unit()
        if not unit:
            self.skipTest("no enabled service unit")
        with tempfile.TemporaryDirectory() as fake:
            tool = Path(fake) / "systemd-analyze"
            tool.write_text(f"#!/bin/sh\ncat >&2 <<'EOF'\n{report}EOF\nexit 1\n")
            tool.chmod(0o755)
            play = f"""
- hosts: localhost
  gather_facts: false
  connection: local
  environment:
    PATH: "{fake}:{{{{ lookup('env', 'PATH') }}}}"
  tasks:
    - name: enabled
      systemd_unit: {{name: {unit}, enabled: true}}
"""
            return run(play=play)["enabled"]

    def test_an_ordering_cycle_fails_the_task_and_names_it(self):
        got = self.enable_with(GPU_NODE)
        self.assertTrue(got.get("failed"), got)
        self.assertIn("nvidia-power-limit.service/start deleted to break ordering cycle", got["msg"])
        self.assertEqual(len(got["cycles"]), 4)

    def test_other_warnings_of_verify_pass(self):
        got = self.enable_with("foo.service:7: Unknown key name 'Foo' in section 'Service', ignoring.\n")
        self.assertFalse(got.get("failed"), got)

    def test_a_real_run_still_fails_on_a_missing_unit(self):
        got = run()
        self.assertTrue(got["start"].get("failed"), got["start"])


if __name__ == "__main__":
    unittest.main()
