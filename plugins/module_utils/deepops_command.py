"""A command an in-tree module runs, always bounded in time: the one way they run one.

    rc, out, err = deepops_command.run(module, ["sacctmgr", "-nP", "show", "user"])

AnsibleModule.run_command takes no timeout, so a command that hangs (sacctmgr with
slurmdbd gone, systemctl with a stuck manager, gpg waiting on an agent) held the whole
play with it. Here it runs in its own session and, past TIMEOUT seconds, the session is
killed (whatever it started too) and the module fails, naming the command. A command
that cannot start returns 127, as a shell would; its output is text.
"""

import os
import signal
import subprocess

TIMEOUT = 300
REAP_TIMEOUT = 5


def run(module, argv, timeout=TIMEOUT, data=None, environ_update=None):
    """(returncode, stdout, stderr) of ARGV, with DATA (str or bytes) on its stdin and
    this environment with ENVIRON_UPDATE's names set (or, given None, unset)."""
    env = dict(os.environ)
    for name, value in (environ_update or {}).items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    binary = isinstance(data, bytes)
    try:
        child = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except OSError as e:
        return 127, "", str(e)
    with child:
        try:
            out, err = child.communicate(data if binary or data is None else data.encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:  # a process that left the session may still hold the pipes
                child.communicate(timeout=REAP_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass
            module.fail_json(msg=f"{argv[0]} did not finish within {timeout}s: {' '.join(argv[:3])}")
    return child.returncode, out.decode(errors="replace"), err.decode(errors="replace")
