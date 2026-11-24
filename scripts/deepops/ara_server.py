"""The run history's API server (roles/ara_server) as its users see it: where it
listens, whether it answers, and what to say when it does not.

ARA records every run either way: without the server the recorder writes the same
database offline, about 4 s a task slower; nothing is lost. The server is a system
unit, enabled once and up after every boot, not something each run starts. The
recorder (plugins/callback/ara_default.py) and the doctor (scripts/validation/
deepops_doctor.py) share this; standard library only.
"""

import os
import socket
from urllib.parse import urlsplit

# ARA's own default api_server, which the recorder probes; roles/ara_server binds it
# (tests/ara-server keeps the three equal).
ADDRESS = "http://127.0.0.1:8000"
INSTALL = "uv run ansible-playbook -K playbooks/utilities/ara-server.yml"
# The database the server serves: ARA's default base directory (roles/ara_server's
# ara_server_base_dir), with its default database file in it.
BASE_DIR = "~/.ara/server"
# The settings by which a run points its offline client at another database.
DATABASE_SETTINGS = ("ARA_BASE_DIR", "ARA_DATABASE_NAME", "ARA_DATABASE_ENGINE", "ARA_SETTINGS")


def serves_this_run(environ=os.environ):
    """Whether the server holds the database this run would write offline: no setting
    points it elsewhere. A run with its own database (a test's, a scratch play's) is
    never sent to the server, whose history is the operator's: tests run beside a live
    server recorded into it (2026-11-24)."""
    base = environ.get("ARA_BASE_DIR")
    if base is not None and os.path.realpath(os.path.expanduser(base)) != os.path.realpath(
        os.path.expanduser(BASE_DIR)
    ):
        return False
    return not any(environ.get(name) for name in DATABASE_SETTINGS if name != "ARA_BASE_DIR")


def answers(url=ADDRESS, timeout=1.0):
    """Whether something accepts connections at URL's host and port (http only)."""
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "127.0.0.1", parts.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def offline_note(url=ADDRESS):
    """The one line a run without the server shows: what it means and the fix, once."""
    return (
        f"ARA: no server at {url}; this run records offline (nothing is lost, about 4 s a task"
        f" slower). Install the server once, it stays up across reboots: {INSTALL}"
    )
