"""Values written into systemd unit files."""

from ansible.errors import AnsibleFilterError


def systemd_exec_arg(value):
    """One word of an Exec*= command line that systemd hands the program exactly as
    given: double-quoted, \\ and " escaped, % doubled (no specifier expands) and $
    doubled (no variable expands). systemd.service(5), Command lines; systemd.unit(5),
    Specifiers. A newline cannot be carried and is refused."""
    text = str(value)
    if "\n" in text or "\r" in text:
        raise AnsibleFilterError(f"systemd_exec_arg: a line break cannot be passed: {text!r}")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


class FilterModule:
    def filters(self):
        return {"systemd_exec_arg": systemd_exec_arg}
