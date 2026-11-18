"""GPU / Nodes: one GPU node at a time: its GPUs (DCGM), host resources (node_exporter)
and system power (the BMC through ipmi_exporter, or hwmon).

The server list comes from DCGM series, so it holds GPU nodes only, on Slurm (instance
host:9400) and Kubernetes (kubernetes_node) layouts alike; the hidden variables resolve
the node's DCGM and node_exporter instances.
"""

from grafana import at, dashboard, query, query_variable, row, stat, timeseries

PATH = "src/dashboards/gpu-nodes.json"

G = 'instance="$gpu_instance"'
N = 'instance="$node_instance"'
LOCAL_FS = 'fstype!~"tmpfs|ramfs|overlay|squashfs|nfs.*|cifs|fuse.*|autofs"'
ETHERNET = 'device=~"en.*|eth.*|bond.*"'
TRAFFIC_LIGHT = [(None, "green"), (80, "orange"), (90, "red")]


def per_gpu(metric, expr=None):
    return query(expr or f"max by (gpu) ({metric}{{{G}}})", "GPU {{gpu}}")


def gpu_total(metric, how="sum", scale=""):
    return f"{how}(max by (gpu) ({metric}{{{G}}})){scale}"


def mem(*names, minus=()):
    terms = " + ".join(f"node_memory_{n}_bytes{{{N}}}" for n in names)
    terms += "".join(f" - node_memory_{n}_bytes{{{N}}}" for n in minus)
    return f"max({terms})"


def rate_sum(metric, by, selector=""):
    sel = f"{N},{selector}" if selector else N
    return f"sum(max by ({by}) (rate({metric}{{{sel}}}[$__rate_interval])))"


