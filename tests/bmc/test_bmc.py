"""bmc_lan and bmc_cipher_privs (plugins/filter/bmc.py) on real `ipmitool lan print` output.

The privilege list is one level per entry of the BMC's cipher suite list, in order: a
BMC listing eleven suites prints eleven levels and four unused, so closing suites by
their IDs as positions would have opened suite 0 (no authentication) and left 17 open.
"""

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("bmc", ROOT / "plugins" / "filter" / "bmc.py")
bmc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bmc)

LAN_PRINT = """\
Set in Progress         : Set Complete
Auth Type Support       : NONE MD2 MD5 PASSWORD
Auth Type Enable        : Callback : MD2 MD5 PASSWORD
                        : User     : MD2 MD5 PASSWORD
                        : Operator : MD2 MD5 PASSWORD
                        : Admin    : MD2 MD5 PASSWORD
                        : OEM      : MD2 MD5 PASSWORD
IP Address Source       : Static Address
IP Address              : 192.0.2.10
Subnet Mask             : 255.255.255.0
MAC Address             : 00:00:5e:00:53:01
SNMP Community String   : public
Default Gateway IP      : 192.0.2.1
Default Gateway MAC     : 00:00:00:00:00:00
802.1q VLAN ID          : Disabled
RMCP+ Cipher Suites     : 1,2,3,6,7,8,11,12,15,16,17
Cipher Suite Priv Max   : aaaaaaaaaaaXXXX
                        :     X=Cipher Suite Unused
                        :     a=ADMIN
Bad Password Threshold  : 3
"""


class Lan(unittest.TestCase):
    def test_fields(self):
        lan = bmc.bmc_lan(LAN_PRINT)
        self.assertEqual(lan["ipsrc"], "Static")
        self.assertEqual(lan["ipaddr"], "192.0.2.10")
        self.assertEqual(lan["netmask"], "255.255.255.0")
        self.assertEqual(lan["gateway"], "192.0.2.1")
        self.assertEqual(lan["snmp"], "public")
        self.assertEqual(lan["cipher_suites"], [1, 2, 3, 6, 7, 8, 11, 12, 15, 16, 17])
        self.assertEqual(lan["cipher_privs"], "aaaaaaaaaaaXXXX")

    def test_auth_per_level(self):
        auth = bmc.bmc_lan(LAN_PRINT)["auth"]
        self.assertEqual(set(auth), {"callback", "user", "operator", "admin", "oem"})
        self.assertEqual(auth["admin"], ["md2", "md5", "password"])

    def test_a_level_with_no_type(self):
        text = LAN_PRINT.replace(": User     : MD2 MD5 PASSWORD", ": User     : ")
        self.assertEqual(bmc.bmc_lan(text)["auth"]["user"], [])

    def test_missing_fields_are_empty(self):
        lan = bmc.bmc_lan("")
        self.assertEqual((lan["ipaddr"], lan["cipher_suites"]), ("", []))
        self.assertEqual(lan["auth"], dict.fromkeys(("callback", "user", "operator", "admin", "oem"), []))


class AuthEnables(unittest.TestCase):
    """LAN parameter 2 written whole: `lan set auth callback,user,operator,admin md5` sets
    admin alone (ipmitool compares each level with strcasecmp against the rest of the list)."""

    def test_support_and_current_bytes(self):
        lan = bmc.bmc_lan(LAN_PRINT)
        self.assertEqual(lan["auth_support"], ["none", "md2", "md5", "password"])
        # MD2, MD5 and PASSWORD: bits 1, 2 and 4
        self.assertEqual(lan["auth_enables"], ["0x16"] * 5)

    def test_md5_at_every_level_oem_included(self):
        lan = bmc.bmc_lan(LAN_PRINT)
        self.assertEqual(bmc.bmc_auth_enables(lan, ["MD5"]), ["0x04"] * 5)

    def test_empty_turns_ipmi15_off(self):
        self.assertEqual(bmc.bmc_auth_enables(bmc.bmc_lan(LAN_PRINT), []), ["0x00"] * 5)

    def test_a_bmc_without_ipmi15_is_converged_on_empty(self):
        """X14 boards ship with Auth Type Support empty and every level enabling none."""
        text = re.sub(r"(?m)^(Auth Type Support\s*:|(?:Auth Type Enable)?\s*: \w+ *:).*$", r"\1", LAN_PRINT)
        lan = bmc.bmc_lan(text)
        self.assertEqual(lan["auth_support"], [])
        self.assertEqual(bmc.bmc_auth_enables(lan, []), lan["auth_enables"])
        with self.assertRaisesRegex(ValueError, "does not support"):
            bmc.bmc_auth_enables(lan, ["md5"])

    def test_converged_reads_back_equal(self):
        text = LAN_PRINT
        for level in ("Callback", "User    ", "Operator", "Admin   ", "OEM     "):
            text = text.replace(f": {level} : MD2 MD5 PASSWORD", f": {level} : MD5")
        lan = bmc.bmc_lan(text)
        self.assertEqual(bmc.bmc_auth_enables(lan, ["md5"]), lan["auth_enables"])

    def test_an_unsupported_type_is_refused(self):
        lan = bmc.bmc_lan(LAN_PRINT.replace("NONE MD2 MD5 PASSWORD\n", "NONE PASSWORD\n", 1))
        with self.assertRaisesRegex(ValueError, "does not support .*'md5'"):
            bmc.bmc_auth_enables(lan, ["md5"])

    def test_an_unknown_type_is_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown"):
            bmc.bmc_auth_enables(bmc.bmc_lan(LAN_PRINT), ["sha1"])


class CipherPrivs(unittest.TestCase):
    def test_by_entry_not_by_id(self):
        lan = bmc.bmc_lan(LAN_PRINT)
        # entries 1,2,3,6,7,8,11,12,15,16,17: suite 3 is the third, 17 the eleventh
        self.assertEqual(bmc.bmc_cipher_privs(lan, [3, 17]), "XXaXXXXXXXaXXXX")

    def test_already_closed_is_unchanged(self):
        lan = bmc.bmc_lan(LAN_PRINT.replace("aaaaaaaaaaaXXXX", "XXaXXXXXXXaXXXX"))
        self.assertEqual(bmc.bmc_cipher_privs(lan, [3, 17]), lan["cipher_privs"])

    def test_a_suite_the_bmc_does_not_list_is_refused(self):
        with self.assertRaisesRegex(ValueError, r"\[0\]"):
            bmc.bmc_cipher_privs(bmc.bmc_lan(LAN_PRINT), [0, 3])


if __name__ == "__main__":
    unittest.main()
