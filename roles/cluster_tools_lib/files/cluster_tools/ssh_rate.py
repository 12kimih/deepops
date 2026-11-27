"""ssh_rate: how many new SSH sessions each person opens on a login node, and the one
decision on a new login (roles/ssh_rate, docs/deepops/ssh-rate.md).

    ssh-rate CONFIG account     PAM account (sshd): exit 77 refuses this new login
    ssh-rate CONFIG session     PAM open_session (sshd): counts this new session
    ssh-rate CONFIG metrics     the node_exporter textfile of every count and block

Run by pam_exec through ssh-rate-gate, which turns only exit 77 into a refusal and
every other end (an error, a timeout, a missing interpreter) into an allowed login: a
fault here never locks anyone out (pam_slurm_adopt did, 2026-11-20).

One file per person under state_dir (root's, a tmpfs), locked while read and written:
the times of their new sessions within the window and, while blocked, of their refused
attempts. Past refuse sessions within window_minutes, a new login is refused for
block_minutes (mode refuse; mode warn counts and refuses nothing); a client that keeps trying while refused (refuse more attempts within the
window) extends the block by block_minutes, up to block_max_minutes from its start, so a
person who stops is free when the block ends and only a loop stays out. Only a new SSH
connection passes PAM: the channels of a ControlMaster connection and open sessions are
never counted or refused. root, the system's accounts (below UID_MIN of
/etc/login.defs: services) and exempt_users (the roster's administrators) are never
counted.
Python 3.9 or newer, standard library only.
"""

import fcntl
import json
import os
import pwd
import sys
import time

from .config import require
from .files import write_textfile
from .prom import key, render

CONFIG_KEYS = {
    "mode": str,
    "window_minutes": int,
    "warn_sessions": int,
    "refuse_sessions": int,
    "block_minutes": int,
    "block_max_minutes": int,
    "exempt_users": list,
    "state_dir": str,
    "textfile": str,
    "docs_url": str,
}
REFUSE = 77  # the gate's one refusal; any other exit allows
# refuse: past the level a new login is refused; warn: counted, shown and told, never
# refused (a first deploy that measures before it acts).
MODES = ("refuse", "warn")
MINUTE = 60
# The first words of the refusal, quoted by the user docs (site.json messages.ssh_rate).
REFUSED = "새 SSH 접속이 잠시 막혔습니다"


def load_config(path):
    cfg = require(path, CONFIG_KEYS)
    if cfg["mode"] not in MODES:
        raise ValueError(f"{path}: mode is one of {', '.join(MODES)}")
    if not 0 < cfg["warn_sessions"] <= cfg["refuse_sessions"]:
        raise ValueError(f"{path}: warn_sessions is above 0 and at most refuse_sessions")
    if not 0 < cfg["block_minutes"] <= cfg["block_max_minutes"]:
        raise ValueError(f"{path}: block_minutes is above 0 and at most block_max_minutes")
    return cfg


def uid_min(path="/etc/login.defs"):
    """The first UID of a person's account, as the system's own tools read it."""
    try:
        with open(path) as f:
            for line in f:
                words = line.split()
                if len(words) >= 2 and words[0] == "UID_MIN" and words[1].isdigit():
                    return int(words[1])
    except OSError:
        pass
    return 1000


def exempt(login, cfg):
    """root, a system account (a service), or one of exempt_users."""
    if login in cfg["exempt_users"]:
        return True
    try:
        account = pwd.getpwnam(login)
    except KeyError:
        return True  # no such account: sshd refuses it on its own
    return account.pw_uid < uid_min()


class State:
    """A person's file, locked from open to close (with State(...) as s)."""

    def __init__(self, cfg, login):
        os.makedirs(cfg["state_dir"], mode=0o700, exist_ok=True)
        self.path = os.path.join(cfg["state_dir"], login)
        self.data = {}

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        text = os.read(self.fd, 1 << 20).decode() or "{}"
        try:
            self.data = json.loads(text)
        except ValueError:
            self.data = {}
        return self

    def save(self):
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.ftruncate(self.fd, 0)
        os.write(self.fd, json.dumps(self.data).encode())

    def __exit__(self, *exc):
        os.close(self.fd)


def _recent(times, now, window):
    return [t for t in times or [] if now - t < window]


