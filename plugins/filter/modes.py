"""mode_exceeds: whether a file mode allows anything beyond a ceiling.

    when: item.stat.mode | mode_exceeds('0750')

Modes are octal strings as stat returns them ("0755"). A stricter mode (0700 under a
0750 ceiling) does not exceed it, so a role can take permissions away without
loosening what an owner chose.
"""


def mode_exceeds(mode, ceiling):
    return bool(int(str(mode), 8) & ~int(str(ceiling), 8) & 0o7777)


class FilterModule:
    def filters(self):
        return {"mode_exceeds": mode_exceeds}
