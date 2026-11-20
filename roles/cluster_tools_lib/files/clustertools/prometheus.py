"""prometheus: the one client for Prometheus' HTTP API.

    api = Prometheus("http://metrics:9090")
    api.query('up{job="node"}')            -> the result list of an instant query
    api.query(expr, at=1795200000)         -> the same, evaluated at that Unix time
    api.get("/api/v1/targets", state="active") -> any other read, parsed

Every failure (unreachable, timeout, an answer that is not a successful JSON reply) is
an Error with one line on why; Error is a RuntimeError, so callers that already catch
RuntimeError or OSError keep working. Each call is bounded by TIMEOUT seconds. The
text format node_exporter's textfile collector reads is clustertools.prom. Python 3.9
or newer, standard library only.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 30


class Error(RuntimeError):
    """Prometheus gave no usable answer; str() says why in one line."""


class Prometheus:
    def __init__(self, url, timeout=TIMEOUT):
        self.url, self.timeout = url.rstrip("/"), timeout

    def get(self, path, **params):
        """The reply to GET PATH?PARAMS: parsed JSON when it is JSON, else the text
        (/-/ready answers in plain text)."""
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        try:
            with urllib.request.urlopen(self.url + path + query, timeout=self.timeout) as resp:  # noqa: S310
                body = resp.read()
        except urllib.error.HTTPError as e:  # a refused query says why in its body
            try:
                why = json.loads(e.read()).get("error") or e.reason
            except (ValueError, AttributeError, OSError):
                why = e.reason
            raise Error(f"{self.url}{path}: HTTP {e.code}: {why}") from None
        except (urllib.error.URLError, OSError) as e:
            raise Error(f"{self.url}: {getattr(e, 'reason', None) or e}") from None
        if not body.strip().startswith(b"{"):
            return body.decode(errors="replace")
        try:
            return json.loads(body)
        except ValueError as e:
            raise Error(f"{self.url}{path}: unreadable JSON: {e}") from None

    def query(self, expr, at=None):
        """The result list of the instant query EXPR, now or AT (Unix time)."""
        params = {"query": expr} if at is None else {"query": expr, "time": at}
        reply = self.get("/api/v1/query", **params)
        if not isinstance(reply, dict) or reply.get("status") != "success":
            why = reply.get("error") if isinstance(reply, dict) else reply[:200]
            raise Error(f"Prometheus query failed: {expr}: {why}")
        return reply["data"]["result"]
