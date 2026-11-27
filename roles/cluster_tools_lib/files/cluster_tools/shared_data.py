"""shared_data: the shared data space's downloads, its ledger and its budget
(roles/shared_space).

    cluster-shared-data CONFIG.json serve      (root: the cluster bot's service, the
                                                request in SSH_ORIGINAL_COMMAND)
        list                                     the ledger and the usage, as JSON
        fetch KIND NAME URL SHA256|- MAX_BYTES REQUESTER
                                                 record the entry, start its download
        use KIND NAME LOGIN                      someone uses it (its requester: keeps it)
        remove KIND NAME                         its directory and its entry go
    cluster-shared-data CONFIG.json download KIND NAME
                                               (the writer; fetch starts it as a unit)
    cluster-shared-data CONFIG.json metrics    (root, a daily timer) the usage against
                                                the budget

The shared data (<root>/<kind>/<name>, kinds: datasets, models, containers) is written
by one account, the writer, and read by everyone. People ask the cluster bot for
something public (cluster_bot.data); an administrator approves; fetch checks the request
again here, records it and starts the download as a transient systemd unit running as
the writer, at the lowest best-effort I/O priority, so a long download outlives the
bot's request and never runs as root.

Public sources only: an https URL whose host resolves to global addresses alone (no
private, loopback or link-local network, and every redirect is checked the same way),
and, when the site names hosts, one of them or below it. The connection goes to the
address that was checked (TLS still verifies the name), never to a second lookup that
could answer otherwise, and no proxy from the environment is used. The file is kept as it is
published (an archive stays packed: many small files on the shared filesystem slow
everyone), its sha256 checked when one was given, and it may not grow past MAX_BYTES
(what was asked, within the budget).

The ledger records each entry: who asked, from where, its size and checksum, when it
came, and the uses people recorded (cluster_bot.data's cleanup reads them: access
times over NFS say nothing). Every change of it is one locked step.

Until the filesystem has project quotas, the usage is exported as the budget's project
(the disk quota metrics' project series, so the daily disk report and its alert show it
with a warn and a hard level, cluster_tools.disk_report):

  disk_quota_project_used_bytes{filesystem, project}
  disk_quota_project_limit_bytes{filesystem, project, kind}     warn, hard (the budget)
  shared_data_entries, shared_data_last_success_timestamp_seconds

and, while the bot's service is offered (bot_service exists), when the bot last read the
ledger (every cleanup round does): shared_data_bot_last_success_timestamp_seconds. The cleanup
runs in the bot, so this is what says it stopped (roles/prometheus rules shared-space.yml).

Python 3.9 or newer, standard library only.
"""

import fcntl
import hashlib
import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import stat
import sys
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager

from . import bot_serve, config, proc
from .cli import run_main
from .exits import CANNOT_RUN, OK, CannotRun, NoAnswer
from .files import write_atomic, write_textfile
from .prom import key, render

NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
LOGIN = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
SHA256 = re.compile(r"[0-9a-f]{64}")
ARITY = {"list": (0,), "fetch": (6,), "use": (3,), "remove": (2,)}
CHUNK = 1 << 20
# A read that stalls this long fails the download; the unit's RuntimeMaxSec bounds it all.
READ_TIMEOUT = 120
START_TIMEOUT = 60

CONFIG_KEYS = {
    "root": str,
    "kinds": list,
    "writer": str,
    "budget": int,
    "warn_fraction": (int, float),
    "ledger": str,
    "hosts": list,
    "command": str,
    "config": str,
    "project": str,
    "usage_metrics": bool,
    "textfile": str,
    "textfile_kept": str,
}
# bot_service: "" (no bot) when an older config lacks it.
CONFIG_OPTIONAL = {"bot_service": str}


class Error(CannotRun):
    """A request refused, or a download failed, said plainly."""


class Unanswered(NoAnswer, Error):
    """A source host did not answer: it does not resolve, or the download broke off."""


