"""plugins/filter/directory.py (the tree a roster declares) and ldap_tree's comparison."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


directory = load("directory", "plugins/filter/directory.py")
# ldap_tree imports python-ldap only to talk to a server; changes() needs none of it.
sys.modules.setdefault("ldap", types.ModuleType("ldap"))
tree = in_tree.load("roles/ldap_server/library/ldap_tree.py")

SUFFIX = "dc=example,dc=internal"
PEOPLE = {
    "alice": {"uid": 1001, "name": "Alice Example", "roles": ["admin"]},
    "bob": {"uid": 1002, "shell": "/bin/zsh", "name": "Bob Kim"},
    "carol": {"uid": 1003, "status": "left"},
}
SHADOW = {"alice": ["$y$j9T$salt$hash", "19000"], "bob": ["!$6$locked", "19000"]}


def build(**kw):
    return directory.directory_entries(PEOPLE, SUFFIX, **kw)


def entry(result, dn):
    return next(e for e in result["entries"] if e["dn"] == dn)


class Tree(unittest.TestCase):
    def test_people_and_their_groups(self):
        got = build()
        alice = entry(got, f"uid=alice,ou=people,{SUFFIX}")["attributes"]
        self.assertEqual(alice["uidNumber"], ["1001"])
        self.assertEqual(alice["gidNumber"], ["1001"])
        self.assertEqual(alice["loginShell"], ["/bin/bash"])
        self.assertNotIn("userPassword", alice, "without a shadow source the directory keeps passwords")
        self.assertEqual(entry(got, f"cn=bob,ou=groups,{SUFFIX}")["attributes"]["gidNumber"], ["1002"])

    def test_someone_who_left_is_expired_not_removed(self):
        carol = entry(build(), f"uid=carol,ou=people,{SUFFIX}")["attributes"]
        self.assertEqual((carol["shadowExpire"], carol["userPassword"]), (["1"], []))
        self.assertEqual(carol["loginShell"], ["/usr/sbin/nologin"])
        self.assertIn(f"ou=people,{SUFFIX}", build()["report"])
        self.assertNotIn(f"ou=people,{SUFFIX}", build()["prune"], "people are never pruned")

    def test_shadow_hashes_are_secrets_and_locked_ones_none(self):
        got = build(shadow=SHADOW)
        self.assertEqual(
            got["secrets"], {f"uid=alice,ou=people,{SUFFIX}": {"userPassword": ["{CRYPT}$y$j9T$salt$hash"]}}
        )
        self.assertEqual(entry(got, f"uid=bob,ou=people,{SUFFIX}")["attributes"]["userPassword"], [])
        self.assertNotIn("$y$", str(got["entries"]), "no hash among the logged entries")

    def test_someone_the_source_no_longer_holds_keeps_their_password(self):
        got = build(shadow={"alice": SHADOW["alice"]})
        self.assertNotIn("userPassword", entry(got, f"uid=bob,ou=people,{SUFFIX}")["attributes"])

    def test_gecos_only_when_ascii(self):
        # A name in Hangul: IA5 cannot hold it.
        got = directory.directory_entries({"dan": {"uid": 1004, "name": "\uae40\ub2e8"}}, SUFFIX)
        self.assertEqual(entry(got, f"uid=dan,ou=people,{SUFFIX}")["attributes"]["gecos"], [])

    def test_groups_netgroups_and_readers(self):
        got = build(
            groups=[{"name": "dockerusers", "gid": 4000, "members": ["alice"]}],
            netgroups=[{"name": "admins", "users": ["alice"]}],
            readers={"sssd": "secret", "dex": ""},
            exclusive=False,
        )
        group = entry(got, f"cn=dockerusers,ou=groups,{SUFFIX}")
        self.assertEqual((group["attributes"]["memberUid"], group["merge"]), (["alice"], ["memberUid"]))
        net = entry(got, f"cn=admins,ou=netgroups,{SUFFIX}")["attributes"]
        self.assertEqual(net["nisNetgroupTriple"], ["(,alice,)"])
        self.assertEqual(
            got["passwords"], {f"cn=sssd,ou=services,{SUFFIX}": "secret"}, "an empty password: no account"
        )

    def test_conflicts_stop_the_build(self):
        bad = [
            {"name": "alice", "gid": 4000, "members": []},
            {"name": "x", "gid": 1002, "members": []},
            {"name": "y", "gid": 27, "members": []},
            {"name": "z", "gid": 4001, "members": ["carol"]},
        ]
        with self.assertRaises(Exception) as caught:
            build(groups=bad, netgroups=[{"name": "n", "users": ["nobody"]}])
        text = str(caught.exception)
        for part in ("a person's login", "bob's per-user group", "below 1000", "carol", "nobody"):
            self.assertIn(part, text)

    def test_reader_dn(self):
        self.assertEqual(directory.ldap_reader_dn("sssd", SUFFIX), f"cn=sssd,ou=services,{SUFFIX}")


class Changes(unittest.TestCase):
    def test_exact_values_and_removal(self):
        current = {"loginshell": ["/bin/bash"], "shadowexpire": ["1"], "cn": ["a"]}
        got = tree.changes(
            current, {"loginShell": ["/bin/zsh"], "shadowExpire": [], "cn": ["a"], "gecos": []}
        )
        self.assertEqual(got, [("loginShell", ["/bin/zsh"]), ("shadowExpire", [])])

    def test_merge_only_adds_and_objectclass_folds_case(self):
        current = {"memberuid": ["bob"], "objectclass": ["posixgroup"]}
        got = tree.changes(
            current, {"memberUid": ["alice"], "objectClass": ["posixGroup"]}, merge=["memberUid"]
        )
        self.assertEqual(got, [("memberUid", ["bob", "alice"])])
        self.assertEqual(
            tree.changes({"memberuid": ["bob", "alice"]}, {"memberUid": ["alice"]}, ["memberUid"]), []
        )


if __name__ == "__main__":
    unittest.main()
