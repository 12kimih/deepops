"""The LDAP directory a people roster declares (roles/ldap_server, docs/deepops/accounts.md).

    {{ people | directory_entries(suffix, groups=directory_groups,
                                  netgroups=directory_netgroups, readers=ldap_reader_passwords,
                                  shadow=getent_shadow, ends=people_login_ends) }}

gives {entries, secrets, passwords, prune, report} for library/ldap_tree.py (secrets
and passwords are the values never to log). The tree is RFC 2307,
what NIS served, one to one:

    ou=people     uid=<login>: inetOrgPerson, posixAccount, shadowAccount; the roster
                  UID, GID = UID, home, shell. Someone leaving expires on the day ENDS
                  gives (login: shadow day, people_login_ends: their last day plus the
                  grace period). Someone who has left keeps the entry (the UID stays
                  reserved), expired (shadowExpire 1), nologin, no password.
    ou=groups     cn=<login>: the per-user group (GID = UID); cn=<name>: each cluster
                  group with its pinned GID and memberUid.
    ou=netgroups  cn=<name>: nisNetgroup with (,user,) triples.
    ou=services   cn=<reader>: the read-only accounts SSSD and Dex search with.

Groups, netgroups and readers are the whole declaration: anything else under their
branch is removed. People are never removed: an entry the roster does not know is
reported, so the roster gains it (status: left) instead.

Passwords: with SHADOW (getent_shadow of the account source, while NIS is the source of
truth) each active person the source holds gets that hash as {CRYPT} (none if it is
locked there); without it the directory owns passwords (users change them with passwd).
Left people's are always removed.
"""

UID_MIN = 1000


def _dn(rdn, branch, suffix):
    return f"{rdn},{branch},{suffix}"


def _usable(hash_):
    """A crypt(3) hash a bind can verify: not locked (!), not disabled (*), not empty."""
    return isinstance(hash_, str) and hash_.startswith("$")