def decide(data, cfg, now):
    """(allowed, data after this attempt) for one new login, a pure function."""
    window, refuse = cfg["window_minutes"] * MINUTE, cfg["refuse_sessions"]
    data = {
        "sessions": _recent(data.get("sessions"), now, window),
        "refused": _recent(data.get("refused"), now, window),
        "blocked_until": float(data.get("blocked_until") or 0),
        "block_start": float(data.get("block_start") or 0),
    }
    if now < data["blocked_until"]:
        data["refused"].append(now)
        if len(data["refused"]) >= refuse:  # still looping: out for one more period
            cap = data["block_start"] + cfg["block_max_minutes"] * MINUTE
            data["blocked_until"] = min(data["blocked_until"] + cfg["block_minutes"] * MINUTE, cap)
            data["refused"] = []
        return False, data
    if len(data["sessions"]) >= refuse:
        data.update(blocked_until=now + cfg["block_minutes"] * MINUTE, block_start=now, refused=[now])
        return False, data
    data["refused"] = []
    return True, data


def message(data, cfg):
    """The refusal, in Korean, as sshd shows it to the person."""
    until = time.strftime("%H:%M", time.localtime(data["blocked_until"]))
    return (
        f"{REFUSED}: 최근 {cfg['window_minutes']}분 동안 새 SSH 접속을 "
        f"{len(data['sessions'])}번 열었습니다 (한도 {cfg['refuse_sessions']}번). "
        f"{until}에 다시 열립니다. 명령마다 새로 접속하지 말고 접속 하나를 다시 쓰세요: "
        f"~/.ssh/config에서 ControlMaster/ControlPersist를 켜세요. {cfg['docs_url']}"
    )


def account(cfg, login, now):
    """The exit status for PAM account: REFUSE, with the message on stdout, or 0."""
    if cfg["mode"] != "refuse" or exempt(login, cfg):
        return 0
    with State(cfg, login) as s:
        allowed, s.data = decide(s.data, cfg, now)
        s.save()
    if allowed:
        return 0
    print(message(s.data, cfg))  # shown by sshd to a password login (the gate logs it)
    return REFUSE


def session(cfg, login, now):
    """Count one new session (PAM open_session)."""
    if exempt(login, cfg):
        return 0
    with State(cfg, login) as s:
        s.data["sessions"] = _recent(s.data.get("sessions"), now, cfg["window_minutes"] * MINUTE) + [now]
        s.save()
    return 0


def metrics(cfg, now):
    """Each person's new sessions within the window and, while blocked, when it ends:
    the count and the time only, never an address or a session's details."""
    window = cfg["window_minutes"] * MINUTE
    values = {
        key("ssh_rate_warn_sessions"): cfg["warn_sessions"],
        key("ssh_rate_refuse_sessions"): cfg["refuse_sessions"],
        key("ssh_rate_window_seconds"): window,
        key("ssh_rate_last_success_timestamp_seconds"): int(now),
    }
    for login in sorted(os.listdir(cfg["state_dir"])) if os.path.isdir(cfg["state_dir"]) else []:
        if login.startswith("."):
            continue
        with State(cfg, login) as s:
            n = len(_recent(s.data.get("sessions"), now, window))
            # warn refuses no one: a block left from refuse mode is none
            until = float(s.data.get("blocked_until") or 0) if cfg["mode"] == "refuse" else 0
            if not n and until <= now:
                s.data = {}
                s.save()
                os.unlink(s.path)  # nothing left to count: the next session starts it again
                continue
        values[key("ssh_rate_new_sessions", user=login)] = n
        if until > now:
            values[key("ssh_rate_blocked_until_seconds", user=login)] = int(until)
            values[key("ssh_rate_block_start_seconds", user=login)] = int(s.data.get("block_start") or 0)
    write_textfile(cfg["textfile"], render(values))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[1] not in ("account", "session", "metrics"):
        print("usage: ssh-rate CONFIG account|session|metrics", file=sys.stderr)
        return 2
    cfg = load_config(argv[0])
    now = time.time()
    if argv[1] == "metrics":
        return metrics(cfg, now)
    # PAM's own items (pam_exec exports them): the service and the person logging in.
    if os.environ.get("PAM_SERVICE") != "sshd" or not os.environ.get("PAM_USER"):
        return 0
    login = os.environ["PAM_USER"]
    if "/" in login or login.startswith("."):
        return 0
    return account(cfg, login, now) if argv[1] == "account" else session(cfg, login, now)


if __name__ == "__main__":
    sys.exit(main())
