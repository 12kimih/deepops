"""cluster_bot.sshkeys accepts ed25519 and strong RSA keys made by ssh-keygen, with
OpenSSH's own fingerprint, and refuses weak RSA and a pasted private key.
cluster_bot.github signs the App's JWT with openssl (verified with the public key) and
proposes a change as branch, commit and pull request, never on the base branch."""

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_bot/files"))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files")
)  # cluster_tools, which the bot imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/lib"))
import bounds  # noqa: E402
from cluster_bot import github, sshkeys  # noqa: E402


def keygen(tmp, kind, bits=None):
    path = os.path.join(tmp, f"{kind}{bits or ''}")
    argv = ["ssh-keygen", "-q", "-t", kind, "-N", "", "-C", "me@lab", "-f", path] + (
        ["-b", str(bits)] if bits else []
    )
    subprocess.run(argv, check=True, timeout=bounds.CHILD)
    return path


class Keys(unittest.TestCase):
    def test_keys_ssh_keygen_makes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = keygen(tmp, "ed25519")
            key = sshkeys.parse(Path(ed + ".pub").read_text())
            printed = subprocess.run(["ssh-keygen", "-l", "-f", ed + ".pub"], capture_output=True, text=True,
                                     check=True).stdout.split()[1]  # fmt: skip
            self.assertEqual((key.kind, key.fingerprint, key.comment), ("ssh-ed25519", printed, "me@lab"))
            self.assertEqual(sshkeys.parse(Path(keygen(tmp, "rsa", 3072) + ".pub").read_text()).bits, 3072)
            with self.assertRaisesRegex(sshkeys.Invalid, "2048"):
                sshkeys.parse(Path(keygen(tmp, "rsa", 2048) + ".pub").read_text())
            with self.assertRaisesRegex(sshkeys.Invalid, "개인키"):
                sshkeys.parse(Path(ed).read_text())

    def test_garbage_is_refused(self):
        for line in (
            "",
            "ssh-dss AAAA",
            "ssh-ed25519 not-base64!!",
            "ssh-ed25519 " + base64.b64encode(b"x").decode(),
        ):
            with self.assertRaises(sshkeys.Invalid, msg=line):
                sshkeys.parse(line)


class GitHub(unittest.TestCase):
    def test_the_jwt_verifies_with_the_apps_public_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, pub = os.path.join(tmp, "app.pem"), os.path.join(tmp, "app.pub")
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-out", key, "-pkeyopt", "rsa_keygen_bits:2048"],
                           check=True, capture_output=True, timeout=bounds.CHILD)  # fmt: skip
            subprocess.run(
                ["openssl", "pkey", "-in", key, "-pubout", "-out", pub], check=True, timeout=bounds.CHILD
            )
            token = github.App(123, 456, key).jwt(now=1_000_000)
            head, body, sig = token.split(".")
            pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
            self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(body)))["iss"], "123")
            Path(tmp, "sig").write_bytes(base64.urlsafe_b64decode(pad(sig)))
            Path(tmp, "data").write_text(f"{head}.{body}")
            ok = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", os.path.join(tmp, "sig"),
                                 os.path.join(tmp, "data")], capture_output=True, text=True, timeout=bounds.CHILD)  # fmt: skip
            self.assertEqual(ok.stdout.strip(), "Verified OK")

    def test_a_change_is_a_branch_and_a_pull_request(self):
        app = mock.Mock(token=mock.Mock(return_value="t"))
        calls = []

        def fake(method, path, bearer, payload=None):
            calls.append((method, path, payload and {k: v for k, v in payload.items() if k != "content"}))
            return {"object": {"sha": "base"}} if path.endswith("/git/ref/heads/main") else {"html_url": "U"}

        with mock.patch.object(github, "call", fake):
            url = github.Repo(app, "org/cfg").propose(
                "main", "join/x", {"people.yml": ("text", "s1")}, "T", "B"
            )
        self.assertEqual(url, "U")
        self.assertEqual([c[:2] for c in calls], [("GET", "/repos/org/cfg/git/ref/heads/main"),
                         ("POST", "/repos/org/cfg/git/refs"), ("PUT", "/repos/org/cfg/contents/people.yml"),
                         ("POST", "/repos/org/cfg/pulls")])  # fmt: skip
        self.assertEqual(calls[2][2]["branch"], "join/x")
        self.assertEqual(calls[3][2], {"title": "T", "head": "join/x", "base": "main", "body": "B"})

    def test_an_answer_that_is_not_json_is_a_github_error(self):
        page = mock.MagicMock()
        page.__enter__.return_value.read.return_value = b"<html>bad gateway</html>"
        with mock.patch("urllib.request.urlopen", return_value=page), self.assertRaises(github.Error) as e:
            github.call("GET", "/repos/o/r", "t")
        self.assertIn("not JSON", str(e.exception))


if __name__ == "__main__":
    unittest.main()
