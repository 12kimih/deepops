"""site: what Slurm cannot know about this cluster (docs URL, dashboard URL, login host,
SSH port, system name), from the one file the cluster_tools_lib role renders from the
cluster_info setting. Every reader goes through info(), so each fact is written once.
Python 3.9 or newer, standard library only.
"""

import json
from functools import lru_cache

PATH = "/etc/cluster-tools/cluster.json"  # cluster_tools_lib_info_file


@lru_cache(maxsize=None)
def _read(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def info(path=PATH):
    """The site facts as a dict; {} when the file is absent or unreadable. Keys with no
    value are absent, so `info().get("docs_url")` is the whole test."""
    return dict(_read(path))
