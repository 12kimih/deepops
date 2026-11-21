"""Merge one owner's keys into /etc/docker/daemon.json (roles/docker_daemon_json).

Reads {"path", "record", "want", "drop_if", "check"} as JSON on stdin; with "check"
true nothing is written, and the exit status and output say what a run would change. The keys the owner set
last time are in its record; a key there that it no longer wants is removed, so
turning a setting off removes it instead of leaving it for good. Other owners' keys
are left alone. Exit 10 when the file changed, 0 when it did not; standard output says
which keys changed and whether dockerd applies them all on SIGHUP (a reload) or only
when it restarts: {"changed": [...], "reload": true}.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# What dockerd reconfigures on SIGHUP without a restart (dockerd reference,
# "Configuration reload behavior": https://docs.docker.com/reference/cli/dockerd/).
# Anything else, or a key missing from this list, takes a restart.
RELOADABLE = {
    "debug", "labels", "live-restore", "max-concurrent-downloads", "max-concurrent-uploads",
    "max-download-attempts", "default-runtime", "default-stop-timeout", "runtimes",
    "authorization-plugins", "insecure-registries", "registry-mirrors", "shutdown-timeout",
}  # fmt: skip

args = json.load(sys.stdin)
path, record = Path(args["path"]), Path(args["record"])
want, drop_if, check = args["want"], args["drop_if"], args.get("check", False)

text = path.read_text().strip() if path.exists() else ""
data = json.loads(text) if text else {}
before = json.dumps(data, sort_keys=True)
owned = json.loads(record.read_text()) if record.exists() else []
for key in set(owned) - set(want):
    data.pop(key, None)
for key, value in drop_if.items():
    if key not in want and data.get(key) == value:
        data.pop(key)
data.update(want)
changed = json.dumps(data, sort_keys=True) != before
old = json.loads(before)
keys = sorted(k for k in set(old) | set(data) if old.get(k) != data.get(k))
print(json.dumps({"changed": keys, "reload": bool(keys) and set(keys) <= RELOADABLE}))

if check:
    sys.exit(10 if changed else 0)
if changed:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".daemon.json-")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(sorted(want)) + "\n")
sys.exit(10 if changed else 0)
