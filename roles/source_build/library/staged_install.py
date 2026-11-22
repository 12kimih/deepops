#!/usr/bin/python3
"""Install a staged tree (`make install DESTDIR=<stage>`) onto the host with a manifest,
as a package manager would: the files the stage holds are copied into place and listed
in the manifest; what the previous manifest listed and this one does not is removed, so
a version change leaves nothing of the old one behind; state=absent removes everything
the manifest lists. Directories this install created are listed too and go when they
are empty; directories that were there before are never removed.

A staged file never replaces one a package owns or another manifest lists: the install
stops first, naming them, as dpkg refuses to overwrite another package's file (once
listed here, this manifest's removal would take it). A file nothing owns is replaced and
listed, as the release this install supersedes left it (an earlier plain `make install`).

`make install` alone keeps no such list, so what an old release installed under a
shared prefix outlived it, and the build tree had to be kept for `make uninstall`
(2026-11-20: Slurm's contrib, Perl API and documentation under /usr/local).
"""

import glob
import json
import os
import shutil

from ansible.module_utils import deepops_diff
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.package_files import canonical, manifest_paths, owned_paths

DOCUMENTATION = r"""
module: staged_install
short_description: Copy a DESTDIR stage into place under a manifest, or remove what one lists
options:
  stage: {type: path, description: The DESTDIR tree (state=present).}
  manifest: {type: path, required: true}
  state: {type: str, choices: [present, absent], default: present}
  root: {type: path, default: /, description: Where the stage goes (the manifest lists paths below it).}
"""


def at(root, path):
    """PATH, a path the manifest lists, below ROOT."""
    return os.path.join(root, path.lstrip("/"))


def staged(stage):
    """The files (and links) and directories of a stage, as host paths."""
    files, dirs = set(), set()
    for root, subdirs, names in os.walk(stage):
        rel = os.path.relpath(root, stage)
        host = "/" if rel == "." else "/" + rel
        for d in subdirs:
            full = os.path.join(root, d)
            target = os.path.join(host, d)
            (files if os.path.islink(full) else dirs).add(target)
        for name in names:
            files.add(os.path.join(host, name))
    return files, dirs


def taken(root, files, manifest, packages=None):
    """The FILES (paths the manifest lists) that exist below ROOT and are not MANIFEST's
    but a package's (PACKAGES: host paths, canonical) or another manifest's beside it:
    overwriting one would hand it to this install."""
    mine, _ = manifest_paths(manifest)
    there = [p for p in sorted(files - mine) if os.path.lexists(at(root, p))]
    if not there:
        return []
    others = set()
    for other in glob.glob(os.path.join(os.path.dirname(manifest), "*.json")):
        if os.path.abspath(other) != os.path.abspath(manifest):
            others |= manifest_paths(other)[0]
    if packages is None:
        packages = {canonical(p) for p in owned_paths()}
    return [p for p in there if p in others or canonical(at(root, p)) in packages]


def remove_files(root, paths):
    removed = []
    for path in sorted(paths, key=len, reverse=True):
        target = at(root, path)
        if os.path.islink(target) or os.path.isfile(target):
            os.unlink(target)
            removed.append(path)
    return removed


def remove_empty_dirs(root, dirs):
    removed = []
    for path in sorted(dirs, key=len, reverse=True):
        target = at(root, path)
        if os.path.isdir(target) and not os.path.islink(target) and not os.listdir(target):
            os.rmdir(target)
            removed.append(path)
    return removed


def copy_into_place(root, stage, files, dirs):
    """Copy the stage over ROOT, creating the directories it needs; returns those it made."""
    made = set()
    for d in sorted(dirs, key=len):
        target = at(root, d)
        if not os.path.isdir(target):
            os.mkdir(target)
            shutil.copystat(stage + d, target)  # its mode; a directory already there keeps its own
            made.add(d)
    for path in sorted(files):
        source, target = stage + path, at(root, path)
        if os.path.isdir(target) and not os.path.islink(target):
            raise RuntimeError(f"{path} is a directory on the host, a file in the stage")
        tmp = f"{target}.staged-install.tmp"
        if os.path.lexists(tmp):
            os.unlink(tmp)
        if os.path.islink(source):
            os.symlink(os.readlink(source), tmp)
        else:
            shutil.copy2(source, tmp)
        os.replace(tmp, target)
    return made


def main():
    module = AnsibleModule(
        argument_spec={
            "stage": {"type": "path"},
            "manifest": {"type": "path", "required": True},
            "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
            "root": {"type": "path", "default": "/"},
        },
        required_if=[("state", "present", ["stage"])],
        supports_check_mode=True,
    )
    manifest, root = module.params["manifest"], module.params["root"]
    old_files, old_dirs = manifest_paths(manifest)
    if module.params["state"] == "absent":
        changed = bool(old_files or old_dirs or os.path.exists(manifest))
        if changed and not module.check_mode:
            removed = remove_files(root, old_files) + remove_empty_dirs(root, old_dirs)
            if os.path.exists(manifest):
                os.unlink(manifest)
        else:
            removed = sorted(old_files)
        diff = deepops_diff.actions([f"remove {path}" for path in removed] + [f"remove {manifest}"])
        module.exit_json(changed=changed, removed=removed, diff=diff if changed else {})

    stage = module.params["stage"].rstrip("/")
    new_files, new_dirs = staged(stage)
    refused = taken(root, new_files, manifest)
    if refused:
        module.fail_json(
            msg="staged_install: these files belong to a package or another install; remove "
            f"or move them first: {', '.join(refused)}"
        )
    gone_files = old_files - new_files
    diff = deepops_diff.actions(
        [f"install {len(new_files)} files from {stage} into {root}"]
        + [f"remove {path}" for path in sorted(gone_files)]
    )
    if module.check_mode:
        module.exit_json(changed=True, installed=len(new_files), removed=sorted(gone_files), diff=diff)
    try:
        made = copy_into_place(root, stage, new_files, new_dirs)
    except (OSError, RuntimeError) as err:
        module.fail_json(msg=f"staged_install: {err}")
    removed = remove_files(root, gone_files)
    # Directories the old install made and the new one does not use, once empty.
    removed += remove_empty_dirs(root, old_dirs - new_dirs)
    os.makedirs(os.path.dirname(manifest), exist_ok=True)
    tmp = manifest + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"files": sorted(new_files), "dirs": sorted((old_dirs & new_dirs) | made)}, f, indent=0)
    os.replace(tmp, manifest)
    module.exit_json(changed=True, installed=len(new_files), removed=removed, diff=diff)


if __name__ == "__main__":
    main()
