"""Where a role's own nftables table lives on a host (roles/nft_table), named once.

    {{ 'egress_policy' | nft_table_paths }} -> {"ruleset": "/etc/egress-policy.nft",
                                               "unit": "egress-policy.service",
                                               "loaded": "/run/egress-policy.loaded"}
    {{ 'egress_policy' | nft_table_items }} -> the unit and files, as component_record items

The table is `inet <name>`; its files and unit take the name with dashes. Fixed paths,
so a role's absent path finds what its present path wrote.
"""

import re

NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def nft_table_paths(name):
    if not NAME.match(str(name)):
        raise ValueError(f"an nftables table of deepops' is named like a variable, not {name!r}")
    stem = str(name).replace("_", "-")
    return {"ruleset": f"/etc/{stem}.nft", "unit": f"{stem}.service", "loaded": f"/run/{stem}.loaded"}


def nft_table_items(name):
    paths = nft_table_paths(name)
    return [f"unit:{paths['unit']}", f"file:/etc/systemd/system/{paths['unit']}", f"file:{paths['ruleset']}"]


class FilterModule:
    def filters(self):
        return {"nft_table_paths": nft_table_paths, "nft_table_items": nft_table_items}
