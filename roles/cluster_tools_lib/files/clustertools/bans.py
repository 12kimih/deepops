"""bans: fail2ban's bans on this host, read and lifted, for the cluster bot.

    cluster-bans list         every banned address, as JSON: [{"ip", "jail", "since"}]
    cluster-bans unban IP     IP's ban lifted in every jail, as JSON: {"ip", "unbanned"}
    cluster-bans serve        one of the above, from SSH_ORIGINAL_COMMAND (cluster-bot-serve)

Through fail2ban-client, the interface fail2ban documents: `status` names the jails,
`get JAIL banip --with-time` lists each jail's bans with when they began, and
`unban IP` lifts one in every jail and in fail2ban's database, so a restart does not
bring it back. Who may ask for an unban is the cluster bot's to decide (an
administrator approves); this tool takes only a well-formed address. Run as root.
Python 3.9 or newer, standard library only.
"""

import ipaddress
import json
import os
import sys

from . import bot_serve, proc
from .cli import run_main

TIMEOUT = 30
CLIENT = "fail2ban-client"


class Error(Exception):
    """A request the tool refuses, said plainly."""


def client(*words):
    """fail2ban-client's output; its refusal (fail2ban not running, say) as an Error."""
    try:
        return proc.run([CLIENT, *words], timeout=TIMEOUT, capture_output=True, text=True).stdout
    except proc.CalledProcessError as e:
        raise Error(f"{CLIENT} {words[0]}: {(e.stderr or e.stdout or '').strip() or e.returncode}") from None
    except proc.TimeoutExpired:
        raise Error(f"{CLIENT} {words[0]}: no answer within {TIMEOUT}s") from None


def jails(status):
    """The jail names in `fail2ban-client status` ("`- Jail list:\tsshd, recidive")."""
    for line in status.splitlines():
        if "Jail list:" in line:
            return [j.strip() for j in line.split(":", 1)[1].split(",") if j.strip()]
    return []


def bans(jail, text):
    """A jail's bans from `get JAIL banip --with-time`: one line each,
    "IP \\tBEGIN + BANTIME = END" (BEGIN and END as YYYY-MM-DD HH:MM:SS, local time)."""
    out = []
    for line in text.splitlines():
        ip, _, rest = line.strip().partition(" ")
        if ip:
            out.append({"ip": ip, "jail": jail, "since": rest.split(" + ", 1)[0].strip()})
    return out


def address(text):
    """TEXT as an IP address (v4 or v6), or an Error."""
    try:
        return str(ipaddress.ip_address(text.strip()))
    except ValueError:
        raise Error(f"not an IP address: {text!r}") from None


def list_bans(run=client):
    return [b for jail in jails(run("status")) for b in bans(jail, run("get", jail, "banip", "--with-time"))]


def unban(ip, run=client):
    ip = address(ip)
    return {"ip": ip, "unbanned": int(run("unban", ip).strip() or 0)}


ARITY = {"list": (0,), "unban": (1,)}


def served(original):
    """The argv a request over SSH (SSH_ORIGINAL_COMMAND) stands for, or an Error."""
    try:
        return ["cluster-bans", *bot_serve.request(original, ARITY)]
    except bot_serve.Error as e:
        raise Error(str(e)) from None


def main(argv=None):
    args = (sys.argv if argv is None else argv)[1:]
    if args == ["serve"]:
        try:
            args = served(os.environ.get("SSH_ORIGINAL_COMMAND"))[1:]
        except Error as e:
            print(f"cluster-bans: {e}", file=sys.stderr)
            return 2
    if not args or len(args) - 1 not in ARITY.get(args[0], ()):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2

    def body():
        try:
            result = list_bans() if args[0] == "list" else unban(args[1])
        except Error as e:
            print(f"cluster-bans: {e}", file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0

    return run_main("cluster-bans", body)


if __name__ == "__main__":
    sys.exit(main())
