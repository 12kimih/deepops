"""tmpfiles.d(5): how the host's own rules keep a directory.

cat_config | tmpfiles_rule("/tmp") -> {"type": "D", "age": "30d"}

CAT_CONFIG is `systemd-tmpfiles --cat-config`, every rule in the order systemd reads
them (a file in /etc over one of the same name in /usr/lib). The first rule that
creates PATH decides, as for systemd-tmpfiles. "D" when its contents go at boot (D),
"d" otherwise (d, v, q, Q keep them), and "d" with no age when no rule creates it (as
Ubuntu leaves /var/tmp). AGE is the rule's cleanup age without its "~", "" for none.
"""

CREATE = ("d", "D", "v", "q", "Q")


def tmpfiles_rule(cat_config, path):
    for line in str(cat_config).splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#") or len(fields) < 2:
            continue
        kind = fields[0].rstrip("!-=~^+")
        if kind not in CREATE or fields[1] != path:
            continue
        age = fields[5].lstrip("~") if len(fields) > 5 and fields[5] != "-" else ""
        return {"type": "D" if kind == "D" else "d", "age": age}
    return {"type": "d", "age": ""}


class FilterModule:
    def filters(self):
        return {"tmpfiles_rule": tmpfiles_rule}
