"""site_secrets_plan: what to do for each declared secret, from the stat results.

    {{ site_results | site_secrets_plan(installed_by_host, prefer) }}

site_results: the controller's stat of each declaration's path (a loop's results).
installed_by_host: host -> that host's stat of each declaration's adopt path (results
in the same order; skipped where nothing is adopted). prefer: the host to read an
adopted copy from when it holds one.

Each item: {decl, exists, checksums and paths: {host: sha256 / path} of the hosts
holding a copy, source: the host to adopt from, or "" to generate}.
"""


def site_secrets_plan(site_results, installed_by_host, prefer=""):
    plan = []
    for i, site in enumerate(site_results):
        checksums, paths = {}, {}
        for host, installed in sorted(installed_by_host.items()):
            stat = installed["results"][i].get("stat", {})
            if stat.get("exists"):
                checksums[host], paths[host] = stat.get("checksum", ""), stat["path"]
        holders = sorted(checksums)
        source = prefer if prefer in checksums else (holders[0] if holders else "")
        plan.append(
            {
                "decl": site["item"],
                "exists": site["stat"]["exists"],
                "checksums": checksums,
                "paths": paths,
                "source": source,
            }
        )
    return plan


class FilterModule:
    def filters(self):
        return {"site_secrets_plan": site_secrets_plan}
