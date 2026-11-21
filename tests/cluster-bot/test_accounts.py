"""clusterbot.accounts: /join and /leave go through the engine to a pull request made
with the roster's own editors (plugins/filter/people.py); bad logins, keys, teams and
duplicates are refused before an administrator sees them."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
sys.path.insert(0, str(ROOT / "tests/lib"))
import in_tree  # noqa: E402
from clusterbot import accounts, requests  # noqa: E402

people = in_tree.load("plugins/filter/people.py")

FILES = {"people.yml": (ROOT / "config.example/people.yml").read_text(),
         "files/slurm/accounting.yml": (ROOT / "config.example/files/slurm/accounting.yml").read_text()}  # fmt: skip
SETTINGS = {
    "base": "main",
    "roster": "people.yml",
    "accounting": "files/slurm/accounting.yml",
    "reserved": [22078],
}


class FakeRepo:
    def __init__(self):
        self.proposed = []

    def read(self, path, ref):
        return FILES[path], "sha-" + path

    def propose(self, base, branch, files, title, body):
        self.proposed.append((base, branch, files, title, body))
        return f"https://github.example/pr/{len(self.proposed)}"


class Accounts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        key = os.path.join(tmp.name, "k")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True, timeout=60)
        self.key = Path(key + ".pub").read_text()
        self.repo = FakeRepo()
        self.engine = requests.Engine({"join": accounts.JoinFeature(self.repo, people, SETTINGS),
                                       "leave": accounts.LeaveFeature(self.repo, people, SETTINGS)},
                                      os.path.join(tmp.name, "pending.json"))  # fmt: skip

    def join(self, **kw):
        req = {
            "kind": "join",
            "user": "carl",
            "name": "Carl",
            "account": "team-a",
            "key": self.key,
            "slack": "U0CARL001",
        }
        return self.engine.submit({**req, **kw})

    def test_join_waits_for_an_admin_then_opens_a_pull_request(self):
        held = self.join()
        self.assertEqual((held.status, self.repo.proposed), ("held", []))
        done = self.engine.approve(held.key, "<@UADMIN>")
        self.assertEqual(done.status, "done", done.text)
        base, branch, files, title, body = self.repo.proposed[0]
        self.assertEqual((base, branch, title), ("main", "join/carl", "people: add carl"))
        text, sha = files["people.yml"]
        self.assertEqual(sha, "sha-people.yml")
        self.assertEqual(people.check(accounts.load(text)), [])
        self.assertIn("carl", accounts.load(text)["people"])
        self.assertIn("SHA256:", body)

    def test_join_refuses_before_an_admin_sees_it(self):
        for kw in ({"user": "Bad Name"}, {"user": "alice"}, {"account": "nosuch"}, {"key": "ssh-rsa AAAA"}):
            self.assertEqual(self.join(**kw).status, "refused", kw)

    def test_leave_opens_a_pull_request_that_keeps_the_uid(self):
        held = self.engine.submit({"kind": "leave", "user": "alice", "asked_by": "alice"})
        self.assertEqual(self.engine.approve(held.key, "<@UADMIN>").status, "done")
        text = self.repo.proposed[0][2]["people.yml"][0]
        alice = accounts.load(text)["people"]["alice"]
        self.assertEqual((alice["status"], alice["uid"]), ("left", 1001))
        self.assertEqual(self.engine.submit({"kind": "leave", "user": "nobody"}).status, "refused")


if __name__ == "__main__":
    unittest.main()
