"""slurm_node: one node as Slurm sees it, from `scontrol --json show node <name>`.

    {{ reply.stdout | slurm_node }}

Slurm reports a base state and flags (["MIXED", "DRAIN"]). The maintenance steps read
`scontrol show node -o`'s State= with regular expressions before, and each misread it
its own way: MIXED+DRAIN (jobs still running) passed as drained, a pre-reboot
IDLE+DRAIN+NOT_RESPONDING as registered, a reason that merely contained "gres" as an
invalid registration. This reads the flags whole, through clustertools, the one
implementation of Slurm's JSON and node vocabulary in this repository, and answers the
questions those steps ask:

    found           Slurm knows the node (a first deploy: it does not)
    state           the full state as scontrol prints it, for messages: mixed+drain
    drain           the drain flag is set
    down            the base state is DOWN
    busy            jobs still run there (allocated, mixed or completing)
    responding      slurmctld hears from its slurmd
    invalid         its last registration did not match the configuration
    reason          why it is drained or down, as Slurm records it
    slurmd_started  when the slurmd that last registered started (UNIX time), or None
"""

import sys
from pathlib import Path

from ansible.errors import AnsibleFilterError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles" / "cluster_tools_lib" / "files"))
from clustertools import slurm, vocab  # noqa: E402


def slurm_node(text):
    try:
        nodes = slurm.parse("nodes", text)["nodes"]
    except slurm.SchemaError as err:
        raise AnsibleFilterError(f"slurm_node: {err}") from None
    if not nodes:
        return {"found": False}
    if len(nodes) != 1:
        raise AnsibleFilterError(f"slurm_node: expected one node, Slurm gave {len(nodes)}")
    node = nodes[0]
    flags = {f.upper() for f in node["state"]}
    return {
        "found": True,
        "state": vocab.node_state(node["state"]),
        "drain": vocab.node_drain(node["state"]),
        "down": vocab.node_base(node["state"]) == "down",
        "busy": bool(flags & {"ALLOCATED", "MIXED", "COMPLETING"}),
        "responding": "NOT_RESPONDING" not in flags,
        "invalid": "INVALID_REG" in flags,
        "reason": node.get("reason") or "",
        "slurmd_started": slurm.num(node.get("slurmd_start_time")),
    }


class FilterModule:
    def filters(self):
        return {"slurm_node": slurm_node}