def build():
    power = f'sum(ipmi_dcmi_power_consumption_watts{{host="$hostname"}}) or sum(node_hwmon_power_average_watt{{{N}}})'
    uname = f"max by (nodename, release) (node_uname_info{{{N}}})"
    gpu = dict(w=8, h=5, calcs=("lastNotNull",), fill=0)
    side = dict(w=7, h=5)
    host = dict(w=6, h=5, calcs=())  # narrow: a legend without values fits on one line
    big = dict(w=3, h=5, value_size=32)
    small = dict(w=3, h=2)
    panels = [
        row("$hostname", repeat="hostname"),
        # column 1: the host
        at(
            0,
            1,
            timeseries(
                "System Load",
                [query(f"max(node_load{m}{{{N}}})", f"{m}m") for m in (1, 5, 15)],
                unit="short",
                **host,
            ),
        ),
        at(
            0,
            6,
            timeseries(
                "System Memory",
                [
                    query(mem("MemTotal", minus=("MemFree", "Buffers", "Cached", "SReclaimable")), "Used"),
                    query(mem("Shmem"), "Shared (tmpfs, /dev/shm)"),
                    query(mem("Buffers", "Cached", "SReclaimable", minus=("Shmem",)), "Cache"),
                    query(mem("MemFree"), "Free"),
                ],
                unit="bytes",
                stack=True,
                description="As free(1) splits it: the four add up to the total.",
                **host,
            ),
        ),
        at(
            0,
            11,
            stat(
                "Disk Usage",
                f"1 - sum(max by (device) (node_filesystem_avail_bytes{{{N},{LOCAL_FS}}}))"
                f" / sum(max by (device) (node_filesystem_size_bytes{{{N},{LOCAL_FS}}}))",
                unit="percentunit",
                thresholds=[(None, "green"), (0.75, "orange"), (0.9, "red")],
                description="Local file systems (network and in-memory ones left out).",
                **big,
            ),
        ),
        at(
            3,
            11,
            stat(
                "Memory Used",
                f"max(1 - node_memory_MemAvailable_bytes{{{N}}} / node_memory_MemTotal_bytes{{{N}}}) * 100",
                unit="percent",
                thresholds=TRAFFIC_LIGHT,
                **big,
            ),
        ),
        at(
            0,
            16,
            timeseries(
                "Disk Throughput",
                [
                    query(
                        f"max by (device) (rate(node_disk_read_bytes_total{{{N}}}[$__rate_interval]))",
                        "Read {{device}}",
                    ),
                    query(
                        f"max by (device) (rate(node_disk_written_bytes_total{{{N}}}[$__rate_interval]))",
                        "Write {{device}}",
                    ),
                ],
                unit="Bps",
                negative="/^Write /",
                description="Writes are drawn below the axis.",
                **host,
            ),
        ),
        at(0, 21, stat("Hostname", uname, field="nodename", value_size=20, **small)),
        at(3, 21, stat("Kernel", uname, field="release", value_size=14, **small)),
        at(
            0,
            23,
            stat(
                "GPU SM Clock",
                gpu_total("DCGM_FI_DEV_SM_CLOCK", "avg", " * 1e6"),
                unit="hertz",
                value_size=24,
                **small,
            ),
        ),
        at(
            3,
            23,
            stat(
                "GPU Mem Clock",
                gpu_total("DCGM_FI_DEV_MEM_CLOCK", "avg", " * 1e6"),
                unit="hertz",
                value_size=24,
                **small,
            ),
        ),
        # columns 2 and 3: each GPU, and the node's total or average
        at(6, 1, timeseries("GPU Power", [per_gpu("DCGM_FI_DEV_POWER_USAGE")], unit="watt", **gpu)),
        at(
            14,
            1,
            stat(
                "GPU Power", gpu_total("DCGM_FI_DEV_POWER_USAGE"), unit="watt", description="All GPUs.", **big
            ),
        ),
        at(
            6,
            6,
            timeseries("GPU Temperature", [per_gpu("DCGM_FI_DEV_GPU_TEMP")], unit="celsius", min=None, **gpu),
        ),
        at(
            14,
            6,
            stat(
                "GPU Temp",
                gpu_total("DCGM_FI_DEV_GPU_TEMP", "avg"),
                unit="celsius",
                description="Average over the GPUs.",
                **big,
            ),
        ),
        at(
            6,
            11,
            timeseries("GPU Utilization", [per_gpu("DCGM_FI_DEV_GPU_UTIL")], unit="percent", max=100, **gpu),
        ),
        at(
            14,
            11,
            stat(
                "GPU Util",
                gpu_total("DCGM_FI_DEV_GPU_UTIL", "avg"),
                unit="percent",
                thresholds=TRAFFIC_LIGHT,
                description="Average over the GPUs.",
                **big,
            ),
        ),
        at(
            6,
            16,
            timeseries(
                "GPU Memory Copy Utilization",
                [per_gpu("DCGM_FI_DEV_MEM_COPY_UTIL")],
                unit="percent",
                max=100,
                **gpu,
            ),
        ),
        at(
            14,
            16,
            stat(
                "GPU Mem Copy",
                gpu_total("DCGM_FI_DEV_MEM_COPY_UTIL", "avg"),
                unit="percent",
                thresholds=[(None, "green"), (70, "orange"), (90, "red")],
                **big,
            ),
        ),
        at(
            6,
            21,
            timeseries(
                "GPU Memory Used",
                [
                    per_gpu(
                        "",
                        f"max by (gpu) (DCGM_FI_DEV_FB_USED{{{G}}}"
                        f" / (DCGM_FI_DEV_FB_USED{{{G}}} + DCGM_FI_DEV_FB_FREE{{{G}}}))",
                    )
                ],
                unit="percentunit",
                max=1,
                **gpu,
            ),
        ),
        at(
            14,
            21,
            stat("System Power", power, unit="watt", **big),
        ),
        # column 4: interconnects and the server's power over time
        at(
            17,
            1,
            timeseries(
                "NVLink Bandwidth",
                [query(gpu_total("DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL", "1e6 * sum"), "Total")],
                unit="Bps",
                no_value="No NVLink on this node",
                **side,
            ),
        ),
        at(
            17,
            6,
            timeseries(
                "PCIe Throughput",
                [
                    query(gpu_total("DCGM_FI_PROF_PCIE_RX_BYTES"), "Rx"),
                    query(gpu_total("DCGM_FI_PROF_PCIE_TX_BYTES"), "Tx"),
                ],
                unit="Bps",
                negative="Tx",
                description="All GPUs; transmit is drawn below the axis.",
                **side,
            ),
        ),
        at(
            17,
            11,
            timeseries(
                "Ethernet Throughput",
                [
                    query(rate_sum("node_network_receive_bytes_total", "device", ETHERNET), "Receive"),
                    query(rate_sum("node_network_transmit_bytes_total", "device", ETHERNET), "Transmit"),
                ],
                unit="Bps",
                negative="Transmit",
                description="Physical and bonded interfaces; transmit below the axis.",
                **side,
            ),
        ),
        at(
            17,
            16,
            timeseries(
                "InfiniBand Throughput",
                [
                    query(
                        rate_sum("node_infiniband_port_data_received_bytes_total", "device, port"), "Receive"
                    ),
                    query(
                        rate_sum("node_infiniband_port_data_transmitted_bytes_total", "device, port"),
                        "Transmit",
                    ),
                ],
                unit="Bps",
                negative="Transmit",
                no_value="No InfiniBand on this node",
                **side,
            ),
        ),
        at(17, 21, timeseries("System Power", [query(power, "Total")], unit="watt", **side)),
    ]
    hostname = query_variable(
        "hostname",
        'query_result(label_replace(DCGM_FI_DEV_POWER_USAGE{kubernetes_node!=""}, "hostname", "$1", "kubernetes_node", "(.+)")'
        ' or label_replace(DCGM_FI_DEV_POWER_USAGE{kubernetes_node="",instance=~".*:9400"}, "hostname", "$1", "instance", "(.*):9400"))',
        '/hostname="([^"]+)"/',
        label="server",
    )
    gpu_instance = query_variable(
        "gpu_instance",
        'query_result(DCGM_FI_DEV_POWER_USAGE{kubernetes_node="$hostname"}'
        ' or DCGM_FI_DEV_POWER_USAGE{kubernetes_node="",instance="$hostname:9400"})',
        '/instance="([^"]+)"/',
        hide=True,
    )
    node_instance = query_variable(
        "node_instance",
        'query_result(node_uname_info{nodename="$hostname"} or node_uname_info{instance="$hostname:9100"})',
        '/instance="([^"]+)"/',
        hide=True,
    )
    return dashboard(
        "gpu-nodes",
        "GPU / Nodes",
        panels,
        tags=["gpu", "dcgm"],
        description="One GPU node: GPUs (DCGM), host resources (node-exporter) and system power (BMC).",
        variables=[hostname, gpu_instance, node_instance],
        refresh="30s",
        time_from="now-6h",
    )