def load_config(path):
    """The config roles/shared_space writes (cluster_tools.config)."""
    cfg = config.require(path, CONFIG_KEYS, CONFIG_OPTIONAL)
    cfg.setdefault("bot_service", "")
    if not cfg["root"].startswith("/") or not all(NAME.fullmatch(k) for k in cfg["kinds"]):
        raise config.ConfigError(f"{path}: root is an absolute path and kinds are plain names")
    if cfg["budget"] < 1 or not 0 < cfg["warn_fraction"] <= 1:
        raise config.ConfigError(f"{path}: budget is 1 or more and warn_fraction in (0, 1]")
    return cfg


def public_url(url, hosts=(), resolve=socket.getaddrinfo):
    """URL when it is a public source, else an Error (checked())."""
    checked(url, hosts, resolve)
    return url


def checked(url, hosts=(), resolve=socket.getaddrinfo):
    """(host, port, addresses) of URL when it is a public source, else an Error: https,
    no credentials in it, one of HOSTS or below one (when any are named), resolving to
    global addresses only."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise Error(f"not a URL: {url!r}") from None
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise Error(f"only an https URL without credentials: {url!r}")
    if hosts and not any(host == h or host.endswith("." + h) for h in hosts):
        raise Error(f"{host} is not one of the sources this cluster takes ({', '.join(hosts)})")
    try:
        found = {ai[4][0] for ai in resolve(host, parts.port or 443, proto=socket.IPPROTO_TCP)}
    except OSError as e:
        raise Unanswered(f"{host} does not resolve: {e}") from None
    if not found or not all(ipaddress.ip_address(a.split("%")[0]).is_global for a in found):
        raise Error(f"{host} is not a public host ({', '.join(sorted(found))})")
    return host, parts.port or 443, sorted(found)


def path_of(cfg, kind, name):
    if kind not in cfg["kinds"] or not NAME.fullmatch(name or ""):
        raise Error(f"not a shared data entry: {kind}/{name}")
    return os.path.join(cfg["root"], kind, name)


@contextmanager
def ledger(cfg):
    """The ledger ({"kind/name": entry}), changed in place and written at the end, under
    one lock for the whole read, change and write; nothing is written on an error."""
    path = cfg["ledger"]
    fd = os.open(path + ".lock", os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with open(path) as f:
                doc = json.load(f)
        except FileNotFoundError:
            doc = {}
        yield doc
        write_atomic(path, json.dumps(doc, indent=1, sort_keys=True) + "\n")
    finally:
        os.close(fd)


def read_ledger(cfg):
    try:
        with open(cfg["ledger"]) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def usage(cfg):
    """{kind: bytes on disk} of the shared data: nothing followed through a link, no
    other filesystem entered."""
    out = {}
    for kind in cfg["kinds"]:
        top = os.path.join(cfg["root"], kind)
        total, stack = 0, [top]
        try:
            dev = os.lstat(top).st_dev
        except FileNotFoundError:
            out[kind] = 0
            continue
        while stack:
            try:
                entries = list(os.scandir(stack.pop()))
            except OSError:
                continue
            for e in entries:
                st = e.stat(follow_symlinks=False)
                total += st.st_blocks * 512
                if stat.S_ISDIR(st.st_mode) and st.st_dev == dev:
                    stack.append(e.path)
        out[kind] = total
    return out


def listing(cfg):
    kinds = usage(cfg)
    return {"entries": read_ledger(cfg), "kinds": kinds, "used": sum(kinds.values()), "budget": cfg["budget"]}


def unit_name(kind, name):
    return f"shared-data-{kind}-{name}"


def fetch(cfg, kind, name, url, sha256, max_bytes, requester, now, start=None, resolve=socket.getaddrinfo):
    """Record the entry and start its download (START(argv), systemd-run by default)."""
    where = path_of(cfg, kind, name)
    public_url(url, cfg["hosts"], resolve)
    if sha256 != "-" and not SHA256.fullmatch(sha256):
        raise Error("the checksum is a sha256 in hex, or -")
    if not LOGIN.fullmatch(requester):
        raise Error(f"not a login: {requester!r}")
    try:
        limit = int(max_bytes)
    except ValueError:
        raise Error(f"not a size in bytes: {max_bytes!r}") from None
    if limit < 1:
        raise Error("the size is 1 byte or more")
    used = sum(usage(cfg).values())
    with ledger(cfg) as doc:
        # Downloads still running count with what they may grow to.
        coming = sum(e.get("max_bytes", 0) for e in doc.values() if e.get("state") == "fetching")
        if used + coming + limit > cfg["budget"]:
            raise Error(
                f"{limit} bytes do not fit the budget ({used} used, {coming} coming, of {cfg['budget']})"
            )
        entry = doc.get(f"{kind}/{name}")
        if (entry and entry.get("state") != "failed") or os.path.lexists(where):
            raise Error(f"{kind}/{name} exists already")
        doc[f"{kind}/{name}"] = {"kind": kind, "name": name, "url": url, "sha256": sha256,
                                 "max_bytes": limit, "requester": requester, "asked": int(now),
                                 "state": "fetching", "uses": {}}  # fmt: skip
    argv = ["systemd-run", "--quiet", "--collect", f"--unit={unit_name(kind, name)}",
            f"--uid={cfg['writer']}", f"--gid={cfg['writer']}",
            "--property=Nice=10", "--property=IOSchedulingClass=best-effort",
            "--property=IOSchedulingPriority=7", "--property=RuntimeMaxSec=2d",
            cfg["command"], cfg["config"], "download", kind, name]  # fmt: skip
    try:
        (start or (lambda a: proc.run(a, timeout=START_TIMEOUT, capture_output=True, text=True)))(argv)
    except (OSError, proc.CalledProcessError, proc.TimeoutExpired) as e:
        with ledger(cfg) as doc:
            doc[f"{kind}/{name}"].update(state="failed", error=f"the download did not start: {e}")
        raise Error(f"the download did not start: {e}") from None
    return {"started": unit_name(kind, name)}


class _Pinned(http.client.HTTPSConnection):
    """A connection to ADDRESS for the name in HOST: the address that was checked, with
    the certificate verified against the name (SNI) and the name in the Host header."""

    def __init__(self, host, address, **kwargs):
        super().__init__(host, **kwargs)
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPS(urllib.request.HTTPSHandler):
    """Every https request (redirects too) is checked, then connected to the checked
    address: the check and the connection see the same host."""

    def __init__(self, hosts, resolve, context=None):
        self.context = context or ssl.create_default_context()
        super().__init__(context=self.context)
        self.hosts, self.resolve = hosts, resolve

    def https_open(self, req):
        _, _, addresses = checked(req.full_url, self.hosts, self.resolve)
        return self.do_open(
            lambda host, **kw: _Pinned(host, addresses[0], **{**kw, "context": self.context}), req
        )


def opener_for(hosts, resolve, context=None):
    """The download's opener: pinned https, redirects checked, no proxy, no plain http."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _PinnedHTTPS(hosts, resolve, context), _Checked(hosts, resolve)
    ).open


