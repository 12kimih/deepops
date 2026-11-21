"""A BMC LAN channel as `ipmitool lan print` reports it, and the settings derived from it.

stdout | bmc_lan                                 -> {ipsrc, ipaddr, netmask, gateway, snmp,
                                                    auth_support: [type, ...],
                                                    auth: {level: [type, ...]} (every level),
                                                    auth_enables: ["0x04", ...] (5 bytes),
                                                    cipher_suites: [id, ...], cipher_privs}
lan | bmc_auth_enables(['md5'])                  -> the 5 bytes of LAN parameter 2 that leave
                                                    only those types at every level ([]: none,
                                                    IPMI 1.5 sessions off)
lan | bmc_cipher_privs([3, 17])                  -> the 15-character privilege list that
                                                    leaves exactly those suites open (ADMIN)

`lan print` is the only readable source of the channel's settings: every change the role
makes is computed from it here, in one place.

Authentication Type Enables (LAN parameter 2, IPMI 2.0 table 23-4) are one byte per
level (callback, user, operator, admin, OEM), one bit per type. They are written whole
(Set LAN Configuration Parameters), the OEM level with the others: `ipmitool lan set <ch>
auth` cannot name it, and compares each level and type with strcasecmp against the rest
of its comma list, so of "callback,user,operator,admin" it sets admin alone and only warns
about the others (ipmitool 1.8.19 to master).

Cipher suite privileges (LAN parameter 24) are one nibble per *entry* of the BMC's
cipher suite list (parameter 23, "RMCP+ Cipher Suites"), in that order, not per suite
ID: a BMC listing 1,2,3,6,7,8,11,12,15,16,17 prints "aaaaaaaaaaaXXXX", eleven levels
then four unused. ipmitool passes the 15 nibbles through in order either way.
"""

import re

# The privilege levels `lan set <ch> auth` takes (ipmitool lib/ipmi_lanp.c); lan print
# also shows OEM, which ipmitool cannot set.
AUTH_LEVELS = ("callback", "user", "operator", "admin")
# Bit of each type in a level's byte; "password" is the straight password/key type.
AUTH_BITS = {"none": 0, "md2": 1, "md5": 2, "password": 4, "oem": 5}
PRIVS_LENGTH = 15


def _field(text, label):
    match = re.search(rf"^{re.escape(label)}\s*:\s*(\S*)", text, re.M)
    return match.group(1) if match else ""


def _auth_byte(types):
    """A level's byte as ipmitool raw takes it ("0x04")."""
    return f"0x{sum(1 << AUTH_BITS[t] for t in types if t in AUTH_BITS):02x}"


def bmc_lan(stdout):
    text = stdout or ""
    found = re.findall(
        r"^(?:Auth Type Enable)?\s*:\s*(Callback|User|Operator|Admin|OEM)\s*:(.*)$", text, re.M
    )
    auth = dict.fromkeys(AUTH_LEVELS + ("oem",), [])
    auth.update({level.lower(): [t.lower() for t in types.split()] for level, types in found})
    suites = _field(text, "RMCP+ Cipher Suites")
    support = re.search(r"^Auth Type Support\s*:(.*)$", text, re.M)
    return {
        "ipsrc": _field(text, "IP Address Source"),
        "ipaddr": _field(text, "IP Address"),
        "netmask": _field(text, "Subnet Mask"),
        "gateway": _field(text, "Default Gateway IP"),
        "snmp": _field(text, "SNMP Community String"),
        "auth_support": [t.lower() for t in support.group(1).split()] if support else [],
        "auth": auth,
        "auth_enables": [_auth_byte(auth[level]) for level in AUTH_LEVELS + ("oem",)],
        "cipher_suites": [int(s) for s in suites.split(",") if s.strip().isdigit()],
        "cipher_privs": _field(text, "Cipher Suite Priv Max"),
    }


def bmc_auth_enables(lan, types):
    """TYPES at every privilege level, the OEM one included; [] enables none."""
    types = [str(t).lower() for t in types or []]
    unknown = sorted(set(types) - set(AUTH_BITS))
    if unknown:
        raise ValueError(f"unknown IPMI 1.5 authentication types {unknown} (known: {list(AUTH_BITS)})")
    missing = sorted(set(types) - set(lan["auth_support"]))
    if missing:
        raise ValueError(
            f"the BMC does not support IPMI 1.5 authentication types {missing} (it supports"
            f" {lan['auth_support']}): set this host's bmc_auth_types to types it supports"
        )
    return [_auth_byte(types)] * (len(AUTH_LEVELS) + 1)


def bmc_cipher_privs(lan, allowed):
    """ADMIN for each listed suite in ALLOWED, unused (X) for every other entry."""
    allowed = {int(s) for s in allowed or []}
    suites = lan["cipher_suites"][:PRIVS_LENGTH]
    missing = sorted(allowed - set(suites))
    if missing:
        raise ValueError(
            f"the BMC does not offer cipher suites {missing} (it lists {suites}): its firmware"
            " predates them; update it, or set this host's bmc_cipher_suites to suites it lists"
        )
    return "".join("a" if s in allowed else "X" for s in suites).ljust(PRIVS_LENGTH, "X")


class FilterModule:
    def filters(self):
        return {
            "bmc_lan": bmc_lan,
            "bmc_auth_enables": bmc_auth_enables,
            "bmc_cipher_privs": bmc_cipher_privs,
        }
