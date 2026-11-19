"""CUDA releases and driver branches, from one table.

    nvidia_cuda_release_driver_branch: {"12.8": 570, "13.0": 580, ...}
    {{ table | cuda_min_driver_branch }}        -> {12: 525, 13: 580}: the oldest branch
                                                   each CUDA major runs on
    {{ table | cuda_release_for_driver(580) }}  -> "13.0": the newest CUDA release the
                                                   driver branch ships with

NVIDIA's release notes pair every CUDA release with the driver branch it ships with
(minor version compatibility: a major runs on the branch of its first release). Every
other driver/CUDA relation in the repository is computed from that one table.
"""


def _releases(table):
    return sorted(((tuple(int(p) for p in str(k).split(".")), int(v)) for k, v in table.items()))


def cuda_min_driver_branch(table):
    out = {}
    for (major, _minor), branch in _releases(table):
        out.setdefault(major, branch)
    return out


def cuda_release_for_driver(table, branch):
    served = [release for release, needs in _releases(table) if needs <= int(branch)]
    if not served:
        raise ValueError(f"no CUDA release in the table runs on driver branch {branch}")
    major, minor = served[-1]
    return f"{major}.{minor}"


class FilterModule:
    def filters(self):
        return {
            "cuda_min_driver_branch": cuda_min_driver_branch,
            "cuda_release_for_driver": cuda_release_for_driver,
        }
