#!/usr/bin/env python3
"""Check that `module` works in every way each installed shell starts, as a user meets it:
an unprivileged account (nobody) with a clean environment. Prints each case that fails
and exits 1; roles/lmod runs it after installing.

  lmod-shell-check.py --version 9.4.5 --shells bash,zsh,tcsh --ssh-reads-system-rc true
"""

import argparse
import os
import pwd
import socket
import subprocess
import sys

# How each shell starts: (case, argv after the shell). "ssh" is a command run over ssh:
# stdin a socket and SSH_CLIENT set, which is how bash recognizes it.
CASES = {
    "bash": [
        ("login", ["-l", "-c"]),
        ("interactive", ["-i", "-c"]),
        ("ssh command", ["-c"]),
        ("script started from a login", ["-l", "-c", "bash -c {cmd}"]),
    ],
    "zsh": [
        ("login", ["-l", "-c"]),
        ("interactive", ["-i", "-c"]),
        ("not interactive", ["-c"]),
        ("bash script started from a login", ["-l", "-c", "bash -c {cmd}"]),
    ],
    # tcsh takes -l only alone: the login reads its commands from stdin.
    "tcsh": [("login", ["-l"]), ("not interactive", ["-c"])],
    "fish": [("login", ["-l", "-c"]), ("not interactive", ["-c"])],
}
BINARIES = {"bash": "/bin/bash", "zsh": "/bin/zsh", "tcsh": "/bin/tcsh", "fish": "/usr/bin/fish"}
COMMAND = "module --version"


def run(shell: str, case: str, args: list[str], user: pwd.struct_passwd) -> subprocess.CompletedProcess:
    given = None
    if args == ["-l"]:
        argv, given = [BINARIES[shell], "-l"], COMMAND + "\n"
    elif "{cmd}" in args[-1]:
        argv = [BINARIES[shell], *args[:-1], args[-1].format(cmd=f"'{COMMAND}'")]
    else:
        argv = [BINARIES[shell], *args, COMMAND]
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": user.pw_dir,
        "USER": user.pw_name,
        "LOGNAME": user.pw_name,
        "TERM": "dumb",
        "LMOD_REDIRECT": "no",
    }
    if case == "ssh command":
        env |= {"SSH_CLIENT": "192.0.2.1 50000 22", "SHLVL": "0"}
    stdin, theirs = socket.socketpair() if case == "ssh command" else (subprocess.DEVNULL, None)
    if given is not None:
        stdin = None

    def drop() -> None:
        os.setgid(user.pw_gid)
        os.setgroups([])
        os.setuid(user.pw_uid)

    try:
        return subprocess.run(
            argv,
            stdin=stdin,
            input=given,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            preexec_fn=drop,
            cwd="/",
        )
    finally:
        if theirs is not None:
            stdin.close()
            theirs.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--shells", required=True)
    parser.add_argument("--ssh-reads-system-rc", choices=["true", "false"], required=True)
    parser.add_argument("--user", default="nobody")
    opts = parser.parse_args()
    user = pwd.getpwnam(opts.user)
    failed = []
    for shell in filter(None, opts.shells.split(",")):
        for case, args in CASES[shell]:
            if case == "ssh command" and opts.ssh_reads_system_rc == "false":
                continue  # bash reads only the user's ~/.bashrc there
            out = run(shell, case, args, user)
            text = out.stdout + out.stderr
            if f"Version {opts.version}" not in text:
                failed.append(f"{shell} ({case}): {text.strip()[-300:] or f'exit {out.returncode}'}")
    if failed:
        print(f"Lmod {opts.version} is missing in:")
        print("\n".join(f"  {f}" for f in failed))
        return 1
    print(f"Lmod {opts.version} in every start of: {opts.shells}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
