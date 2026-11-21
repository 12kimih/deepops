"""roles/nsswitch: one client's source token, added and taken away, nothing else touched."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

nss = in_tree.load("roles/nsswitch/library/nsswitch_source.py")

# Ubuntu 24.04's shipped file (/usr/share/libc-bin/nsswitch.conf), as installed.
UBUNTU = """\
# /etc/nsswitch.conf
#
# Example configuration of GNU Name Service Switch functionality.

passwd:         files systemd
group:          files systemd
shadow:         files systemd
gshadow:        files systemd

hosts:          files mdns4_minimal [NOTFOUND=return] dns
networks:       files

protocols:      db files
services:       db files
ethers:         db files
rpc:            db files

netgroup:       nis
"""

NIS = dict(source="nis", databases=["passwd", "group", "shadow", "netgroup", "automount"], replaces=["sss"])
SSS = dict(source="sss", databases=["passwd", "group", "netgroup"], replaces=["nis"])


def lines(text):
    return {line.split(":")[0]: line.split(":", 1)[1].split() for line in text.splitlines() if ":" in line}


class Source(unittest.TestCase):
    def test_nis_then_sss_then_nis_removed_leaves_sss(self):
        nis = nss.apply(UBUNTU, **NIS)
        self.assertEqual(lines(nis)["passwd"], ["files", "systemd", "nis"])
        self.assertEqual(lines(nis)["netgroup"], ["nis"], "an existing token is not added twice")
        self.assertEqual(lines(nis)["automount"], ["files", "nis"], "a missing line is added")
        sss = nss.apply(nis, **SSS)
        self.assertEqual(lines(sss)["passwd"], ["files", "systemd", "sss"])
        self.assertEqual(lines(sss)["shadow"], ["files", "systemd"], "sss serves no shadow")
        self.assertEqual(lines(sss)["netgroup"], ["sss"])
        self.assertEqual(lines(sss)["automount"], ["files"])
        self.assertEqual(nss.apply(sss, "nis", state="absent"), sss, "NIS removed later finds nothing")

    def test_converged_is_unchanged_and_only_owned_lines_move(self):
        once = nss.apply(UBUNTU, **SSS)
        self.assertEqual(nss.apply(once, **SSS), once)
        changed = [a for a, b in zip(UBUNTU.splitlines(), once.splitlines()) if a != b]
        self.assertEqual(len(changed), 3, "passwd, group, netgroup only")
        self.assertIn("passwd:         files systemd sss", once, "the spacing stays")

    def test_absent_drops_the_token_and_its_criteria(self):
        text = "passwd: files sss [NOTFOUND=return] systemd\nnetgroup: sss\n"
        self.assertEqual(nss.apply(text, "sss", state="absent"), "passwd: files systemd\nnetgroup: files\n")

    def test_a_token_in_place_stays_in_place(self):
        text = "passwd: files sss systemd\n"
        self.assertEqual(nss.apply(text, "sss", ["passwd"]), text)

    def test_present_takes_the_source_off_databases_not_listed(self):
        text = "passwd: files sss\nshadow: files sss\n"
        self.assertEqual(nss.apply(text, "sss", ["passwd"]), "passwd: files sss\nshadow: files\n")


if __name__ == "__main__":
    unittest.main()
