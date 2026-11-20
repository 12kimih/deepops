"""proc: the one way the package starts a process, and it is always bounded.

    proc.run(argv, timeout=SECONDS, **subprocess.run options)
    rc, out, err = proc.outcome(argv, timeout=SECONDS, input=None, env=None)

A process that never answers (a Slurm RPC the client retries for ever, a read from a
hung NFS mount) hangs whatever waits for it, so a call without a positive timeout is
refused. check defaults to True. Failures raise proc.CalledProcessError or
proc.TimeoutExpired (subprocess's own). Text output is decoded with errors="replace"
unless the caller says otherwise: one byte that is not UTF-8 in a job name, a comment
or a file must not stop a tool for everyone.

The process runs in a session of its own, and on a timeout (or an interrupt) the whole
session is killed: subprocess.run kills only the process it started, so the commands a
shell pipeline or runuser starts (a database dump and its gzip) would outlive it.

outcome() is run() for a check that reports instead of stopping (the validators): it
never raises, and gives the exit status with the stripped text output, 124 for a
timeout, 127 for a missing command and 126 for one that cannot be started, as a shell
would.
Python 3.9 or newer, standard library only.
"""

import os
import signal
import subprocess

CalledProcessError = subprocess.CalledProcessError
TimeoutExpired = subprocess.TimeoutExpired


def run(argv, *, timeout, check=True, input=None, capture_output=False, **kwargs):
    if not timeout or timeout <= 0:
        raise ValueError(f"{argv[0]}: a process needs a positive timeout")
    argv = list(argv)
    if kwargs.get("text") or kwargs.get("universal_newlines"):
        kwargs.setdefault("errors", "replace")
    if input is not None:
        kwargs.setdefault("stdin", subprocess.PIPE)
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    with subprocess.Popen(argv, start_new_session=True, **kwargs) as child:
        try:
            out, err = child.communicate(input, timeout=timeout)
        except BaseException:  # the timeout, or an interrupt: nothing it started survives
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate()
            raise
    if check and child.returncode:
        raise CalledProcessError(child.returncode, argv, out, err)
    return subprocess.CompletedProcess(argv, child.returncode, out, err)


def outcome(argv, *, timeout, input=None, env=None):
    """(returncode, stdout, stderr) of ARGV, text and stripped; never raises."""
    try:
        done = run(argv, timeout=timeout, check=False, input=input, env=env, capture_output=True, text=True)
    except TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return 127, "", f"command not found: {argv[0]}"
    except OSError as e:
        return 126, "", f"{argv[0]}: {e.strerror or e}"
    return done.returncode, done.stdout.strip(), done.stderr.strip()
