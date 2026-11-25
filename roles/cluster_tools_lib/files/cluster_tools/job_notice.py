"""job-notice: Slurm's MailProg, delivering job notices as Slack DMs instead of mail.

    job-notice CONFIG.json -s SUBJECT RECIPIENT     (as slurmctld calls MailProg)

Slurm calls it only for jobs that asked (--mail-type, or SBATCH_MAIL_TYPE in the
user's shell), once per event, with the job in its environment (SLURM_JOB_ID,
SLURM_JOB_NAME, SLURM_JOB_MAIL_TYPE, SLURM_JOB_STATE, SLURM_JOB_RUN_TIME,
SLURM_JOB_EXIT_CODE_MAX, SLURM_JOB_USER, ...). The notice goes to the job's own user
(SLURM_JOB_USER) alone, a Slack member through the roster's chat IDs
(cluster_tools.slack.member): --mail-user may name anyone, so it is never a recipient.
The job's name is shown as text (Slack's control characters escaped).

A person's notices close together become one message: within `coalesce_seconds` of
the first, each later one is added to it (chat.update), so a burst of jobs ending
is one DM, not hundreds. Anyone may turn them off without touching their jobs: a
login listed under `muted` in the preferences file gets none.

CONFIG: token_file, members {login: Slack ID or email}, email_domain, state_file,
coalesce_seconds, prefs_file (optional; {"muted": [login, ...]}), dry_run.
Without a token it exits 0 and sends nothing: a notice is never worth a failed job.
"""

import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import config
from .files import write_atomic
from .slack import Error as SlackError
from .slack import Slack, escape, member

SHOWN = 15  # lines listed in a coalesced message; the rest are counted
USAGE = "usage: job-notice CONFIG.json -s SUBJECT RECIPIENT"


def recipient_login(recipient, email_domain):
    """The login a recipient names: a bare login, or <login>@<the site's domain>."""
    local, _, domain = recipient.partition("@")
    return local if not domain or domain == email_domain else ""


def line(env):
    """One line for a notice, from the job's environment."""
    state = env.get("SLURM_JOB_STATE", "") or env.get("SLURM_JOB_MAIL_TYPE", "")
    head = f"`{escape(env.get('SLURM_JOB_ID', '?'))}` {escape(env.get('SLURM_JOB_NAME', ''))}".rstrip()
    parts = [head, state, env.get("SLURM_JOB_RUN_TIME", "")]
    code = env.get("SLURM_JOB_EXIT_CODE_MAX")
    if code not in (None, "", "0"):
        parts.append(f"exit {code}")
    return ", ".join(p for p in parts if p)


def render(lines):
    """The message for one or more notices."""
    if len(lines) == 1:
        return f"Slurm 잡 알림: {lines[0]}"
    shown = "\n".join(lines[-SHOWN:])
    more = f"\n(앞의 {len(lines) - SHOWN}개 생략)" if len(lines) > SHOWN else ""
    return f"Slurm 잡 알림 {len(lines)}건\n{shown}{more}"


@contextmanager
def locked_state(path):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            state = {}
        yield state
        write_atomic(path, json.dumps(state), mode=0o600)


def deliver(slack, state, slack_id, login, text_line, now, window):
    """Post a new message, or add to the person's open one; returns the state entry."""
    open_ = state.get(slack_id)
    if open_ and now - open_["first"] < window:
        open_["lines"].append(text_line)
        slack.update(open_["channel"], open_["ts"], render(open_["lines"]))
        return open_
    # A job's start and end are the person's own business: in the journal, not logged
    # for the administrators (owner 2026-11-24).
    channel, ts = slack.dm(slack_id, render([text_line]), kind="job", login=login, log=False)
    state[slack_id] = {"channel": channel, "ts": ts, "first": now, "lines": [text_line]}
    return state[slack_id]


def muted(cfg, login):
    path = cfg.get("prefs_file")
    if not path:
        return False
    try:
        return login in (json.loads(Path(path).read_text()).get("muted") or [])
    except (OSError, ValueError):
        return False


CONFIG_KEYS = {"token_file": str, "members": dict, "email_domain": str, "state_file": str,
               "coalesce_seconds": int, "prefs_file": str}  # fmt: skip
CONFIG_OPTIONAL = {"dry_run": bool}


def load_config(path):
    """The config roles/job_notice writes (cluster_tools.config)."""
    return config.require(path, CONFIG_KEYS, CONFIG_OPTIONAL)


def main(argv=None, env=None, now=None):
    args = (sys.argv if argv is None else argv)[1:]
    env = os.environ if env is None else env
    if len(args) < 4 or args[1] != "-s":
        print(USAGE, file=sys.stderr)
        return 2
    try:
        cfg = load_config(args[0])
    except config.ConfigError as e:
        print(f"job-notice: {e}", file=sys.stderr)
        return 2
    token = Path(cfg["token_file"]).read_text().strip() if cfg.get("token_file") else ""
    # The job's own user: --mail-user (the recipient argument) may name anyone.
    login = env.get("SLURM_JOB_USER") or recipient_login(args[-1], cfg.get("email_domain", ""))
    if not token or not login or muted(cfg, login):
        return 0
    slack = Slack(token, dry_run=cfg.get("dry_run", False))
    now = time.time() if now is None else now
    try:
        with locked_state(cfg["state_file"]) as state:
            # Each person's open message ends with the window; "_members", the Slack ID
            # cache, keeps its entries for their own lifetime (slack.member).
            for key in [
                k
                for k, v in state.items()
                if not k.startswith("_") and now - v.get("first", 0) >= cfg["coalesce_seconds"]
            ]:
                del state[key]
            cache = state.setdefault("_members", {})
            slack_id = member(slack, login, cfg.get("members", {}), cfg.get("email_domain", ""), cache)
            if slack_id:
                deliver(slack, state, slack_id, login, line(env), now, cfg["coalesce_seconds"])
    except SlackError as e:
        print(f"job-notice: {e}", file=sys.stderr)  # slurmctld logs it; the job is unaffected
    return 0


if __name__ == "__main__":
    sys.exit(main())
