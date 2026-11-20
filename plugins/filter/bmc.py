"""A BMC LAN channel as `ipmitool lan print` reports it, and the settings derived from it.

stdout | bmc_lan                                 -> {ipsrc, ipaddr, netmask, gateway, snmp,
                                                    auth: {level: [type, ...]} (every level),
                                                    cipher_suites: [id, ...], cipher_privs}
lan | bmc_cipher_privs([3, 17])                  -> the 15-character privilege list that
                                                    leaves exactly those suites open (ADMIN)

`lan print` is the only readable source of the channel's settings: every change the role
makes is computed from it here, in one place.

Cipher suite privileges (LAN parameter 24) are one nibble per *entry* of the BMC's
cipher suite list (parameter 23, "RMCP+ Cipher Suites"), in that order, not per suite
ID: a BMC listing 1,2,3,6,7,8,11,12,15,16,17 prints "aaaaaaaaaaaXXXX", eleven levels
then four unused. ipmitool passes the 15 nibbles through in order either way.
"""

import re

# The privilege levels `lan set <ch> auth` takes (ipmitool lib/ipmi_lanp.c); lan print
# also shows OEM, which ipmitool cannot set.
AUTH_LEVELS = ("callback", "user", "operator", "admin")
AUTH_TYPES = ("none", "md2", "md5", "password", "oem")
PRIVS_LENGTH = 15


def _field(text, label):
    match = re.search(rf"^{re.escape(label)}\s*:\s*(\S*)", text, re.M)
    return match.group(1) if match else ""


def bmc_lan(stdout):
    text = stdout or ""
    found = re.findall(
        r"^(?:Auth Type Enable)?\s*:\s*(Callback|User|Operator|Admin|OEM)\s*:(.*)$", text, re.M
    )
    auth = dict.fromkeys(AUTH_LEVELS + ("oem",), [])
    auth.update({level.lower(): [t.lower() for t in types.split()] for level, types in found})
    suites = _field(text, "RMCP+ Cipher Suites")
    return {
        "ipsrc": _field(text, "IP Address Source"),
        "ipaddr": _field(text, "IP Address"),
        "netmask": _field(text, "Subnet Mask"),
        "gateway": _field(text, "Default Gateway IP"),
        "snmp": _field(text, "SNMP Community String"),
        "auth": auth,
        "cipher_suites": [int(s) for s in suites.split(",") if s.strip().isdigit()],
        "cipher_privs": _field(text, "Cipher Suite Priv Max"),
    }


def bmc_cipher_privs(lan, allowed):
    """ADMIN for each listed suite in ALLOWED, unused (X) for every other entry."""
    allowed = {int(s) for s in allowed or []}
    suites = lan["cipher_suites"][:PRIVS_LENGTH]
    missing = sorted(allowed - set(suites))
    if missing:
        raise ValueError(f"the BMC does not offer cipher suites {missing} (it lists {suites})")
    return "".join("a" if s in allowed else "X" for s in suites).ljust(PRIVS_LENGTH, "X")


class FilterModule:
    def filters(self):
        return {"bmc_lan": bmc_lan, "bmc_cipher_privs": bmc_cipher_privs}
