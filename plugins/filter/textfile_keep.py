"""roles/textfile_keep: the producers a refresh runs (tasks_from: refresh)."""


def _on(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def textfile_keep_producer(producer):
    """{name, playbook, on} of PRODUCER: a unit name is one the calling role installs
    (no playbook, always on: missing, the refresh fails); a mapping names the playbook
    that installs it and whether its role runs it on the host (off: left alone)."""
    if isinstance(producer, str):
        return {"name": producer, "playbook": "", "on": True}
    if not isinstance(producer, dict) or not producer.get("name") or not producer.get("playbook"):
        from ansible.errors import AnsibleFilterError

        raise AnsibleFilterError(
            f"a textfile producer is a unit name or {{name, playbook, on}}, not {producer!r}"
        )
    return {
        "name": str(producer["name"]),
        "playbook": str(producer["playbook"]),
        "on": _on(producer.get("on", True)),
    }


class FilterModule:
    def filters(self):
        return {"textfile_keep_producer": textfile_keep_producer}
