"""cluster_tools.bans: the jails and their bans read from fail2ban-client as it prints
them, an unban only for a well-formed address, and the bot's key asks for nothing else."""

import contextlib
import io
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import bans  # noqa: E402

# As fail2ban 1.0 prints them (client/beautifier.py, server/banmanager.py getBanList).
STATUS = "Status\n|- Number of jail:\t2\n`- Jail list:\tsshd, recidive\n"
SSHD = ("192.0.2.7 \t2026-11-20 03:12:44 + -1 = 9999-12-31 23:59:59\n"
        "2001:db8::5 \t2026-11-21 10:00:00 + -1 = 9999-12-31 23:59:59\n")  # fmt: skip


def fake(answers):
    calls = []

    def run(*words):
        calls.append(words)
        return answers[words]

    run.calls = calls
    return run


class Bans(unittest.TestCase):
    def test_every_jails_bans_with_when_they_began(self):
        run = fake({("status",): STATUS, ("get", "sshd", "banip", "--with-time"): SSHD,
                    ("get", "recidive", "banip", "--with-time"): ""})  # fmt: skip
        self.assertEqual(
            bans.list_bans(run),
            [{"ip": "192.0.2.7", "jail": "sshd", "since": "2026-11-20 03:12:44"},
             {"ip": "2001:db8::5", "jail": "sshd", "since": "2026-11-21 10:00:00"}],
        )  # fmt: skip
        self.assertEqual(bans.jails("Status\n`- Jail list:\t\n"), [])

    def test_unban_takes_an_address_written_one_way(self):
        run = fake({("unban", "2001:db8::5"): "1\n"})
        self.assertEqual(bans.unban(" 2001:DB8::5 ", run), {"ip": "2001:db8::5", "unbanned": 1})
        for bad in ("", "192.0.2", "192.0.2.0/24", "-h", "host.example"):
            with self.subTest(address=bad), self.assertRaises(bans.Error):
                bans.unban(bad, run)

    def test_the_bot_key_may_ask_only_for_the_list_and_an_unban(self):
        self.assertEqual(bans.served("unban 192.0.2.7"), ["cluster-bans", "unban", "192.0.2.7"])
        self.assertEqual(bans.served("list"), ["cluster-bans", "list"])
        for bad in ("", "ban 192.0.2.7", "unban", "list x", "unban a b"):
            with self.subTest(request=bad), self.assertRaises(bans.Error):
                bans.served(bad)

    def test_a_refused_request_could_not_run_and_a_silent_fail2ban_is_retry_later(self):
        down = bans.proc.CalledProcessError(255, [bans.CLIENT], stderr="Failed to access socket path")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(bans.main(["cluster-bans", "unban", "192.0.2"]), 2)
            self.assertEqual(bans.main(["cluster-bans", "ban", "192.0.2.7"]), 2)
            with mock.patch.dict(bans.os.environ, {"SSH_ORIGINAL_COMMAND": "ban 192.0.2.7"}):
                self.assertEqual(bans.main(["cluster-bans", "serve"]), 2)
            with mock.patch.object(bans.proc, "run", side_effect=down):
                self.assertEqual(bans.main(["cluster-bans", "list"]), 3)
        self.assertIn("cluster-bans: fail2ban-client status: Failed to access socket path", err.getvalue())


if __name__ == "__main__":
    unittest.main()
