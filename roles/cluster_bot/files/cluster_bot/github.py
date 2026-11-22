"""GitHub, as the cluster bot's GitHub App: pull requests on one repository through the
REST API, standard library only.

    app = App(app_id, installation_id, key_file)        # the App's private key (PEM)
    repo = Repo(app, "org/cluster-config")
    text, sha = repo.read("people.yml", "main")
    url = repo.propose("main", "join/alice", {"people.yml": (new_text, sha)}, title, body)

The App's JWT (RS256) is signed with the system's openssl, so no crypto library is
installed; installation tokens last an hour and are renewed before they end. Every
change is a branch and a pull request: the bot never writes the base branch.
"""

import base64
import calendar
import json
import subprocess
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
TIMEOUT = 30


class Error(RuntimeError):
    """GitHub refused or could not be reached; str() says which call and why."""


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class App:
    def __init__(self, app_id, installation_id, key_file):
        self.app_id, self.installation_id, self.key_file = str(app_id), str(installation_id), key_file
        self._token, self._expires = "", 0

    def jwt(self, now=None):
        now = int(time.time() if now is None else now)
        head = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        body = _b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": self.app_id}).encode())
        signing = f"{head}.{body}".encode()
        done = subprocess.run(["openssl", "dgst", "-sha256", "-sign", self.key_file], input=signing,
                              capture_output=True, timeout=TIMEOUT, check=False)  # fmt: skip
        if done.returncode != 0:
            raise Error(f"signing the App token failed: {done.stderr.decode(errors='replace').strip()}")
        return f"{head}.{body}.{_b64url(done.stdout)}"

    def token(self):
        if time.time() < self._expires - 300:
            return self._token
        got = call("POST", f"/app/installations/{self.installation_id}/access_tokens", bearer=self.jwt())
        self._token = got["token"]
        self._expires = calendar.timegm(time.strptime(got["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
        return self._token


def call(method, path, bearer, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {bearer}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "cluster-bot"})  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        with e:  # the error is the response: closed once read
            why = e.read().decode(errors="replace")[:300]
        raise Error(f"GitHub {method} {path}: {e.code} {why}") from None
    except OSError as e:
        raise Error(f"GitHub {method} {path}: {e}") from None
    return json.loads(body) if body else {}


class Repo:
    def __init__(self, app, name):
        self.app, self.name = app, name

    def _call(self, method, path, payload=None):
        return call(method, f"/repos/{self.name}{path}", self.app.token(), payload)

    def read(self, path, ref):
        got = self._call("GET", f"/contents/{path}?ref={ref}")
        return base64.b64decode(got["content"]).decode(), got["sha"]

    def propose(self, base, branch, files, title, body):
        """Branch BRANCH from BASE, commit FILES {path: (text, the sha it was read at)},
        and open a pull request; returns its URL."""
        head = self._call("GET", f"/git/ref/heads/{base}")["object"]["sha"]
        self._call("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": head})
        for path, (text, sha) in files.items():
            self._call("PUT", f"/contents/{path}", {"message": title, "branch": branch, "sha": sha,
                                                    "content": base64.b64encode(text.encode()).decode()})  # fmt: skip
        return self._call("POST", "/pulls", {"title": title, "head": branch, "base": base, "body": body})[
            "html_url"
        ]
