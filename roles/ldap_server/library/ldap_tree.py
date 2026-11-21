#!/usr/bin/python
"""Make LDAP entries match a declaration, over ldapi as root (SASL EXTERNAL)."""

DOCUMENTATION = r"""
module: ldap_tree
short_description: Declare LDAP entries and their attributes; add, modify and prune to match
description:
  - Each entry in I(entries) is added when missing; each attribute it lists is made exactly
    the listed values (an empty list removes the attribute); attributes it does not list
    are left alone. One modify per entry, so values slapd checks together (a TLS
    certificate and its key) change together.
  - An attribute in an entry's C(merge) list only gains values, never loses one.
  - Under each DN in I(prune), children no entry declares are deleted; under each DN in
    I(report) they are only returned (C(undeclared)).
  - I(secrets) maps a DN to attributes whose values are secret (password hashes): they
    join that entry's attributes, kept out of every log. I(passwords) maps a DN to its
    password, checked with a bind and set with the password modify operation when the
    bind fails, so the server hashes it (olcPasswordHash). A new entry with a password
    gets an unusable one until then.
  - Works on cn=config as on data. Supports check mode; values are never returned.
  - Needs python3-ldap on the host.
options:
  uri:
    type: str
    default: ldapi:///
  entries:
    type: list
    elements: dict
    default: []
  secrets:
    type: dict
    default: {}
  prune:
    type: list
    elements: str
    default: []
  report:
    type: list
    elements: str
    default: []
  passwords:
    type: dict
    default: {}
"""

RETURN = r"""
added:
  description: DNs added.
  type: list
modified:
  description: DN -> the attribute names changed.
  type: dict
deleted:
  description: DNs deleted (pruned).
  type: list
passwords_set:
  description: DNs whose password was set.
  type: list
undeclared:
  description: Children of a I(report) DN that no entry declares.
  type: list
"""

from ansible.module_utils import deepops_diff
from ansible.module_utils.basic import AnsibleModule, missing_required_lib

try:
    import ldap
    import ldap.modlist
    import ldap.sasl

    HAS_LDAP = True
except ImportError:
    HAS_LDAP = False

# An unusable password for a new entry whose password is set right after (no crypt
# hash is "*"), so it never exists without one or with a guessable one.
UNUSABLE = "{CRYPT}*"


def norm(dn):
    """A DN for comparison: lowercase, no spaces around separators."""
    return ",".join("+".join(f"{a.lower()}={v.lower()}" for a, v, _ in rdn) for rdn in ldap.dn.str2dn(dn))


def depth(dn):
    return len(ldap.dn.str2dn(dn))


def changes(current, attributes, merge=()):
    """[(name, values)] to replace: what differs between CURRENT ({lower name: [str]})
    and the declared ATTRIBUTES (a pure function, for the tests)."""
    out = []
    for name, values in attributes.items():
        values = [str(v) for v in values]
        have = current.get(name.lower(), [])
        if name in merge:
            values = have + [v for v in values if v not in have]
        fold = (lambda v: v.lower()) if name.lower() == "objectclass" else (lambda v: v)
        if {fold(v) for v in have} != {fold(v) for v in values}:
            out.append((name, values))
    return out


def main():
    module = AnsibleModule(
        argument_spec={
            "uri": {"type": "str", "default": "ldapi:///"},
            "entries": {"type": "list", "elements": "dict", "default": []},
            "secrets": {"type": "dict", "default": {}, "no_log": True},
            "prune": {"type": "list", "elements": "str", "default": []},
            "report": {"type": "list", "elements": "str", "default": []},
            "passwords": {"type": "dict", "default": {}, "no_log": True},
        },
        supports_check_mode=True,
    )
    if not HAS_LDAP:
        module.fail_json(msg=missing_required_lib("python3-ldap"))
    p, check = module.params, module.check_mode
    result = {"added": [], "modified": {}, "deleted": [], "passwords_set": [], "undeclared": []}
    try:
        conn = ldap.initialize(p["uri"])
        conn.sasl_interactive_bind_s("", ldap.sasl.external())

        def read(dn):
            try:
                [(_, attrs)] = conn.search_s(dn, ldap.SCOPE_BASE, "(objectClass=*)", ["*"])
            except ldap.NO_SUCH_OBJECT:
                return None
            return {k.lower(): [v.decode() for v in vs] for k, vs in attrs.items()}

        declared = {norm(e["dn"]) for e in p["entries"]}
        for entry in sorted(p["entries"], key=lambda e: depth(e["dn"])):
            dn = entry["dn"]
            attrs = {**(entry.get("attributes") or {}), **(p["secrets"].get(dn) or {})}
            current = read(dn)
            if current is None:
                add = {k: [str(v).encode() for v in vs] for k, vs in attrs.items() if vs}
                if dn in p["passwords"] and "userPassword" not in add:
                    add["userPassword"] = [UNUSABLE.encode()]
                result["added"].append(dn)
                if not check:
                    conn.add_s(dn, ldap.modlist.addModlist(add))
                continue
            todo = changes(current, attrs, entry.get("merge") or ())
            if todo:
                result["modified"][dn] = [name for name, _ in todo]
                mods = [
                    (ldap.MOD_REPLACE, name, [v.encode() for v in values])
                    if values
                    else (ldap.MOD_DELETE, name, None)
                    for name, values in todo
                ]
                if not check:
                    conn.modify_s(dn, mods)

        for dn, secret in sorted(p["passwords"].items()):
            if check and dn in result["added"]:
                result["passwords_set"].append(dn)
                continue
            probe = ldap.initialize(p["uri"])
            try:
                probe.simple_bind_s(dn, secret)
                continue
            except ldap.INVALID_CREDENTIALS:
                pass
            finally:
                probe.unbind_s()
            result["passwords_set"].append(dn)
            if not check:
                conn.passwd_s(dn, None, secret)

        for base, remove in [(b, True) for b in p["prune"]] + [(b, False) for b in p["report"]]:
            try:
                children = conn.search_s(base, ldap.SCOPE_ONELEVEL, "(objectClass=*)", ["1.1"])
            except ldap.NO_SUCH_OBJECT:
                continue
            for dn, _ in sorted(children, key=lambda c: -depth(c[0])):
                if norm(dn) in declared:
                    continue
                if not remove:
                    result["undeclared"].append(dn)
                    continue
                result["deleted"].append(dn)
                if not check:
                    conn.delete_s(dn)
        conn.unbind_s()
    except ldap.LDAPError as e:
        info = e.args[0] if e.args and isinstance(e.args[0], dict) else {"desc": str(e)}
        module.fail_json(msg=f"LDAP: {info.get('desc', '')} {info.get('info', '')}".strip(), **result)
    changed = bool(result["added"] or result["modified"] or result["deleted"] or result["passwords_set"])
    # Entry and attribute names only: values may be secrets.
    diff = deepops_diff.actions(
        [f"add {dn}" for dn in result["added"]]
        + [f"modify {dn}: {', '.join(names)}" for dn, names in result["modified"].items()]
        + [f"set the password of {dn}" for dn in result["passwords_set"]]
        + [f"delete {dn}" for dn in result["deleted"]]
    )
    module.exit_json(changed=changed, diff=diff if changed else {}, **result)


if __name__ == "__main__":
    main()
