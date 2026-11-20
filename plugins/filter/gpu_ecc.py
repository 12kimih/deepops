"""Which GPUs differ from the ECC mode a site declares, from nvidia-smi's own report.

    nvidia-smi --query-gpu=index,ecc.mode.current,ecc.mode.pending --format=csv,noheader
    {{ stdout | gpu_ecc_plan('disabled') }} -> {"set": [indices whose pending mode differs],
                                                "reboot": [indices still running another mode],
                                                "unsupported": [indices with no ECC]}

The mode is stored on the GPU and survives reboots; a change is pending until the GPU
is reset, which on a node in service means its next reboot.
"""

MODES = ("enabled", "disabled")


def gpu_ecc_plan(stdout, mode):
    want = str(mode).lower()
    if want not in MODES:
        raise ValueError(f"ECC mode must be one of {MODES}, not {mode!r}")
    plan = {"set": [], "reboot": [], "unsupported": []}
    for line in (stdout or "").splitlines():
        fields = [f.strip().lower() for f in line.split(",")]
        if len(fields) != 3 or not fields[0].isdigit():
            continue
        index, current, pending = fields
        if current not in MODES or pending not in MODES:
            plan["unsupported"].append(int(index))
            continue
        if pending != want:
            plan["set"].append(int(index))
        if current != want:
            plan["reboot"].append(int(index))
    return plan


class FilterModule:
    def filters(self):
        return {"gpu_ecc_plan": gpu_ecc_plan}