class _Checked(urllib.request.HTTPRedirectHandler):
    """Every redirect is to a public source too."""

    def __init__(self, hosts, resolve):
        self.hosts, self.resolve = hosts, resolve

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl, self.hosts, self.resolve)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def file_name(url):
    """The file's name from the URL's last path part, safe as one name: download when none."""
    base = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return base[:200] or "download"


def download(cfg, kind, name, now=time.time, opener=None, resolve=socket.getaddrinfo):
    """The download fetch started (as the writer): into a hidden directory beside its
    place, checked, then moved there in one rename; the ledger says how it ended."""
    where = path_of(cfg, kind, name)
    entry = read_ledger(cfg).get(f"{kind}/{name}") or {}
    if entry.get("state") != "fetching":
        raise Error(f"{kind}/{name} is not waiting for a download")
    partial = os.path.join(os.path.dirname(where), f".{name}.partial")
    shutil.rmtree(partial, ignore_errors=True)
    os.mkdir(partial, 0o755)
    try:
        url = public_url(entry["url"], cfg["hosts"], resolve)
        open_url = opener or opener_for(cfg["hosts"], resolve)
        digest, size = hashlib.sha256(), 0
        with open_url(urllib.request.Request(url, headers={"User-Agent": "cluster-shared-data"}),
                      timeout=READ_TIMEOUT) as resp:  # fmt: skip
            target = os.path.join(partial, file_name(resp.geturl() or url))
            with open(target, "xb") as out:
                while chunk := resp.read(CHUNK):
                    size += len(chunk)
                    if size > entry["max_bytes"]:
                        raise Error(f"larger than the {entry['max_bytes']} bytes asked for")
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        if entry["sha256"] != "-" and digest.hexdigest() != entry["sha256"]:
            raise Error(f"the sha256 is {digest.hexdigest()}, not the {entry['sha256']} given")
        os.rename(partial, where)
    except (Error, OSError, ValueError) as e:
        shutil.rmtree(partial, ignore_errors=True)
        with ledger(cfg) as doc:
            doc[f"{kind}/{name}"].update(state="failed", error=str(e), ended=int(now()))
        raise (Unanswered if isinstance(e, (NoAnswer, OSError)) else Error)(f"{kind}/{name}: {e}") from None
    with ledger(cfg) as doc:
        doc[f"{kind}/{name}"].update(state="done", bytes=size, sha256_got=digest.hexdigest(),
                                     file=os.path.basename(target), ended=int(now()))  # fmt: skip
    return {"done": f"{kind}/{name}", "bytes": size}


