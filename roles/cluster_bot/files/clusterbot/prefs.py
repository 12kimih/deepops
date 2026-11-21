"""Each person's choices about what the cluster tells them, kept in one small JSON file
the bot writes and the tools that notify read (clustertools.job_notice: `muted`).

    prefs = Prefs(path)
    prefs.muted("alice") -> bool      prefs.set_muted("alice", True)

The file is world-readable (it holds logins, nothing secret) so a tool running as
another user (job notices run as SlurmUser) reads it; it is replaced atomically.
"""

import json
import os


class Prefs:
    def __init__(self, path):
        self.path = path

    def _read(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {"muted": []}
        return {"muted": sorted(set(data.get("muted") or []))}

    def muted(self, login):
        return login in self._read()["muted"]

    def set_muted(self, login, value):
        data = self._read()
        muted = set(data["muted"])
        (muted.add if value else muted.discard)(login)
        data["muted"] = sorted(muted)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.path)
