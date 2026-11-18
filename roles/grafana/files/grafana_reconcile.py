#!/usr/bin/env python3
"""Converge Grafana's dashboard state on what provisioning declares.

File provisioning leaves state behind that it never repairs:
  - a provisioned dashboard stays in its folder when the provider's folder changes
    (Grafana re-reads a file only when its checksum changes);
  - a folder keeps its title when the provider's folder title changes;
  - a dashboard whose file went away while deletion was disabled becomes an ordinary,
    unprovisioned dashboard that provisioning never removes.
This makes the managed folder exist under its fixed UID with the configured title,
deletes retired dashboards (by UID, only unprovisioned ones), re-homes provisioned
dashboards that sit in another folder (their file is moved aside, provisioning reloaded,
the file restored and provisioning reloaded again: the supported way, as Grafana refuses
to delete or move a provisioned dashboard), and removes the folders those left empty and empty duplicates of the managed folder.

Run as root on the Grafana host (it moves provisioned files). Prints JSON
{"changed": bool, "actions": [...]}; --check only reports. Standard library only.
"""

import argparse
import base64
import configparser
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class Grafana:
    def __init__(self, url, user, password):
        self.url = url.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def call(self, method, path, body=None, ok=(200,)):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", self.auth)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        if status not in ok:
            raise RuntimeError(f"{method} {path}: HTTP {status}: {raw[:200].decode(errors='replace')}")
        return status, (json.loads(raw) if raw.strip() else None)

    def get(self, path, ok=(200,)):
        return self.call("GET", path, ok=ok)

    def reload(self):
        self.call("POST", "/api/admin/provisioning/dashboards/reload")

    def dashboards(self):
        _, found = self.get("/api/search?type=dash-db&limit=5000")
        return found

    def meta(self, uid):
        status, body = self.get(f"/api/dashboards/uid/{urllib.parse.quote(uid)}", ok=(200, 404))
        return body["meta"] if status == 200 else None


def reconcile(api, folder_uid, folder_title, dashboards_path, retired, check):
    actions = []

    def act(text, fn=None):
        actions.append(text)
        if fn and not check:
            fn()

    status, folder = api.get(f"/api/folders/{urllib.parse.quote(folder_uid)}", ok=(200, 404))
    if status == 404:
        act(
            f"create folder {folder_title!r} ({folder_uid})",
            lambda: api.call("POST", "/api/folders", {"uid": folder_uid, "title": folder_title}),
        )
    elif folder["title"] != folder_title:
        act(
            f"rename folder {folder['title']!r} to {folder_title!r} ({folder_uid})",
            lambda: api.call("PUT", f"/api/folders/{folder_uid}", {"title": folder_title, "overwrite": True}),
        )

    former = set()
    for uid in retired:
        meta = api.meta(uid)
        if meta is None:
            continue
        if meta.get("provisioned"):
            actions.append(
                f"keep retired dashboard {uid}: still provisioned by {meta.get('provisionedExternalId')}"
            )
            continue
        former.add(meta.get("folderUid") or "")
        act(
            f"delete retired dashboard {uid} ({meta.get('folderTitle')})",
            lambda uid=uid: api.call("DELETE", f"/api/dashboards/uid/{uid}"),
        )

    moves = []
    for dash in api.dashboards():
        meta = api.meta(dash["uid"])
        if not meta or not meta.get("provisioned") or meta.get("folderUid") == folder_uid:
            continue
        path = os.path.join(dashboards_path, meta["provisionedExternalId"])
        if not os.path.isfile(path):
            raise RuntimeError(f"{dash['uid']}: provisioned from {path}, which does not exist")
        former.add(meta.get("folderUid") or "")
        moves.append(path)
        actions.append(f"move dashboard {dash['uid']} from {meta.get('folderTitle')!r} to {folder_title!r}")
    if moves and not check:
        aside = [(p, p + ".reconcile") for p in moves]
        for src, dst in aside:
            os.rename(src, dst)
        try:
            api.reload()  # Grafana deletes the dashboards whose files are gone
        finally:
            for src, dst in aside:
                os.rename(dst, src)
        api.reload()  # and provisions them again, in the managed folder

    # Folders under the managed title but another UID are earlier homes too: the
    # provider created them before the folder had a fixed UID.
    _, same = api.get(f"/api/search?type=dash-folder&query={urllib.parse.quote(folder_title)}&limit=5000")
    former.update(f["uid"] for f in same if f.get("title") == folder_title)
    former.discard("")  # the root ("General") is not a folder
    former.discard(folder_uid)
    for uid in sorted(former):
        status, folder = api.get(f"/api/folders/{urllib.parse.quote(uid)}", ok=(200, 404))
        if status == 404:
            continue
        _, left = api.get(f"/api/search?folderUIDs={urllib.parse.quote(uid)}&limit=1")
        if check:
            actions.append(f"delete folder {folder['title']!r} ({uid}) once emptied")
        elif not left:
            act(
                f"delete emptied folder {folder['title']!r} ({uid})",
                lambda uid=uid: api.call("DELETE", f"/api/folders/{uid}"),
            )
    return actions


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--url", required=True)
    p.add_argument("--folder-uid", required=True)
    p.add_argument("--folder-title", required=True)
    p.add_argument("--dashboards-path", required=True)
    p.add_argument("--retire", nargs="*", default=[], metavar="UID")
    p.add_argument("--ini", help="grafana.ini holding the admin user and password")
    p.add_argument("--password-stdin", action="store_true", help="read the admin password from stdin")
    p.add_argument("--user", default="admin")
    p.add_argument("--check", action="store_true")
    args = p.parse_args(argv)

    user = args.user
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    elif args.ini:
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(args.ini)
        user = ini.get("security", "admin_user", fallback=user)
        password = ini.get("security", "admin_password")
    else:
        p.error("one of --ini or --password-stdin is required")

    try:
        actions = reconcile(
            Grafana(args.url, user, password),
            args.folder_uid,
            args.folder_title,
            args.dashboards_path,
            args.retire,
            args.check,
        )
    except (RuntimeError, OSError, urllib.error.URLError) as e:
        print(f"grafana_reconcile: {e}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"changed": bool(actions) and not all(a.startswith("keep ") for a in actions), "actions": actions}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
