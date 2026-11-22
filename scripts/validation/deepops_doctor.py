#!/usr/bin/env python3
"""Preflight checks for a DeepOps provisioning environment.

Run this from the DeepOps repository root on the provisioning machine before
running cluster playbooks. It verifies the local environment (Ansible, Galaxy
dependencies, Kubespray submodule, configuration directory, inventory) and,
with ``--remote``, host reachability and GPU visibility over the configured
inventory.

The default output is one line per check. With ``--json`` the script prints a
single JSON object with a stable ``checks`` list so automation and AI agents
can consume the result directly.

Exit codes: 0 = all checks passed, 1 = one or more checks failed,
2 = not run from a DeepOps repository root.
"""

import argparse
import functools
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The validators start processes the cluster tools' one way (cluster_tools.proc).
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from cluster_tools import proc  # noqa: E402


# (rc, stdout, stderr), never raising; bounded (cluster_tools.proc).
run = functools.partial(proc.outcome, timeout=120)


def check(checks, name, ok, detail):
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    return ok


# Ansible's JSON callback: the oneline callback (-o) is deprecated in ansible-core 2.20
# and removed in 2.23, and its text was never meant to be parsed.
JSON_OUTPUT = {"ANSIBLE_LOAD_CALLBACK_PLUGINS": "1", "ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.json"}


def ad_hoc(inventory, module, args=None):
    """(rc, {host: result}) of one ad-hoc MODULE task on every host of INVENTORY."""
    argv = ["ansible", "all", "-i", inventory, "-m", module] + (["-a", args] if args else [])
    rc, out, _ = run(argv, timeout=300, env={**os.environ, **JSON_OUTPUT})
    try:
        return rc, json.loads(out)["plays"][0]["tasks"][0]["hosts"]
    except (ValueError, KeyError, IndexError):
        return rc, {}


def reached(results):
    """The hosts that answered: neither unreachable nor failed."""
    return [h for h, r in results.items() if not r.get("unreachable") and not r.get("failed")]


def count_positive_stdout_hosts(results):
    """Count hosts whose command printed a positive integer first."""
    hosts = 0
    for result in results.values():
        first = (result.get("stdout") or "").split("\n")[0].strip()
        if first.isdigit() and int(first) > 0:
            hosts += 1
    return hosts


# The fact the playbooks read for a host's NVIDIA GPUs (roles/facts), run as is.
GPUS_FACT = ROOT / "roles/facts/files/gpus.fact"


def count_gpu_hosts(results):
    """Count hosts whose gpus.fact answered a positive count."""
    hosts = 0
    for result in results.values():
        try:
            count = json.loads(result.get("stdout") or "").get("count", 0)
        except (ValueError, AttributeError):
            continue
        if isinstance(count, int) and count > 0:
            hosts += 1
    return hosts


def count_inventory_hosts(inventory_json):
    """Count hosts and detect DeepOps groups in ``ansible-inventory --list`` output."""
    hosts = set()
    meta = inventory_json.get("_meta", {}).get("hostvars", {})
    hosts.update(meta.keys())
    for group, data in inventory_json.items():
        if group == "_meta" or not isinstance(data, dict):
            continue
        hosts.update(data.get("hosts", []))
    groups = [g for g in inventory_json if g not in ("_meta", "all", "ungrouped")]
    return len(hosts), sorted(groups)


