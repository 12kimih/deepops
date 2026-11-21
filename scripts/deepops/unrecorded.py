"""The environment of an Ansible run that leaves no record: a test or a molecule scenario.

    subprocess.run([... "ansible-playbook" ...], env=unrecorded.env(KEY="value"))

ansible.cfg records every run with ARA (callback_plugins = ./.callback_plugins), so the
operator's history shows what each run did. A run that is not the operator's points
ANSIBLE_CALLBACK_PLUGINS at an empty directory instead and loads no callback. The one
definition: scripts/deepops/molecule.py and every test that starts Ansible use it
(tests/run-record), and scripts/check.sh exports the same for the suites it runs.
Standard library only.
"""

import atexit
import os
import shutil
import tempfile

# How long one such run may take: a bound against a hang, not a performance test, so
# sized for a shared, loaded host (a 180 s bound failed at load 50, 2026-11).
TIMEOUT = 1800

_EMPTY = []


def callback_dir():
    """An empty directory, made once per process and removed at its exit."""
    if not _EMPTY:
        _EMPTY.append(tempfile.mkdtemp(prefix="deepops-no-callbacks-"))
        atexit.register(shutil.rmtree, _EMPTY[0], True)
    return _EMPTY[0]


def env(base=None, **extra):
    """BASE (os.environ by default) with EXTRA and no callback plugins."""
    return {**(os.environ if base is None else base), **extra, "ANSIBLE_CALLBACK_PLUGINS": callback_dir()}
