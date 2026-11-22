"""The bot's requests to the hosts it acts on: over its SSH key, whose one forced command
there (clustertools.bot_serve) runs only the services roles offer on that host.

    run = Remote(host, key, user, known_hosts, "scratch")
    run("list", "alice") -> the service's JSON answer, or Failed with its reason

The request is the service's name and its words, quoted as a shell would quote them; the
host splits them back without running a shell.
"""

import json
import shlex
import subprocess

from .requests import Failed

TIMEOUT = 120


class Remote:
    def __init__(self, host, key, user, known_hosts, service):
        self.host, self.key, self.user, self.known_hosts, self.service = host, key, user, known_hosts, service

    def __call__(self, *words):
        argv = ["ssh", "-i", self.key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self.known_hosts}",
                f"{self.user}@{self.host}", shlex.join([self.service, *(str(w) for w in words)])]  # fmt: skip
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, errors="replace", timeout=TIMEOUT, check=False
            )
        except subprocess.TimeoutExpired:
            raise Failed(f"{self.host} did not answer within {TIMEOUT}s") from None
        if done.returncode != 0:
            reason = (done.stderr or done.stdout).strip()
            raise Failed(reason.split(": ", 1)[-1] if reason else f"{self.host} refused ({done.returncode})")
        return json.loads(done.stdout)
