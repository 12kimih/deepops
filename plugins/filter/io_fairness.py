"""roles/io_fairness: the devices cluster_tools.io_fairness is given, from the site's models."""

from ansible.errors import AnsibleFilterError


def io_fairness_devices(models, enable):
    """[{mount, enable, model, qos}] from MODELS ({mount: {model, qos, ...}}, as
    scripts/deepops/io_model.py prints each), sorted by mount; the record of the run
    (measured, command, latency) stays in the config. An entry without its model or
    targets is refused here, before a host is touched."""
    out = []
    for mount, entry in sorted(dict(models).items()):
        missing = [k for k in ("model", "qos") if not isinstance((entry or {}).get(k), dict)]
        if missing:
            raise AnsibleFilterError(
                f"io_fairness_devices: {mount} has no {' or '.join(missing)} (io_model.py prints both)"
            )
        out.append(
            {"mount": mount, "enable": bool(enable), "model": dict(entry["model"]), "qos": dict(entry["qos"])}
        )
    return out


def io_notice_mounts(models):
    """{mount: {read_bps, write_bps}}: what the disk behind each mount serves, its model's
    sequential throughput (rbps, wbps), the I/O notices' measure of a node's share."""
    return {dev["mount"]: {"read_bps": dev["model"]["rbps"], "write_bps": dev["model"]["wbps"]}
            for dev in io_fairness_devices(models, False)}  # fmt: skip


class FilterModule:
    def filters(self):
        return {"io_fairness_devices": io_fairness_devices, "io_notice_mounts": io_notice_mounts}
