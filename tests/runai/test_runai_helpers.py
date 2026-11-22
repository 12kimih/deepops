import http.client
import importlib.util
import os
import ssl
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


filters = load("roles/runai/filter_plugins/runai.py", "runai_filters")
api = load("roles/runai/files/runai_api.py", "runai_api")


class TestFilters(unittest.TestCase):
    def test_deep_merge_replaces_scalars_and_merges_dicts(self):
        base = {"a": {"b": 1, "c": 2}, "d": [1]}
        self.assertEqual(
            filters.runai_deep_merge(base, {"a": {"b": 9}, "d": [2]}), {"a": {"b": 9, "c": 2}, "d": [2]}
        )
        self.assertEqual(base["a"]["b"], 1)  # inputs untouched

    def test_password_policy(self):
        ok = filters.runai_password_ok
        self.assertTrue(ok("Passw0rd!x"))
        for bad in ("shrt1A!", "nouppercase1!", "NOLOWERCASE1!", "NoDigits!!", "NoSpecial11A", "", None, 5):
            self.assertFalse(ok(bad), bad)


class TestKubeParsers(unittest.TestCase):
    NODES = {
        "items": [
            {
                "metadata": {
                    "labels": {
                        "nvidia.com/gpu.present": "true",
                        "nvidia.com/cuda.driver-version.major": "615",
                    }
                },
                "status": {"allocatable": {"nvidia.com/gpu": "2"}},
            },
            {"metadata": {"labels": {}}, "status": {"allocatable": {}}},
            {
                "metadata": {"labels": {"nvidia.com/gpu.present": "true"}},
                "status": {"allocatable": {"nvidia.com/gpu": "x"}},
            },
        ]
    }

    def test_gpu_count_ignores_bad_values(self):
        self.assertEqual(filters.runai_gpu_count(self.NODES), 2)
        self.assertEqual(filters.runai_gpu_count({"items": []}), 0)

    def test_driver_majors_only_gpu_nodes(self):
        self.assertEqual(filters.runai_driver_majors(self.NODES), [615, 0])

    def test_default_storageclass(self):
        doc = {
            "items": [
                {
                    "metadata": {
                        "name": "a",
                        "annotations": {"storageclass.kubernetes.io/is-default-class": "true"},
                    }
                },
                {"metadata": {"name": "b"}},
            ]
        }
        self.assertEqual(filters.runai_default_storageclasses(doc), ["a"])
        self.assertEqual(filters.runai_default_storageclasses(json_text(doc)), ["a"])


def json_text(doc):
    import json

    return json.dumps(doc)


class TestInstallString(unittest.TestCase):
    TEXT = (
        "helm upgrade -i runai-cluster runai/runai-cluster -n runai \\\n"
        "  --set controlPlane.url=runai.lab.test \\\n"
        "  --set controlPlane.bootstrapToken=abc.def \\\n"
        "  --set cluster.uid=1234-5678 \\\n"
        '  --set cluster.url="https://runai.lab.test" --version="3.0.20" --create-namespace'
    )

    def test_parse_and_nest(self):
        pairs = api.parse_set_args(self.TEXT)
        self.assertEqual(pairs["controlPlane.url"], "runai.lab.test")
        self.assertEqual(pairs["cluster.url"], "https://runai.lab.test")
        self.assertEqual(api.nest(pairs)["cluster"]["uid"], "1234-5678")

    def test_indexed_keys(self):
        self.assertEqual(
            api.nest({"global.imagePullSecrets[0].name": "x"}),
            {"global": {"imagePullSecrets": [{"name": "x"}]}},
        )

    def test_bad_key_rejected(self):
        with self.assertRaises(api.ApiError):
            api.nest({"a b": "x"})

    def test_conflicting_keys_rejected(self):
        for pairs in ({"a": "1", "a.b": "2"}, {"a.b": "1", "a[0]": "2"}, {"a[0]": "1", "a[0].b": "2"}):
            with self.subTest(pairs=pairs), self.assertRaisesRegex(api.ApiError, "conflicting"):
                api.nest(pairs)


class FakeConnection:
    """_Connection that answers STATUS with BODY, or raises ERROR; records close()."""

    def __init__(self, status=200, body=b"{}", error=None):
        self.status, self.body, self.error, self.closed = status, body, error, False

    def __call__(self, *args, **kwargs):
        return self

    def request(self, *args, **kwargs):
        if self.error:
            raise self.error

    def getresponse(self):
        return self

    def read(self, limit):
        return self.body

    def close(self):
        self.closed = True


class TestClient(unittest.TestCase):
    def call(self, conn, **kwargs):
        client = api.Client("runai.example.test", "", "")
        with mock.patch.object(api, "_Connection", conn):
            return client.request("GET", "/api/v1/clusters", **kwargs)

    def test_every_connection_is_closed(self):
        for conn, error in (
            (FakeConnection(error=ConnectionRefusedError()), "failed: ConnectionRefusedError"),
            (FakeConnection(error=http.client.RemoteDisconnected()), "failed: RemoteDisconnected"),
            (FakeConnection(status=503), "returned HTTP 503"),
            (FakeConnection(body=b"<html>"), "invalid JSON"),
            (FakeConnection(body=b"[]"), "unexpected answer"),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(api.ApiError, error):
                    self.call(conn)
                self.assertTrue(conn.closed)
        conn = FakeConnection(body=b"[]")
        self.assertEqual(self.call(conn, expect=(dict, list)), [])
        self.assertTrue(conn.closed)

    def test_a_failed_handshake_closes_its_socket(self):
        sock, tls = mock.Mock(), mock.Mock()
        tls.wrap_socket.side_effect = ssl.SSLError("handshake")
        conn = api._Connection("runai.example.test", "192.0.2.1", tls, 5)
        with mock.patch("socket.create_connection", return_value=sock) as connect:
            with self.assertRaises(ssl.SSLError):
                conn.connect()
        connect.assert_called_once_with(("192.0.2.1", 443), 5)
        tls.wrap_socket.assert_called_once_with(sock, server_hostname="runai.example.test")
        sock.close.assert_called_once_with()

    def test_a_refused_login_is_not_waited_on(self):
        client = mock.Mock()
        client.login.side_effect = api.ApiError("POST /api/v2/token returned HTTP 401", 401)
        with mock.patch("time.sleep") as sleep, self.assertRaisesRegex(api.ApiError, "refused the login"):
            api.wait_for_api(client, "u", "p", 600)
        sleep.assert_not_called()

    def test_an_unready_control_plane_is_waited_on(self):
        client = mock.Mock()
        client.login.side_effect = [api.ApiError("POST /api/v2/token returned HTTP 503", 503), None]
        with mock.patch("time.sleep") as sleep:
            api.wait_for_api(client, "u", "p", 600)
        sleep.assert_called_once_with(10)


if __name__ == "__main__":
    unittest.main()
