"""The environment of an Ansible run that leaves no record and reads no site secret: a
test or a molecule scenario.

    subprocess.run([... "ansible-playbook" ...], env=unrecorded.env(KEY="value"))

ansible.cfg records every run with ARA (callback_plugins = ./plugins/callback), so the
operator's history shows what each run did. A run that is not the operator's points
ANSIBLE_CALLBACK_PLUGINS at an empty directory instead and loads no callback. ansible.cfg
also names the site's vault password file (config/.vault-pass), without which Ansible
does not start; such a run needs no site secret, so it gets a throwaway password of its
own (a test that vaults something passes its own file), and runs where no site config
is (CI, a fresh clone) as where one is. The one
definition: scripts/deepops/molecule.py and every test that starts Ansible use it
(tests/run-record), and scripts/check.sh exports the same for the suites it runs.
Standard library only.
"""

import atexit
import os
import secrets
import shutil
import tempfile

# How long one such run may take: a bound against a hang, not a performance test, so
# sized for a shared, loaded host (a 180 s bound failed at load 50, 2026-11).
TIMEOUT = 1800

_DIR = []


def _scratch():
    """A directory, made once per process and removed at its exit, holding an empty
    callback directory and a throwaway vault password file."""
    if not _DIR:
        top = tempfile.mkdtemp(prefix="deepops-unrecorded-")
        atexit.register(shutil.rmtree, top, True)
        os.mkdir(os.path.join(top, "no-callbacks"))
        with open(os.path.join(top, "vault-pass"), "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(16) + "\n")
        _DIR.append(top)
    return _DIR[0]


def callback_dir():
    """An empty directory: no callback plugin loads from it."""
    return os.path.join(_scratch(), "no-callbacks")


def vault_password_file():
    """A vault password file that decrypts nothing of the site's."""
    return os.path.join(_scratch(), "vault-pass")


def env(base=None, **extra):
    """BASE (os.environ by default) with EXTRA, no callback plugins, facts cached in
    memory only and, unless EXTRA names one, the throwaway vault password file.

    ansible.cfg caches facts in the operator's ~/.cache/deepops/ansible-facts; a test
    play on localhost wrote its checkout's interpreter there (discovered_interpreter_python
    under /tmp), and once that checkout was gone every later localhost play, tests in
    other checkouts and real runs delegating to localhost, failed: "The module
    interpreter ... was not found" (2026-11-23)."""
    return {
        **(os.environ if base is None else base),
        "ANSIBLE_VAULT_PASSWORD_FILE": vault_password_file(),
        **extra,
        "ANSIBLE_CALLBACK_PLUGINS": callback_dir(),
        "ANSIBLE_CACHE_PLUGIN": "memory",
    }
