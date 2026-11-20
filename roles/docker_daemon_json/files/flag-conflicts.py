"""dockerd refuses to start when an option is given both as a flag and in daemon.json
("the following directives are specified both as a flag and in the configuration
file"). Reads {"daemon_json": path, "keys": [...], "units": [unit file, drop-ins...]} on
stdin and prints
the daemon.json keys (those in the file and those about to be written, "keys") the unit's
dockerd command line also sets, with the files naming them.

The unit files are read as systemd reads them: continuation lines joined, Environment=
assignments (quoted values kept whole), the last ExecStart= wins, $VAR and ${VAR}
expanded from them.
"""

import json
import re
import shlex
import sys

# dockerd flag -> daemon.json key (https://docs.docker.com/reference/cli/dockerd/).
FLAGS = {
    "--insecure-registry": "insecure-registries", "--registry-mirror": "registry-mirrors",
    "--log-opt": "log-opts", "--log-driver": "log-driver", "--live-restore": "live-restore",
    "--default-address-pool": "default-address-pools", "--group": "group", "-G": "group",
    "--storage-driver": "storage-driver", "-s": "storage-driver", "--storage-opt": "storage-opts",
    "--data-root": "data-root", "--exec-opt": "exec-opts", "--iptables": "iptables",
    "--dns": "dns", "--bip": "bip", "--mtu": "mtu", "--userns-remap": "userns-remap",
    "--default-runtime": "default-runtime", "--add-runtime": "runtimes", "--host": "hosts",
    "-H": "hosts", "--containerd": "containerd",
}  # fmt: skip


def lines(path):
    try:
        text = open(path).read()
    except OSError:
        return []
    return re.sub(r"\\\n", " ", text).splitlines()


def main():
    req = json.load(sys.stdin)
    try:
        keys = set(json.load(open(req["daemon_json"])))
    except (OSError, ValueError):
        keys = set()
    keys |= set(req.get("keys") or ())
    env, exec_start = {}, ("", "")
    for path in req["units"]:
        for line in lines(path):
            name, _, value = line.strip().partition("=")
            if name == "Environment":
                for word in shlex.split(value):
                    var, _, val = word.partition("=")
                    env[var] = (val, path)
            elif name == "ExecStart":
                exec_start = (value, path)
    argv, where = [], {exec_start[1]}
    for word in shlex.split(exec_start[0]):
        m = re.fullmatch(r"\$\{?(\w+)\}?", word)
        if m and m[1] in env:
            argv += shlex.split(env[m[1]][0])
            where.add(env[m[1]][1])
        else:
            argv.append(word)
    flags = {FLAGS[w.split("=", 1)[0]] for w in argv if w.split("=", 1)[0] in FLAGS}
    print(json.dumps({"conflicts": sorted(flags & keys), "files": sorted(p for p in where if p)}))


if __name__ == "__main__":
    main()
