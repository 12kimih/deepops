"""cluster_tools.bot_serve: the bot's one forced command runs a service only from a file
of root's that nobody else may write, with the rest of the request as that service's
own, and every service splits its request the same way."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import bot_serve  # noqa: E402

ME = os.getuid()  # root's part in the tests: the files are the test's own


class Dispatch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "services")
        os.mkdir(self.dir, 0o755)
        self.offer("bans", ["/usr/local/sbin/cluster-bans", "serve"])

    def offer(self, name, argv, mode=0o644):
        path = os.path.join(self.dir, name + ".json")
        Path(path).write_text(json.dumps({"argv": argv}))
        os.chmod(path, mode)
        return path

    def test_the_first_word_picks_the_service_and_the_rest_is_its_request(self):
        argv, request = bot_serve.resolve(self.dir, "bans unban 203.0.113.7 'two words'", owner=ME)
        self.assertEqual(argv, ["/usr/local/sbin/cluster-bans", "serve"])
        self.assertEqual(request, "unban 203.0.113.7 'two words'", "quoting survives the hop")

    def test_what_is_not_offered_or_not_safe_is_refused(self):
        self.offer("open", ["/bin/sh"], mode=0o666)
        self.offer("relative", ["sh", "-c", "x"])
        os.symlink(os.path.join(self.dir, "bans.json"), os.path.join(self.dir, "linked.json"))
        for request in (
            "quota list",
            "../bans list",
            "",
            "Bans list",
            "open x",
            "relative",
            "linked list",
        ):
            with self.subTest(request=request), self.assertRaises(bot_serve.Error):
                bot_serve.resolve(self.dir, request, owner=ME)
        with self.assertRaises(bot_serve.Error):
            bot_serve.resolve(self.dir, "bans list", owner=ME + 1)  # not the owner's files
        os.chmod(self.dir, 0o777)
        with self.assertRaises(bot_serve.Error):
            bot_serve.resolve(self.dir, "bans list", owner=ME)

    def test_a_service_takes_only_the_operations_and_counts_it_allows(self):
        arity = {"list": (0, 1), "unban": (1,)}
        self.assertEqual(bot_serve.request("unban 192.0.2.1", arity), ["unban", "192.0.2.1"])
        self.assertEqual(bot_serve.request("list", arity), ["list"])
        for bad in ("", "unban", "unban a b", "sweep", "list a b", "rm -rf /"):
            with self.subTest(request=bad), self.assertRaises(bot_serve.Error):
                bot_serve.request(bad, arity)

    def test_the_service_runs_in_a_clean_environment_with_only_its_request(self):
        real = bot_serve.resolve
        env = {"SSH_ORIGINAL_COMMAND": "bans list alice", "LD_PRELOAD": "/tmp/x.so", "PATH": "/tmp"}
        with mock.patch.dict(os.environ, env), mock.patch("cluster_tools.proc.become") as become, \
                mock.patch.object(bot_serve, "resolve", lambda d, o: real(d, o, owner=ME)):  # fmt: skip
            bot_serve.main(["cluster-bot-serve", self.dir])
        argv, environment = become.call_args.args
        self.assertEqual(argv, ["/usr/local/sbin/cluster-bans", "serve"])
        self.assertEqual(
            environment, {"PATH": bot_serve.PATH, "LANG": "C.UTF-8", "SSH_ORIGINAL_COMMAND": "list alice"}
        )

    def test_a_bad_request_is_refused_with_its_reason_and_nothing_runs(self):
        with mock.patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "quota list"}), \
                mock.patch("cluster_tools.proc.become") as become:  # fmt: skip
            self.assertEqual(bot_serve.main(["cluster-bot-serve", self.dir]), 2)
        become.assert_not_called()


if __name__ == "__main__":
    unittest.main()
