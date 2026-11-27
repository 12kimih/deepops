"""roles/ssh_rate under a real sshd and PAM, in a throwaway container (unprivileged, bound
to 127.0.0.1, removed after): the profile as pam-auth-update writes it, the gate and
cluster_tools.ssh_rate. Opt in with DEEPOPS_CONTAINER_TESTS=1 (it needs Docker and
downloads Ubuntu's openssh-server); the decision itself is tests/cluster-tools-lib/
test_ssh_rate.py.

Proven here, as before the first deploy (2026-11-28): below the level a login goes in,
past it one is refused; a key login sees "Connection closed", a password login the
message; an exempt account and another PAM service (su) are never refused; the channels
of a ControlMaster connection count once; a broken config, a hung decision and a
missing interpreter or a missing gate let the login in; a retrying client's block grows to its cap; warn
mode refuses no one."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/lib"))
import bounds  # noqa: E402

NAME = "deepops-ssh-rate-test"
PORT = 22922
CONFIG = {"mode": "refuse", "window_minutes": 10, "warn_sessions": 3, "refuse_sessions": 4, "block_minutes": 1,
          "block_max_minutes": 3, "exempt_users": ["boss"], "state_dir": "/run/deepops/ssh-rate",
          "textfile": "/run/ssh-rate.prom", "docs_url": "https://docs.example.invalid/start/connect/"}  # fmt: skip
DOCKERFILE = """FROM ubuntu:24.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \\
    openssh-server python3 >/dev/null && rm -rf /var/lib/apt/lists/*
RUN for u in kim:2001 boss:2002 lee:2003; do useradd -m -u ${u#*:} -s /bin/bash ${u%:*}; done \\
    && echo 'kim:pw-kim-1' | chpasswd && mkdir -p /run/sshd /etc/cluster-tools /usr/local/libexec
COPY lib /opt/lib
COPY ssh-rate ssh-rate-gate /usr/local/libexec/
COPY ssh-rate.json /etc/cluster-tools/
COPY deepops-ssh-rate /usr/share/pam-configs/
COPY id.pub /keys/id.pub
RUN chmod 0755 /usr/local/libexec/ssh-rate /usr/local/libexec/ssh-rate-gate \\
    && for u in kim boss lee; do install -d -o $u -m 700 /home/$u/.ssh; \\
       install -o $u -m 600 /keys/id.pub /home/$u/.ssh/authorized_keys; done \\
    && pam-auth-update --force --enable deepops-ssh-rate \\
    && printf 'PasswordAuthentication yes\\nUsePAM yes\\n' > /etc/ssh/sshd_config.d/test.conf
CMD ["/usr/sbin/sshd", "-D", "-e"]
"""


def render(template, values):
    text = (ROOT / "roles/ssh_rate/templates" / template).read_text()
    for name, value in values.items():
        text = text.replace("{{ " + name + " }}", value)
    return text.replace("{{ ansible_managed | comment }}", "# test")


@unittest.skipUnless(os.environ.get("DEEPOPS_CONTAINER_TESTS") == "1" and shutil.which("docker"),
                     "DEEPOPS_CONTAINER_TESTS=1 and Docker")  # fmt: skip
class Pam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="ssh-rate-")
        ctx = Path(cls.dir)
        shutil.copytree(ROOT / "roles/cluster_tools_lib/files/cluster_tools", ctx / "lib/cluster_tools",
                        ignore=shutil.ignore_patterns("__pycache__"))  # fmt: skip
        (ctx / "ssh-rate").write_text('#!/usr/bin/python3 -I\nimport sys\nsys.path.insert(0, "/opt/lib")\n'
                                      "from cluster_tools.ssh_rate import main\nsys.exit(main())\n")  # fmt: skip
        paths = {"_ssh_rate_command": "/usr/local/libexec/ssh-rate", "_ssh_rate_config": "/etc/cluster-tools/ssh-rate.json",
                 "_ssh_rate_gate": "/usr/local/libexec/ssh-rate-gate"}  # fmt: skip
        (ctx / "ssh-rate-gate").write_text(render("ssh-rate-gate.j2", paths))
        (ctx / "deepops-ssh-rate").write_text(render("pam-profile.j2", paths))
        (ctx / "ssh-rate.json").write_text(json.dumps(CONFIG))
        (ctx / "Dockerfile").write_text(DOCKERFILE)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(ctx / "id")], check=True)
        subprocess.run(["docker", "build", "-q", "-t", NAME, str(ctx)], check=True, capture_output=True)
        subprocess.run(["docker", "run", "-d", "--rm", "--name", NAME, "-p", f"127.0.0.1:{PORT}:22", NAME],
                       check=True, capture_output=True)  # fmt: skip
        # A short control path: a socket path is at most 104 bytes.
        cls.control = tempfile.mktemp(prefix="srcm-", dir="/tmp")  # noqa: S306 - ssh makes the socket itself

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["docker", "stop", NAME], capture_output=True)
        subprocess.run(["docker", "rmi", "-f", NAME], capture_output=True)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def ssh(self, user, *extra, command="true"):
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10", "-i", f"{self.dir}/id", "-p", str(PORT), *extra,
                f"{user}@127.0.0.1", command]  # fmt: skip
        if not any(e.startswith("ControlPath=") for e in extra):  # a new connection each time
            argv[1:1] = ["-o", "ControlMaster=no", "-o", "ControlPath=none"]
        return subprocess.run(argv, capture_output=True, text=True, timeout=bounds.CHILD)

    def inside(self, script):
        return subprocess.run(["docker", "exec", NAME, "sh", "-c", script], capture_output=True, text=True)

    def state(self, user):
        return json.loads(self.inside(f"cat /run/deepops/ssh-rate/{user}").stdout or "{}")

    def reset(self, *users, block=False):
        for user in users:
            data = {"blocked_until": 9999999999, "block_start": 9999999000} if block else {}
            self.inside(f"echo '{json.dumps(data)}' > /run/deepops/ssh-rate/{user}")

    def test_1_past_the_level_a_new_login_is_refused_and_what_each_login_sees(self):
        self.inside("mkdir -p /run/deepops/ssh-rate")
        self.reset("kim")
        self.assertEqual([self.ssh("kim").returncode for _ in range(4)], [0, 0, 0, 0])
        refused = self.ssh("kim")
        self.assertEqual(refused.returncode, 255)
        self.assertNotIn("새 SSH 접속", refused.stderr, "a key login sees no PAM message")
        if shutil.which("sshpass"):
            password = subprocess.run(
                ["sshpass", "-p", "pw-kim-1", "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                 "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password", "-p", str(PORT),
                 "kim@127.0.0.1", "true"], capture_output=True, text=True, timeout=bounds.CHILD)  # fmt: skip
            self.assertIn(
                "새 SSH 접속이 잠시 막혔습니다", password.stderr, "a password login sees the message"
            )

    def test_2_exempt_accounts_and_other_services_are_never_refused(self):
        self.reset("kim", block=True)
        self.assertEqual({self.ssh("boss").returncode for _ in range(8)}, {0})
        self.assertEqual(self.inside("for i in 1 2 3 4 5 6; do su kim -c true || exit 1; done").returncode, 0)

    def test_3_controlmaster_channels_count_once(self):
        self.reset("lee")
        master = self.ssh("lee", "-o", "ControlMaster=yes", "-o", f"ControlPath={self.control}",
                          "-o", "ControlPersist=60", "-fN", command="")  # fmt: skip
        self.assertEqual(master.returncode, 0, master.stderr)
        try:
            for _ in range(8):
                self.assertEqual(
                    self.ssh("lee", "-o", "ControlMaster=no", "-o", f"ControlPath={self.control}").returncode,
                    0,
                )
            self.assertEqual(len(self.state("lee")["sessions"]), 1)
        finally:
            self.ssh("lee", "-o", f"ControlPath={self.control}", "-O", "exit", command="")

    def test_4_a_broken_decision_lets_the_login_in(self):
        self.reset("kim", block=True)
        self.assertEqual(self.ssh("kim").returncode, 255, "blocked")
        for broken, restore in (
            ("cp /etc/cluster-tools/ssh-rate.json /tmp/c && echo '{bad' > /etc/cluster-tools/ssh-rate.json",
             "cp /tmp/c /etc/cluster-tools/ssh-rate.json"),
            ("cp /usr/local/libexec/ssh-rate /tmp/h && printf '#!/bin/sh\\nsleep 30\\n' > /usr/local/libexec/ssh-rate",
             "cp /tmp/h /usr/local/libexec/ssh-rate"),
            ("mv /usr/bin/python3.12 /tmp/py", "mv /tmp/py /usr/bin/python3.12"),
        ):  # fmt: skip
            with self.subTest(broken=broken):
                self.inside(broken)
                try:
                    self.assertEqual(self.ssh("kim").returncode, 0)
                finally:
                    self.inside(restore)
        self.assertEqual(self.ssh("kim").returncode, 255, "restored: blocked again")

    def test_4b_a_missing_gate_lets_the_login_in(self):
        """The PAM line runs the gate only when it is there: a gate removed by hand, a half
        run or a rename never refuses anyone (2026-11-20's lockout class)."""
        self.reset("kim", block=True)
        self.inside("mv /usr/local/libexec/ssh-rate-gate /tmp/gate")
        try:
            self.assertEqual(self.ssh("kim").returncode, 0)
            self.inside(
                "cp /tmp/gate /usr/local/libexec/ssh-rate-gate && chmod 0644 /usr/local/libexec/ssh-rate-gate"
            )
            self.assertEqual(self.ssh("kim").returncode, 0, "there but not executable")
        finally:
            self.inside(
                "mv /tmp/gate /usr/local/libexec/ssh-rate-gate && chmod 0755 /usr/local/libexec/ssh-rate-gate"
            )
        self.assertEqual(self.ssh("kim").returncode, 255, "back: blocked again")

    def test_5_a_retrying_client_is_kept_out_up_to_the_cap(self):
        self.reset("kim")
        for _ in range(4):
            self.ssh("kim")
        self.ssh("kim")
        first = self.state("kim")
        for _ in range(12):
            self.ssh("kim")
        later = self.state("kim")
        self.assertEqual(round(first["blocked_until"] - first["block_start"]), 60)
        self.assertEqual(
            round(later["blocked_until"] - later["block_start"]), 180, "extended to block_max_minutes"
        )

    def test_6_warn_mode_refuses_no_one(self):
        self.reset("kim", block=True)
        self.inside('sed -i \'s/"mode": "refuse"/"mode": "warn"/\' /etc/cluster-tools/ssh-rate.json')
        try:
            self.assertEqual({self.ssh("kim").returncode for _ in range(6)}, {0})
        finally:
            self.inside('sed -i \'s/"mode": "warn"/"mode": "refuse"/\' /etc/cluster-tools/ssh-rate.json')


if __name__ == "__main__":
    unittest.main()
