"""ara_default: ARA's recorder (the ara package's own callback, its options and
behaviour unchanged), with every name and path it sends kept within the server's limit.

ARA's server stores a playbook's path, a file's path and a play's or task's name in
255 characters and refuses a longer one (400). For an ad-hoc command the path is
"Ad-Hoc: <module>: <arguments>", so any command of more than about 240 characters went
unrecorded, with a warning for every callback after it (a disk inventory, 2026-11-22:
"Failed to post on /api/v1/playbooks", then 'id', 'labels' and 'unhashable list'). ARA
1.8.0 shortens host names and labels itself, not these. The whole command stays in the
record's arguments (module_args), so a shortened path loses nothing.

ansible.cfg loads this file (callback_plugins = ./plugins/callback) in place of the
package's directory; ARA must be installed where Ansible runs (.venv, .venv-kubespray).
"""

from __future__ import annotations

from ara.plugins.callback.ara_default import DOCUMENTATION  # noqa: F401  # the loader reads it here
from ara.plugins.callback.ara_default import CallbackModule as AraCallbackModule

import socket
from urllib.parse import urlsplit

from ansible.utils.display import Display
from ara.clients import utils as client_utils

# ara.api.models: CharField(max_length=255) for Playbook.path, File.path, Play.name, Task.name
LIMIT = 255
BOUNDED = ("path", "name")


def bounded(value):
    """A name or path as the server stores it: the server (a REST framework CharField)
    strips surrounding blanks before it checks the length, so a cut that ends on a
    blank is stripped too. A file is found again by this value: one that differed by a
    trailing blank was created twice (500) and the run's tasks went unrecorded."""
    return value.strip()[:LIMIT].rstrip() if isinstance(value, str) else value


class BoundedClient:
    """ARA's API client, with each name and path it posts or patches bounded."""

    def __init__(self, client):
        self._client = client

    def _send(self, method, endpoint, **payload):
        return getattr(self._client, method)(
            endpoint, **{k: bounded(v) if k in BOUNDED else v for k, v in payload.items()}
        )

    def post(self, endpoint, **payload):
        return self._send("post", endpoint, **payload)

    def patch(self, endpoint, **payload):
        return self._send("patch", endpoint, **payload)

    def put(self, endpoint, **payload):
        return self._send("put", endpoint, **payload)

    def __getattr__(self, name):
        return getattr(self._client, name)


def server_answers(url, timeout=1.0):
    """Whether something accepts connections at URL's host and port (http only)."""
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "127.0.0.1", parts.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


class CallbackModule(AraCallbackModule):
    def set_options(self, task_keys=None, var_options=None, direct=None):
        super().set_options(task_keys=task_keys, var_options=var_options, direct=direct)
        # ARA's offline client starts its database layer for every request, about 4 s a
        # task; its API server (roles/ara_server, on ARA's default address) does not. When
        # nothing chose a client (offline is ARA's default) and that server answers, the
        # run records through it; with no server it records offline, as before.
        if self.client is not None and self.get_option("api_client") == "offline":
            server = self.get_option("api_server")
            if server_answers(server):
                self.client = client_utils.get_client(client="http", endpoint=server)
            else:
                Display().vv(f"ARA: no server at {server}; recording offline")
        if self.client is not None:
            self.client = BoundedClient(self.client)

    # A file is found again by the path the server holds (the playbook's, for an ad-hoc
    # command's tasks): the cache is keyed the same way, or the file is read from disk.
    def _get_or_create_file(self, path, content=None):
        return super()._get_or_create_file(bounded(path), content)
