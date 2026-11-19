"""proc: the one way the package starts a process, and it is always bounded.

    proc.run(argv, timeout=SECONDS, **subprocess.run options)

A process that never answers (a Slurm RPC the client retries for ever, a read from a
hung NFS mount) hangs whatever waits for it, so a call without a positive timeout is
refused. check defaults to True. Failures raise proc.CalledProcessError or
proc.TimeoutExpired (subprocess's own). Python 3.9 or newer, standard library only.
"""

import subprocess

CalledProcessError = subprocess.CalledProcessError
TimeoutExpired = subprocess.TimeoutExpired


def run(argv, *, timeout, check=True, **kwargs):
    if not timeout or timeout <= 0:
        raise ValueError(f"{argv[0]}: a process needs a positive timeout")
    return subprocess.run(list(argv), check=check, timeout=timeout, **kwargs)
