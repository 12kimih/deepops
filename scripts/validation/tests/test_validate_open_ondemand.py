"""Unit tests for validate_open_ondemand.py (no network, no packages): it asks as a
browser does, and refuses to run unprivileged (2026-11-23: both read as portal faults)."""

import importlib.util
import io
import os
import unittest
import urllib.error
from contextlib import redirect_stderr
from email.message import Message
from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("vood", os.path.join(SCRIPTS_DIR, "validate_open_ondemand.py"))
vood = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vood)


class Login(unittest.TestCase):
    def run_login(self, status, location):
        headers = Message()
        headers["Location"] = location
        seen = {}

        def open_(request, timeout):
            seen["accept"] = request.get_header("Accept")
            raise urllib.error.HTTPError(request.full_url, status, "", headers, io.BytesIO())

        result = {"errors": []}
        cert = {"notAfter": "Dec 31 23:59:59 2099 GMT"}
        with (
            mock.patch.object(vood.socket, "create_connection"),
            mock.patch.object(vood.ssl, "create_default_context") as ctx,
            mock.patch.object(vood.urllib.request, "build_opener") as opener,
        ):
            ctx.return_value.wrap_socket.return_value.__enter__.return_value.getpeercert.return_value = cert
            opener.return_value.open.side_effect = open_
            vood.check_tls_and_login(result, "https://portal.example:8443", "portal.example", 8443, 30)
        return result, seen

    def test_it_asks_as_a_browser_and_expects_the_login_redirect(self):
        result, seen = self.run_login(302, "https://portal.example:8443/dex/auth?client_id=x")
        self.assertIn("text/html", seen["accept"], "mod_auth_openidc answers a non-browser with 401")
        self.assertEqual(result["errors"], [])

    def test_a_401_is_a_failure(self):
        result, _ = self.run_login(401, "")
        self.assertTrue(result["errors"])


class Privilege(unittest.TestCase):
    def test_unprivileged_it_refuses_instead_of_reporting_differences(self):
        err = io.StringIO()
        with mock.patch.object(vood.os, "geteuid", return_value=1000), redirect_stderr(err):
            self.assertEqual(vood.main(["--url", "https://portal.example:8443"]), 2)
        self.assertIn("sudo", err.getvalue())


if __name__ == "__main__":
    unittest.main()
