#!/usr/bin/env python3
"""Check an Open OnDemand portal on its host: services, the one listening port, TLS,
the login redirect, package integrity and the Slurm cluster file. Submits no job.

Run as root on the portal host:
    validate_open_ondemand.py --url https://portal.example.com:443 --json
"""

import argparse
import functools
import json
import os
import re
import shutil
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

# The validators start processes the cluster tools' one way (cluster_tools.proc).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import proc  # noqa: E402

PACKAGES = ["ondemand", "ondemand-dex", "ondemand-nginx", "ondemand-passenger", "nodejs"]
# The role installs this check with its package and conffile lists (one source).
DPKG_VERIFY = "/usr/local/sbin/open-ondemand-dpkg-verify"
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
CLUSTERS_D = Path("/etc/ood/config/clusters.d")


# (rc, stdout, stderr), never raising; bounded (cluster_tools.proc).
run = functools.partial(proc.outcome, timeout=60)


def check_services(result):
    for unit in ("apache2", "ondemand-dex"):
        rc, out, _ = run(["systemctl", "is-active", unit])
        result["services"][unit] = out.strip() or "unknown"
        if rc != 0:
            result["errors"].append(f"{unit} is not active")


def listening_ports():
    rc, out, err = run(["ss", "-Hlnt"])
    if rc != 0:
        raise RuntimeError(f"ss failed: {err.strip()}")
    ports = set()
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 4:
            ports.add(int(fields[3].rsplit(":", 1)[1]))
    return ports


def check_ports(result, port):
    try:
        ports = listening_ports()
    except RuntimeError as exc:
        result["errors"].append(str(exc))
        return
    result["port_listening"] = port in ports
    if port not in ports:
        result["errors"].append(f"nothing listens on port {port}")
    for other in (80, 443):
        if other != port and other in ports:
            result["errors"].append(f"port {other} is open; the portal should be the only listener")


def check_tls_and_login(result, url, host, port, min_days):
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=15) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        not_after = ssl.cert_time_to_seconds(cert["notAfter"])
        days = int((not_after - time.time()) // 86400)
        result["tls"] = {"verified": True, "not_after": cert["notAfter"], "days_left": days}
        if days < min_days:
            result["errors"].append(f"certificate expires in {days} days (< {min_days})")
    except (OSError, ssl.SSLError, KeyError, ValueError) as exc:
        result["tls"] = {"verified": False, "error": str(exc)}
        result["errors"].append(f"TLS check failed: {exc}")
        return

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    # As a browser asks: mod_auth_openidc answers a request that accepts no HTML (urllib
    # sends no Accept) with 401 instead of the redirect to the login.
    request = urllib.request.Request(
        url.rstrip("/") + "/pun/sys/dashboard", headers={"Accept": BROWSER_ACCEPT}
    )
    try:
        resp = opener.open(request, timeout=15)
        status, location = resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        with exc:  # the error is the response
            status, location = exc.code, exc.headers.get("Location", "")
    except (OSError, urllib.error.URLError) as exc:
        result["errors"].append(f"portal request failed: {exc}")
        return
    result["login"] = {"status": status, "location": location}
    # Unauthenticated, the dashboard must send the browser to the OIDC provider (Dex).
    if status not in (301, 302, 303, 307) or not re.search(r"/dex/|/oidc", location):
        result["errors"].append(
            f"dashboard did not redirect to the login (status {status}, Location {location!r})"
        )


def check_packages(result, version):
    rc, out, _ = run(["dpkg-query", "-W", "-f", "${Package} ${Version}\\n", *PACKAGES])
    installed = dict(line.split(" ", 1) for line in out.splitlines() if " " in line)
    result["packages"] = installed
    for pkg in PACKAGES:
        if pkg not in installed:
            result["errors"].append(f"{pkg} is not installed")
    if version and installed.get("ondemand") != version:
        result["errors"].append(f"ondemand is {installed.get('ondemand')}, expected {version}")
    rc, out, err = run([DPKG_VERIFY])
    diffs = [line for line in out.splitlines() if line.strip()]
    result["dpkg_verify_differences"] = diffs
    if rc == 127:
        result["errors"].append(
            f"{DPKG_VERIFY} is missing: the portal was not deployed by roles/open_ondemand"
        )
    elif rc != 0:
        result["errors"].append(f"{len(diffs)} package files differ from the packages (dpkg -V)")


def check_cluster_file(result):
    if not shutil.which("scontrol"):
        result["errors"].append("scontrol not found; cannot read ClusterName")
        return
    rc, out, _ = run(["scontrol", "show", "config"])
    m = re.search(r"^ClusterName\s*=\s*(\S+)", out, re.M)
    if not m:
        result["errors"].append("ClusterName not found in scontrol show config")
        return
    name = m.group(1)
    files = sorted(p.name for p in CLUSTERS_D.glob("*.y*ml")) if CLUSTERS_D.is_dir() else []
    result["cluster"] = {"slurm_cluster_name": name, "cluster_files": files}
    if files != [f"{name}.yml"]:
        result["errors"].append(f"clusters.d should hold exactly {name}.yml, holds {files}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="the portal URL, e.g. https://portal.example.com:8443")
    parser.add_argument("--version", default="", help="the ondemand package version expected")
    parser.add_argument("--min-cert-days", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    # dpkg -V reads package files only root may read: unprivileged, each would count as a
    # difference.
    if os.geteuid() != 0:
        print(
            "validate_open_ondemand.py: run as root (sudo): dpkg -V reads files only root can",
            file=sys.stderr,
        )
        return 2

    parts = urlsplit(args.url)
    if parts.scheme != "https" or not parts.hostname:
        parser.error("--url must be an https URL")
    port = parts.port or 443

    result = {"schema_version": 1, "ok": False, "url": args.url, "services": {}, "errors": []}
    check_services(result)
    check_ports(result, port)
    check_tls_and_login(result, args.url, parts.hostname, port, args.min_cert_days)
    check_packages(result, args.version)
    check_cluster_file(result)
    result["ok"] = not result["errors"]

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("ok" if result["ok"] else "FAILED")
        for err in result["errors"]:
            print(f"  - {err}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