def use(cfg, kind, name, login, now):
    path_of(cfg, kind, name)
    if not LOGIN.fullmatch(login):
        raise Error(f"not a login: {login!r}")
    with ledger(cfg) as doc:
        entry = doc.get(f"{kind}/{name}")
        if not entry or entry.get("state") != "done":
            raise Error(f"no shared data {kind}/{name}")
        entry.setdefault("uses", {})[login] = int(now)
    return {"used": f"{kind}/{name}", "by": login}


def remove(cfg, kind, name):
    where = path_of(cfg, kind, name)
    with ledger(cfg) as doc:
        if os.path.islink(where):
            os.unlink(where)
        elif os.path.isdir(where):
            shutil.rmtree(where)  # entries below are unlinked, never followed
        elif os.path.lexists(where):
            os.unlink(where)
        gone = doc.pop(f"{kind}/{name}", None) is not None
    return {"removed": f"{kind}/{name}", "recorded": gone}


def mount_of(path):
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        path = os.path.dirname(path)
    return path


def round_file(cfg):
    return os.path.join(os.path.dirname(cfg["ledger"]), "bot-round")


def metrics(cfg, now):
    values = {
        key("shared_data_entries"): len(read_ledger(cfg)),
        key("shared_data_last_success_timestamp_seconds"): int(now),
    }
    if cfg["bot_service"] and os.path.exists(cfg["bot_service"]) and os.path.exists(round_file(cfg)):
        with open(round_file(cfg)) as f:
            values[key("shared_data_bot_last_success_timestamp_seconds")] = int(f.read().strip() or 0)
    if cfg["usage_metrics"]:
        labels = {"filesystem": mount_of(cfg["root"]), "project": cfg["project"]}
        values[key("disk_quota_project_used_bytes", **labels)] = sum(usage(cfg).values())
        values[key("disk_quota_project_limit_bytes", kind="hard", **labels)] = cfg["budget"]
        warn = int(cfg["budget"] * cfg["warn_fraction"])
        values[key("disk_quota_project_limit_bytes", kind="warn", **labels)] = warn
    write_textfile(cfg["textfile"], render(values), cfg["textfile_kept"])


def serve(cfg, original, now):
    words = bot_serve.request(original, ARITY)
    op, args = words[0], words[1:]
    if op == "list":  # the bot's rounds and its requests: stamped for the staleness alert
        write_atomic(round_file(cfg), f"{int(now)}\n")
        return listing(cfg)
    if op == "fetch":
        return fetch(cfg, *args, now)
    if op == "use":
        return use(cfg, *args, now)
    return remove(cfg, *args)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ops = {("serve", 0), ("metrics", 0), ("download", 2)}
    if len(argv) < 2 or (argv[1], len(argv) - 2) not in ops:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return CANNOT_RUN

    def body():
        cfg = load_config(argv[0])
        if argv[1] == "serve":
            result = serve(cfg, os.environ.get("SSH_ORIGINAL_COMMAND"), time.time())
        elif argv[1] == "download":
            result = download(cfg, *argv[2:])
        else:
            return metrics(cfg, time.time()) or OK
        print(json.dumps(result, sort_keys=True))
        return OK

    return run_main("cluster-shared-data", body)


if __name__ == "__main__":
    sys.exit(main())