def main():
    parser = argparse.ArgumentParser(description="Preflight checks for a DeepOps provisioning environment.")
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    parser.add_argument(
        "--inventory",
        default="",
        help="inventory path (default: config/inventory via ansible.cfg)",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="also check host reachability and GPU visibility over SSH",
    )
    args = parser.parse_args()

    root = os.getcwd()
    if not os.path.exists(os.path.join(root, "ansible.cfg")) or not os.path.isdir(
        os.path.join(root, "playbooks")
    ):
        print(
            "error: run from the DeepOps repository root (ansible.cfg not found)",
            file=sys.stderr,
        )
        return 2

    checks, info = [], {}

    rc, out, _ = run(["ansible", "--version"], timeout=60)
    ansible_ok = check(
        checks,
        "ansible_installed",
        rc == 0,
        out.splitlines()[0] if rc == 0 and out else "install Ansible via ./scripts/setup.sh",
    )
    check(
        checks,
        "ansible_playbook_installed",
        shutil.which("ansible-playbook") is not None,
        "ansible-playbook on PATH" if shutil.which("ansible-playbook") else "missing ansible-playbook",
    )

    # The pinned collections and roles at their pinned versions, as galaxy.py --check
    # (the same check scripts/check.sh runs) decides: a non-empty roles/galaxy proved
    # nothing about the collections most modules come from.
    rc, out, err = run([sys.executable, os.path.join(root, "scripts", "deepops", "galaxy.py"), "--check"],
                       timeout=120)  # fmt: skip
    check(
        checks,
        "galaxy_dependencies_installed",
        rc == 0,
        "collections and roles match roles/requirements.yml"
        if rc == 0
        else ((err or out).splitlines() or ["galaxy.py --check failed"])[0] + "; run ./scripts/setup.sh",
    )

    kubespray_marker = os.path.join(root, "submodules", "kubespray", "cluster.yml")
    check(
        checks,
        "kubespray_submodule_initialized",
        os.path.exists(kubespray_marker),
        "submodules/kubespray present"
        if os.path.exists(kubespray_marker)
        else "run: git submodule update --init --recursive",
    )

    config_dir = os.environ.get("DEEPOPS_CONFIG_DIR", os.path.join(root, "config"))
    config_ok = check(
        checks,
        "config_dir_exists",
        os.path.isdir(config_dir),
        config_dir if os.path.isdir(config_dir) else "copy config.example/ to config/ and edit the inventory",
    )

    inventory = args.inventory or os.path.join(config_dir, "inventory")
    hosts_total = 0
    groups = []
    if ansible_ok and config_ok and os.path.exists(inventory):
        rc, out, err = run(["ansible-inventory", "-i", inventory, "--list"], timeout=120)
        parsed_ok = False
        if rc == 0:
            try:
                hosts_total, groups = count_inventory_hosts(json.loads(out))
                parsed_ok = True
            except json.JSONDecodeError:
                # Leave parsed_ok False; the inventory_parses check below
                # reports the failure with the ansible-inventory context.
                parsed_ok = False
        check(
            checks,
            "inventory_parses",
            parsed_ok,
            "%d host(s), groups: %s" % (hosts_total, ", ".join(groups))
            if parsed_ok
            else "ansible-inventory failed: %s" % (err or "unparseable output"),
        )
        if parsed_ok:
            check(
                checks,
                "inventory_has_hosts",
                hosts_total > 0,
                "%d host(s) defined" % hosts_total if hosts_total else "inventory defines no hosts",
            )
    else:
        check(
            checks,
            "inventory_parses",
            False,
            "inventory not found at %s" % inventory,
        )

    if args.remote and hosts_total > 0:
        rc, results = ad_hoc(inventory, "ping")
        reachable = len(reached(results))
        check(
            checks,
            "hosts_reachable",
            rc == 0,
            "%d/%d host(s) reachable" % (reachable, hosts_total)
            + (
                ""
                if rc == 0
                else "; for hosts that still ask for a password, run once:"
                " ansible-playbook -k playbooks/bootstrap/bootstrap-ssh.yml -l <hosts>"
            ),
        )

        rc, results = ad_hoc(inventory, "script", str(GPUS_FACT))
        # Facts for the operator, not checks: they cannot fail, so they are not in
        # "checks" (where a pass that cannot fail would mean nothing); a count that
        # could not be read is null, not 0.
        info["gpu_hosts"] = count_gpu_hosts(results) if rc == 0 else None

        rc, results = ad_hoc(
            inventory,
            "shell",
            "systemctl show ssh.service sshd.service -p DeviceAllow 2>/dev/null | grep -ci nvidiactl || true",
        )
        # Hosts that hide GPUs from SSH sessions (Slurm login GPU hiding): there a
        # direct nvidia-smi over SSH is expected to fail and srun is the GPU test.
        info["ssh_gpu_hiding_hosts"] = count_positive_stdout_hosts(results) if rc == 0 else None

    ok = all(c["ok"] for c in checks)
    result = {"ok": ok, "checks": checks, "info": info}

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for c in checks:
            print("%s %s: %s" % ("PASS" if c["ok"] else "FAIL", c["name"], c["detail"]))
        for key, value in sorted(info.items()):
            print("INFO %s: %s" % (key, "unknown" if value is None else value))
        print("ok=%s" % ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
