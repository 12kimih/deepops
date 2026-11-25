"""The bot's requests to the hosts it acts on: over its SSH key, whose one forced command
there (cluster_tools.bot_serve) runs only the services roles offer on that host.

    run = Remote(host, key, user, known_hosts, "bans")
    run("list") -> the service's JSON answer, or Failed with its reason
    run.offers() -> the services the host offers the bot (the deploy's check)

The request is the service's name and its words, quoted as a shell would quote them; the
host splits them back without running a shell. The client is fully given here, no
ssh_config read: a site's (host-based authentication, connection sharing) is for people
and Ansible, and its failures buried the bot's own reason.

    python3 -m cluster_bot.remote CONFIG ADDRESS   the services ADDRESS offers, as JSON
                                                   ({"offers": [...]} or {"failed": why})
"""

import json
import shlex
import subprocess
import sys

from .requests import Failed

TIMEOUT = 120
# The dispatcher's own request (cluster_tools.bot_serve.OFFERS).
OFFERS = "offers"
# What the deploy does about each way the bot is kept out: its check names what is missing.
DEPLOY = "`playbooks/generic/cluster-bot.yml`을 실행하면 무엇을 배포할지 알려 줍니다"
# ssh's own failures (exit 255), by the words of its message: (reason, what to do).
SSH_FAILURES = (
    (("Permission denied",), "봇의 키가 허용되지 않았습니다", DEPLOY),
    (("Host key verification failed", "REMOTE HOST IDENTIFICATION HAS CHANGED"),
     "호스트 키가 봇이 아는 것과 다릅니다", "`playbooks/generic/cluster-bot.yml`을 다시 실행해 호스트 키를 고정해 주세요"),
    (("Connection timed out", "Connection refused", "No route to host", "Network is unreachable",
      "Could not resolve", "Connection closed", "Connection reset"),
     "접속되지 않습니다", "호스트가 켜져 있고 sshd가 응답하는지 확인해 주세요"),
)  # fmt: skip


def _last_line(text):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def failure(returncode, stderr, stdout=""):
    """The Failed of a request that exited RETURNCODE: one short line, and the fix."""
    if returncode == 255:
        for needles, reason, fix in SSH_FAILURES:
            if any(n in stderr for n in needles):
                return Failed(reason, fix)
        return Failed(f"SSH 접속이 실패했습니다: {_last_line(stderr)[:120]}")
    line = _last_line(stderr) or _last_line(stdout)
    if "offers no" in line:  # the dispatcher: the host offers no such service
        return Failed("이 서비스를 제공하지 않습니다", DEPLOY)
    # The service's own refusal, without its program's name ("cluster-bans: ...").
    return Failed(line.split(": ", 1)[-1][:200] if line else f"거절했습니다 (종료 코드 {returncode})")


class Remote:
    def __init__(self, host, key, user, known_hosts, service):
        self.host, self.key, self.user, self.known_hosts, self.service = host, key, user, known_hosts, service

    def argv(self, request):
        """The whole ssh command for REQUEST: no config file, only the bot's key, only
        public-key authentication, only the host keys the deploy pinned."""
        options = {
            "BatchMode": "yes", "ConnectTimeout": "10", "IdentitiesOnly": "yes",
            "PreferredAuthentications": "publickey", "StrictHostKeyChecking": "yes",
            "UserKnownHostsFile": self.known_hosts, "GlobalKnownHostsFile": "none",
            "ControlMaster": "no", "ControlPath": "none", "LogLevel": "ERROR",
        }  # fmt: skip
        return ["ssh", "-F", "none", "-T", "-i", self.key,
                *(w for k, v in options.items() for w in ("-o", f"{k}={v}")),
                f"{self.user}@{self.host}", request]  # fmt: skip

    def _ask(self, request):
        try:
            done = subprocess.run(
                self.argv(request),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise Failed(
                f"{TIMEOUT}초 안에 답하지 않았습니다", "호스트가 느리거나 멈췄는지 확인해 주세요"
            ) from None
        if done.returncode != 0:
            raise failure(done.returncode, done.stderr, done.stdout)
        try:
            return json.loads(done.stdout)
        except ValueError:
            # A reply that is not the service's JSON is the host's failure, reported as
            # such, never an exception past the caller's report.
            raise Failed(f"알아볼 수 없는 답을 보냈습니다: {_last_line(done.stdout)[:80]!r}") from None

    def __call__(self, *words):
        return self._ask(shlex.join([self.service, *(str(w) for w in words)]))

    def offers(self):
        return self._ask(OFFERS)[OFFERS]


def main(argv=None):
    """The deploy's check of one host, as the bot (roles/cluster_bot, reach.yml)."""
    config, address = (sys.argv if argv is None else argv)[1:3]
    with open(config) as f:
        ssh = json.load(f)["ssh"]
    try:
        out = {"offers": Remote(address, ssh["key"], ssh["user"], ssh["known_hosts"], None).offers()}
    except Failed as e:
        out = {"failed": str(e), "fix": e.fix}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
