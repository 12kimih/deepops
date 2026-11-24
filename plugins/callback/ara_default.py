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

import os
import socket
import sys

import requests
import urllib3
from ansible.utils.display import Display
from ara.clients.http import AraHttpClient

# The probe and the note, shared with the doctor (scripts/deepops/ara_server.py); realpath:
# the file may be linked into another callback directory (tests do that).
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "scripts", "deepops")
)
from ara_server import offline_note, serves_this_run, usable  # noqa: E402

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


class _UnixConnection(urllib3.connection.HTTPConnection):
    """An HTTP connection over the unix socket PATH (the host name is only a header)."""

    def __init__(self, path, **kwargs):
        super().__init__("localhost", **kwargs)
        self._path = path

    def _new_conn(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        timeout = self.timeout if isinstance(self.timeout, (int, float)) else None
        sock.settimeout(timeout)
        sock.connect(self._path)
        return sock


class _UnixPool(urllib3.connectionpool.HTTPConnectionPool):
    def __init__(self, path, **kwargs):
        super().__init__("localhost", **kwargs)
        self._path = path

    def _new_conn(self):
        return _UnixConnection(self._path, timeout=self.timeout.connect_timeout)


class UnixAdapter(requests.adapters.HTTPAdapter):
    """Every request of a session to one unix socket."""

    def __init__(self, path):
        super().__init__()
        self._pool = _UnixPool(path)

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        return self._pool

    def get_connection(self, url, proxies=None):
        return self._pool

    def request_url(self, request, proxies):
        return request.path_url

    def close(self):
        self._pool.close()
        super().close()


def displayed():
    """{"warnings": [...], "deprecations": [...]}: what this process's display showed,
    each once, as the terminal showed it."""
    display = Display()
    return {"warnings": sorted(display._warns), "deprecations": sorted(display._deprecations)}


def unix_client(path):
    """ARA's HTTP client, its every request sent over the unix socket PATH and never
    through a proxy from the environment (a site's http_proxy)."""
    client = AraHttpClient(endpoint="http://localhost")
    session = client.client.http
    session.trust_env = False
    session.mount("http://localhost", UnixAdapter(path))
    return client


class CallbackModule(AraCallbackModule):
    def set_options(self, task_keys=None, var_options=None, direct=None):
        super().set_options(task_keys=task_keys, var_options=var_options, direct=direct)
        # ARA's offline client starts its database layer for every request, about 4 s a
        # task; its API server (roles/ara_server) does not. When nothing chose a client
        # (offline is ARA's default), the run's database is the one the server serves,
        # and the running person's own server answers on its socket
        # (scripts/deepops/ara_server.py), the run records through it; otherwise offline.
        if self.client is not None and self.get_option("api_client") == "offline" and serves_this_run():
            path = usable()
            if path:
                self.client = unix_client(path)
            else:
                Display().display(offline_note())
        if self.client is not None:
            self.client = BoundedClient(self.client)

    # Ansible's own warnings and deprecations (a reserved variable name, the inventory, an
    # interpreter, a notice no task result carries) reach the terminal only: ARA records
    # just those a result carries, on its task. The run's display keeps each it showed
    # (ansible-core 2.19 and newer, deduplicated), so they are recorded with the playbook,
    # as the records "warnings" and "deprecations", and the playbook is labelled
    # "warnings" so a list finds it (2026-11-25: a run's warning was seen on screen only).
    def v2_playbook_on_stats(self, stats):
        if self.client is not None and self.playbook is not None:
            shown = displayed()
            for key, messages in shown.items():
                if messages:
                    self.client.post(
                        "/api/v1/records", playbook=self.playbook["id"], key=key, value=messages, type="list"
                    )
            if any(shown.values()):
                self._set_playbook_labels([label["name"] for label in self.playbook["labels"]] + ["warnings"])
        super().v2_playbook_on_stats(stats)

    # A file is found again by the path the server holds (the playbook's, for an ad-hoc
    # command's tasks): the cache is keyed the same way, or the file is read from disk.
    def _get_or_create_file(self, path, content=None):
        return super()._get_or_create_file(bounded(path), content)