def directory_entries(
    people,
    suffix,
    groups=(),
    netgroups=(),
    readers=None,
    home_root="/home",
    shell="/bin/bash",
    nologin="/usr/sbin/nologin",
    shadow=None,
    exclusive=True,
    ends=None,
):
    from ansible.errors import AnsibleFilterError

    people = people or {}
    active = {n: p or {} for n, p in people.items() if (p or {}).get("status", "active") == "active"}
    problems, secrets = [], {}
    entries = [
        {"dn": f"ou={ou},{suffix}", "attributes": {"objectClass": ["organizationalUnit"], "ou": [ou]}}
        for ou in ("people", "groups", "netgroups", "services")
    ]
    for login, p in sorted(people.items()):
        p = p or {}
        name = p.get("name") or login
        attrs = {
            "objectClass": ["inetOrgPerson", "posixAccount", "shadowAccount"],
            "uid": [login],
            "cn": [name],
            "sn": [login],
            "uidNumber": [str(p["uid"])],
            "gidNumber": [str(p["uid"])],
            "homeDirectory": [f"{home_root}/{login}"],
            # gecos is IA5 (ASCII) in RFC 2307; SSSD falls back to cn without it.
            "gecos": [name] if name.isascii() else [],
        }
        dn = _dn(f"uid={login}", "ou=people", suffix)
        if login in active:
            day = (ends or {}).get(login, "")
            attrs |= {
                "loginShell": [p.get("shell") or shell],
                "shadowExpire": [str(day)] if day != "" else [],
            }
            # Mirrored only where the source has the account: one it no longer holds (its
            # local copy retired, playbooks/utilities/retire-local-accounts.yml) keeps
            # the password the directory has; a locked one is locked here too.
            if shadow is not None and login in shadow:
                hash_ = (shadow[login] or [""])[0]
                if _usable(hash_):
                    secrets[dn] = {"userPassword": [f"{{CRYPT}}{hash_}"]}
                else:
                    attrs["userPassword"] = []
        else:
            # Expiry stops every login SSSD checks (ldap_account_expire_policy shadow), SSH
            # keys too; no password stops a Dex bind.
            attrs |= {"loginShell": [nologin], "shadowExpire": ["1"], "userPassword": []}
        entries.append({"dn": dn, "attributes": attrs})
        entries.append(
            {
                "dn": _dn(f"cn={login}", "ou=groups", suffix),
                "attributes": {"objectClass": ["posixGroup"], "cn": [login], "gidNumber": [str(p["uid"])]},
            }
        )

    ids = {str(p["uid"]): n for n, p in (people or {}).items()}
    for g in groups or ():
        gid, members = str(g["gid"]), list(g.get("members") or [])
        if g["name"] in people:
            problems.append(f"group {g['name']}: a person's login, whose per-user group it would replace")
        if gid in ids:
            problems.append(f"group {g['name']}: gid {gid} is {ids[gid]}'s per-user group")
        if int(gid) < UID_MIN:
            problems.append(f"group {g['name']}: gid {gid} is below {UID_MIN}, the OS's own range")
        unknown = sorted(set(members) - set(active))
        if unknown:
            problems.append(f"group {g['name']}: members not active in the roster: {', '.join(unknown)}")
        entry = {
            "dn": _dn(f"cn={g['name']}", "ou=groups", suffix),
            "attributes": {
                "objectClass": ["posixGroup"],
                "cn": [g["name"]],
                "gidNumber": [gid],
                "memberUid": sorted(members),
            },
        }
        if not exclusive:
            # Members are only added; one taken off the list keeps the membership.
            entry["merge"] = ["memberUid"]
        entries.append(entry)

    for ng in netgroups or ():
        users = sorted(ng.get("users") or [])
        unknown = sorted(set(users) - set(active))
        if unknown:
            problems.append(f"netgroup {ng['name']}: users not active in the roster: {', '.join(unknown)}")
        entries.append(
            {
                "dn": _dn(f"cn={ng['name']}", "ou=netgroups", suffix),
                "attributes": {
                    "objectClass": ["nisNetgroup"],
                    "cn": [ng["name"]],
                    "nisNetgroupTriple": [f"(,{u},)" for u in users],
                },
            }
        )

    passwords = {}
    for name, secret in sorted((readers or {}).items()):
        if not secret:
            continue
        dn = _dn(f"cn={name}", "ou=services", suffix)
        entries.append(
            {
                "dn": dn,
                "attributes": {"objectClass": ["organizationalRole", "simpleSecurityObject"], "cn": [name]},
            }
        )
        passwords[dn] = secret

    if problems:
        raise AnsibleFilterError("the directory cannot be built:\n  " + "\n  ".join(problems))
    return {
        "entries": entries,
        "secrets": secrets,
        "passwords": passwords,
        "prune": [f"ou={ou},{suffix}" for ou in ("groups", "netgroups", "services")],
        "report": [f"ou=people,{suffix}"],
    }


def directory_local_leftovers(people, groups, local_passwd, local_group, sss_passwd, sss_group, keep=()):
    """The roster's accounts and the cluster groups a host still has in its local files
    although the directory serves them with the same number (playbooks/utilities/
    retire-local-accounts.yml): {users, groups, mismatched}. LOCAL_* are getent facts
    (service files), SSS_* the lines `getent -s sss` printed. A name with another number
    in the directory is mismatched, never retired; KEEP names stay."""

    def numbers(lines):
        return {f[0]: f[2] for f in (line.split(":") for line in lines or ()) if len(f) > 2}

    sp, sg = numbers(sss_passwd), numbers(sss_group)
    keep = set(keep or ())
    users = [n for n in sorted(people or {}) if n in local_passwd and n not in keep]
    names = sorted(set(people or {}) | {g["name"] for g in groups or ()})
    local_groups = [n for n in names if n in local_group and n not in keep]
    return {
        "users": [n for n in users if sp.get(n) == str(local_passwd[n][1])],
        "groups": [n for n in local_groups if sg.get(n) == str(local_group[n][1])],
        "mismatched": sorted(
            [n for n in users if n in sp and sp[n] != str(local_passwd[n][1])]
            + [n for n in local_groups if n in sg and sg[n] != str(local_group[n][1])]
        ),
    }


def ldap_reader_dn(name, suffix):
    """The DN a reader (sssd, dex) binds as."""
    return _dn(f"cn={name}", "ou=services", suffix)


class FilterModule:
    def filters(self):
        return {
            "directory_entries": directory_entries,
            "directory_local_leftovers": directory_local_leftovers,
            "ldap_reader_dn": ldap_reader_dn,
        }
