"""The run history's API server (roles/ara_server) as its users see it: where it
listens, whether it is the running person's own and answers, and what to say when not.

ARA records every run either way: without the server the recorder writes the same
database offline, about 4 s a task slower; nothing is lost. The server is a system
unit, enabled once and up after every boot, not something each run starts.

It listens on a unix socket, never on TCP: on a shared login node a TCP port is every
user's to read, write and, while the server is down, take over (the first version
served 127.0.0.1:8000 to every account on master, 2026-11-24). Each person's server
has its own unit and its socket in /run/deepops-ara-<user>/, a directory systemd makes
for that person alone (0700, RuntimeDirectory=) and removes when the unit stops; only
root can make anything in /run. A run uses a socket only when it is a socket owned by
the person running, in a directory only they can enter.

The recorder (plugins/callback/ara_default.py) and the doctor (scripts/validation/
deepops_doctor.py) share this; standard library only.
"""

import os
import pwd
import socket
import stat

INSTALL = "uv run ansible-playbook -K playbooks/utilities/ara-server.yml"
# The database the server serves: ARA's default base directory (roles/ara_server's
# ara_server_base_dir), with its default database file in it.
BASE_DIR = "~/.ara/server"
# The settings by which a run points its offline client at another database.
DATABASE_SETTINGS = ("ARA_BASE_DIR", "ARA_DATABASE_NAME", "ARA_DATABASE_ENGINE", "ARA_SETTINGS")
# Where each person's server listens; roles/ara_server's ara_server_socket (a test keeps
# the two equal).
SOCKET = "/run/deepops-ara-{user}/ara.sock"


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


def socket_path(uid=None):
    """The socket of the server of the person with UID (default: the one running)."""
    uid = os.getuid() if uid is None else uid
    return SOCKET.format(user=pwd.getpwuid(uid).pw_name)


def owned(path, uid=None):
    """Why PATH is not this person's server socket, or "": a socket owned by UID in a
    directory owned by UID that no one else may enter."""
    uid = os.getuid() if uid is None else uid
    try:
        sock, parent = os.lstat(path), os.lstat(os.path.dirname(path))
    except OSError as e:
        return f"{path}: {e.strerror}"
    if not stat.S_ISSOCK(sock.st_mode) or sock.st_uid != uid:
        return f"{path} is not a socket of uid {uid}"
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid or parent.st_mode & 0o077:
        return f"{os.path.dirname(path)} is not a directory of uid {uid} alone"
    return ""


def answers(path, timeout=1.0):
    """Whether something accepts connections on the unix socket PATH."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            return True
    except OSError:
        return False


def usable(path=None, uid=None, timeout=1.0):
    """The socket a run records through, or "" (none of this person's answers)."""
    path = socket_path(uid) if path is None else path
    return path if not owned(path, uid) and answers(path, timeout) else ""


def offline_note(path=None):
    """The one line a run without the server shows: what it means and the fix, once."""
    path = socket_path() if path is None else path
    return (
        f"ARA: no server of yours at {path}; this run records offline (nothing is lost, about"
        f" 4 s a task slower). Install the server once, it stays up across reboots: {INSTALL}"
    )
