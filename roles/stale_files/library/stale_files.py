#!/usr/bin/python3
"""Find, and remove, what retired installs left on a host, by evidence.

A `make install`, a `pip install` or a `gem install` keeps no record a package manager
can undo, so what it put under /usr/local outlives its replacement: removal by the
names of the new packages missed whatever was named differently or deliberately
skipped (2026-11-20: Slurm's contrib commands, Perl modules, documentation and an old
pyxis plugin, besides the runtimes of interpreters gone with the previous OS).

Each rule names its evidence, never a guess from a file name alone:

    glob       paths a retired install is known to have used (trees go whole)
    packlist   Perl .packlist files: every file they list, then the list itself, and
               the perllocal.pod beside them once every module it records goes
    libtool    .la archives whose shared library is gone, with the static library
               they name (the archive is broken: libtool follows it to nothing)
    links      ELF files whose dynamic section needs a library matching `needed`
    scripts    scripts (#!) under `dirs` named one of `names` whose text matches
               `contains`
    runtime    a language runtime's tree (glob, version captured by `version`)
               whose interpreter (`interpreter`, {0} the version) is gone, the
               files pip recorded installing from it elsewhere (dist-info/RECORD:
               its console scripts), and the scripts under `dirs` whose #! names
               that interpreter
    files      files under `dirs` whose name matches `pattern` (a regex on the
               whole name) and, with `contains`, whose text matches it: a file a tool is
               known to leave behind (an installer's temporary config, the copies
               Ansible's backup: true writes)
    kernel_trees  /lib/modules/<version> trees of kernels no package installs, never
               the running kernel's (an rc kernel's purge or a hand install left them)
    ld_conf    files under `dirs` (ld.so.conf.d) every directory of which is gone: a
               removed install's library path (a CUDA runfile's /usr/local/cuda-X.Y)

Nothing a package owns is touched, whatever a rule says, nor anything the manifest of
a staged install in `manifests` lists (roles/source_build: a build from source owns its
files that way). With report, files under
`report` that no package owns and no rule matched are listed too (counted per
directory), for a person to judge.

With audit, the report also lists what changes how the host behaves without a
package's or a rule's say, again only for a person to judge (nothing is removed):

    unowned_config     files in `config_dirs` that no package owns, no rule matched
                       and no component record claims; "(deepops)" marks one named
                       deepops-* or whose text names deepops or its role (roles/x)
    unit_overrides     unit files and drop-ins under /etc/systemd/system no package
                       owns (enable links and snapd's generated units left out),
                       marked "(deepops)" like unowned_config
    prompt_copies      copies conffile prompts and editors leave beside a file under
                       /etc (.dpkg-old, .dpkg-dist, .ucf-dist, .bak, ...): a -dist one
                       is the package's newer version that was never merged
    changed_conffiles  a package's configuration files changed from what it ships
    removed_packages   packages removed without purging, whose files stay (dpkg rc)
    orphan_modules     /lib/modules trees no installed package owns, other than the
                       running kernel's
"""

import glob
import json
import os
import re
import shutil
import struct
import subprocess

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.deepops_marks import named_deepops, text_marked

DOCUMENTATION = r"""
module: stale_files
short_description: Find and remove the leftovers of retired installs, by evidence
options:
  rules: {type: list, elements: dict, required: true}
  state: {type: str, choices: [absent, report], default: absent}
  report: {type: list, elements: path, default: []}
  ignore: {type: list, elements: path, default: []}
  audit: {type: bool, default: false}
  config_dirs: {type: list, elements: path, default: []}
  manifests: {type: path, description: The directory of staged_install manifests (*.json).}
"""


# --- package ownership ---------------------------------------------------------------
UCF_REGISTRY = "/var/lib/ucf/registry"


def owned_paths():
    """Every path a package owns (dpkg, else rpm). On Debian that includes the
    configuration files a package's scripts keep through ucf (/etc/ssh/sshd_config,
    /etc/default/grub): dpkg does not list them, ucf's registry does."""
    paths = set()
    info = "/var/lib/dpkg/info"
    if os.path.isdir(info):
        for name in os.listdir(info):
            if name.endswith(".list"):
                with open(os.path.join(info, name), errors="replace") as listing:
                    paths.update(line.rstrip("\n") for line in listing if line.strip())
        if os.path.isfile(UCF_REGISTRY):
            with open(UCF_REGISTRY, errors="replace") as registry:
                paths.update(line.split()[-1] for line in registry if len(line.split()) > 1)
        return paths
    try:
        out = subprocess.run(["rpm", "-qal"], capture_output=True, text=True, check=False).stdout
        paths.update(line for line in out.splitlines() if line.startswith("/"))
    except OSError:
        pass
    return paths


def staged_paths(manifests):
    """Every file and directory a staged install's manifest in MANIFESTS lists."""
    paths = set()
    if not manifests:
        return paths
    for manifest in glob.glob(os.path.join(manifests, "*.json")):
        with open(manifest) as f:
            data = json.load(f)
        paths.update(data.get("files", []), data.get("dirs", []))
    return paths


def owned_below(path, owned):
    prefix = path.rstrip("/") + "/"
    return path in owned or any(p.startswith(prefix) for p in owned)


# --- evidence -------------------------------------------------------------------------
def elf_needed(path):
    """The DT_NEEDED names of an ELF file, or None for anything else."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if head[:4] != b"\x7fELF":
                return None
            is64, little = head[4] == 2, head[5] == 1
            end = "<" if little else ">"
            if is64:
                shoff, shentsize, shnum = (
                    struct.unpack(end + "Q", head[40:48])[0],
                    *struct.unpack(end + "HH", head[58:62]),
                )
            else:
                shoff, shentsize, shnum = (
                    struct.unpack(end + "I", head[32:36])[0],
                    *struct.unpack(end + "HH", head[46:50]),
                )
            sections = []
            for i in range(shnum):
                f.seek(shoff + i * shentsize)
                raw = f.read(shentsize)
                if is64:
                    _, stype, _, _, off, size, link, _, _, entsize = struct.unpack(
                        end + "IIQQQQIIQQ", raw[:64]
                    )
                else:
                    _, stype, _, _, off, size, link, _, _, entsize = struct.unpack(
                        end + "IIIIIIIIII", raw[:40]
                    )
                sections.append((stype, off, size, link, entsize))
            names = []
            for stype, off, size, link, entsize in sections:
                if stype != 6 or not entsize:  # SHT_DYNAMIC
                    continue
                _, stroff, strsize, _, _ = sections[link]
                f.seek(stroff)
                strtab = f.read(strsize)
                f.seek(off)
                dyn = f.read(size)
                fmt = end + ("qQ" if is64 else "iI")
                for j in range(0, len(dyn) - entsize + 1, entsize):
                    tag, val = struct.unpack(fmt, dyn[j : j + struct.calcsize(fmt)])
                    if tag == 0:
                        break
                    if tag == 1:  # DT_NEEDED
                        names.append(strtab[val : strtab.index(b"\0", val)].decode(errors="replace"))
            return names
    except (OSError, struct.error, IndexError, ValueError):
        return None


def shebang(path):
    try:
        with open(path, "rb") as f:
            first = f.readline(256)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    words = first[2:].decode(errors="replace").split()
    if words and os.path.basename(words[0]) == "env" and len(words) > 1:
        return words[1]
    return words[0] if words else None


def text_matches(path, pattern):
    try:
        with open(path, errors="replace") as f:
            return re.search(pattern, f.read(1 << 20)) is not None
    except OSError:
        return False


def files_in(dirs):
    for top in dirs:
        for root, _, names in os.walk(top):
            for name in names:
                path = os.path.join(root, name)
                if os.path.isfile(path) or os.path.islink(path):
                    yield path


# The lists each rule type needs: an empty one (a name list computed from packages not
# built yet) would make the rule match nothing, silently.
REQUIRED_LISTS = {
    "glob": ("globs",),
    "packlist": ("globs",),
    "libtool": ("globs",),
    "links": ("dirs",),
    "scripts": ("dirs", "names"),
    "files": ("dirs",),
}


def match_rule(rule):
    kind = rule["type"]
    empty = [key for key in REQUIRED_LISTS.get(kind, ()) if not rule[key]]
    if empty:
        raise ValueError(f"empty {', '.join(empty)}: the rule would match nothing")
    found = []
    if kind == "glob":
        for pattern in rule["globs"]:
            found += glob.glob(pattern)
    elif kind == "packlist":
        modules = {}
        for pattern in rule["globs"]:
            for packlist in glob.glob(pattern):
                with open(packlist, errors="replace") as listing:
                    found += [
                        line.split()[0]
                        for line in listing
                        if line.strip() and os.path.lexists(line.split()[0])
                    ]
                found.append(packlist)
                # <perl dir>/auto/<Module path>/.packlist
                tree, _, module = os.path.dirname(packlist).partition("/auto/")
                modules.setdefault(tree, set()).add(module.replace("/", "::"))
        for tree, gone in modules.items():
            pod = os.path.join(tree, "perllocal.pod")
            if os.path.isfile(pod):
                with open(pod, errors="replace") as f:
                    recorded = set(re.findall(r"^=head2 .*?L<([^|>]+)\|", f.read(), re.M))
                if recorded and recorded <= gone:
                    found.append(pod)
    elif kind == "libtool":
        for pattern in rule["globs"]:
            for archive in glob.glob(pattern):
                with open(archive, errors="replace") as f:
                    fields = dict(re.findall(r"^(\w+)='([^']*)'", f.read(), re.M))
                lib = os.path.dirname(archive)
                if fields.get("dlname") and not os.path.lexists(os.path.join(lib, fields["dlname"])):
                    found.append(archive)
                    if fields.get("old_library") and os.path.lexists(
                        os.path.join(lib, fields["old_library"])
                    ):
                        found.append(os.path.join(lib, fields["old_library"]))
    elif kind == "links":
        needed = re.compile(rule["needed"])
        for path in files_in(rule["dirs"]):
            names = None if os.path.islink(path) else elf_needed(path)
            if names and any(needed.search(n) for n in names):
                found.append(path)
    elif kind == "scripts":
        names = set(rule["names"])
        for path in files_in(rule["dirs"]):
            if os.path.basename(path) in names and shebang(path) and text_matches(path, rule["contains"]):
                found.append(path)
    elif kind == "runtime":
        version = re.compile(rule["version"])
        for tree in glob.glob(rule["glob"]):
            m = version.search(tree)
            if not m:
                continue
            interpreter = rule["interpreter"].format(m.group(1))
            if os.path.exists(interpreter):
                continue
            found.append(tree)
            for record in glob.glob(os.path.join(tree, "**", "*.dist-info", "RECORD"), recursive=True):
                site = os.path.dirname(os.path.dirname(record))
                with open(record, errors="replace") as f:
                    for line in f:
                        listed = os.path.normpath(os.path.join(site, line.split(",")[0]))
                        if not listed.startswith(tree.rstrip("/") + "/") and os.path.lexists(listed):
                            found.append(listed)
            for path in files_in(rule.get("dirs", [])):
                if shebang(path) in (interpreter, os.path.basename(interpreter)):
                    found.append(path)
    elif kind == "files":
        pattern = re.compile(rule["pattern"])
        for path in files_in(rule["dirs"]):
            if pattern.fullmatch(os.path.basename(path)) and (
                "contains" not in rule or text_matches(path, rule["contains"])
            ):
                found.append(path)
    elif kind == "kernel_trees":
        running = os.uname().release
        for top in rule.get("dirs", ["/lib/modules"]):
            if os.path.isdir(top):
                found += [os.path.join(top, v) for v in os.listdir(top) if v != running]
    elif kind == "ld_conf":
        for path in files_in(rule["dirs"]):
            if not path.endswith(".conf") or os.path.islink(path):
                continue
            with open(path, errors="replace") as f:
                dirs = [ln.split("#")[0].strip() for ln in f]
            dirs = [d for d in dirs if d and not d.startswith("include")]
            if dirs and not any(os.path.isdir(d) for d in dirs):
                found.append(path)
    else:
        raise ValueError(f"unknown rule type {kind!r}")
    return sorted(set(found))


def prune_within(rule):
    """The directories a rule's removals may empty out below: a Perl tree's
    auto/<Module> directories (packlist). No other rule prunes: a directory it
    empties may be one the system made (/usr/local/src), and stays."""
    if rule["type"] != "packlist":
        return ()
    return tuple(
        sorted(
            {packlist.partition("/auto/")[0] for pattern in rule["globs"] for packlist in glob.glob(pattern)}
        )
    )


def remove(path, within=()):
    """Remove a file or a whole tree, then the directories that leaves empty, but only
    strictly below one of `within`."""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.unlink(path)
    parent = os.path.dirname(path)
    while (
        any(parent.startswith(top.rstrip("/") + "/") for top in within)
        and os.path.isdir(parent)
        and not os.listdir(parent)
    ):
        os.rmdir(parent)
        parent = os.path.dirname(parent)


# --- audit ----------------------------------------------------------------------------
# Copies a conffile prompt (dpkg, ucf), an editor or a hand edit leaves beside a file.
PROMPT_COPY = re.compile(
    r".+(\.dpkg-(old|dist|new|bak|tmp)|\.ucf-(old|dist|new)|\.update-old|\.bak|\.orig|\.save|\.old|~)"
)
# Directories whose *.old files are a package's own working copies, not a person's.
PROMPT_COPY_SKIP = ("/etc/xml/",)


def run_lines(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False).stdout.splitlines()
    except OSError:
        return []


def labelled(path):
    """path, marked "(deepops)" when its name or text says a deepops role wrote it."""
    # The one convention deepops' roles follow (plugins/module_utils/deepops_marks.py).
    ours = named_deepops(os.path.basename(path)) or text_marked(path)
    return f"{path} (deepops)" if ours else path


def unowned_config(config_dirs, owned, claimed):
    found = []
    for top in config_dirs:
        if not os.path.isdir(top):
            continue
        for name in sorted(os.listdir(top)):
            path = os.path.join(top, name)
            if path in owned or path.startswith(claimed):
                continue
            if os.path.isdir(path) and not os.path.islink(path):
                continue
            found.append(labelled(path))
    return found


def unit_overrides(owned, root="/etc/systemd/system"):
    found = []
    for top, _, names in os.walk(root):
        if top != root and not top.endswith(".d"):
            continue  # .wants/.requires hold enable links
        for name in sorted(names):
            path = os.path.join(top, name)
            if path in owned or os.path.islink(path) or text_matches(path, r"X-Snappy=yes"):
                continue
            found.append(path)
    return [labelled(p) for p in sorted(found)]


def prompt_copies(root="/etc"):
    found = []
    for top, _, names in os.walk(root):
        if (top + "/").startswith(PROMPT_COPY_SKIP):
            continue
        found += [os.path.join(top, n) for n in names if PROMPT_COPY.fullmatch(n)]
    return sorted(found)


def changed_conffiles():
    if os.path.isdir("/var/lib/dpkg/info"):
        # dpkg --verify: "??5?????? c /etc/x" for a conffile whose checksum differs.
        return sorted(
            {ln.split()[-1] for ln in run_lines(["dpkg", "--verify"]) if ln[2:3] == "5" and " c " in ln}
        )
    return sorted({ln.split()[-1] for ln in run_lines(["rpm", "-Va", "--configfiles"]) if ln[2:3] == "5"})


def removed_packages():
    lines = run_lines(["dpkg-query", "-W", "-f", "${db:Status-Abbrev} ${binary:Package}\n"])
    return sorted(ln.split()[1] for ln in lines if ln.startswith("rc"))


def orphan_modules(owned, root="/lib/modules"):
    if not os.path.isdir(root):
        return []
    keep = {os.uname().release}
    for path in owned:
        for prefix in ("/lib/modules/", "/usr/lib/modules/"):
            if path.startswith(prefix) and path.count("/") > prefix.count("/"):
                keep.add(path[len(prefix) :].split("/")[0])
    return [os.path.join(root, v) for v in sorted(os.listdir(root)) if v not in keep]


def audit(config_dirs, owned, claimed):
    return {
        "unowned_config": unowned_config(config_dirs, owned, claimed),
        "unit_overrides": unit_overrides(owned),
        "prompt_copies": prompt_copies(),
        "changed_conffiles": changed_conffiles(),
        "removed_packages": removed_packages(),
        "orphan_modules": orphan_modules(owned),
    }


def main():
    module = AnsibleModule(
        argument_spec={
            "rules": {"type": "list", "elements": "dict", "required": True},
            "state": {"type": "str", "choices": ["absent", "report"], "default": "absent"},
            "report": {"type": "list", "elements": "path", "default": []},
            "ignore": {"type": "list", "elements": "path", "default": []},
            "audit": {"type": "bool", "default": False},
            "config_dirs": {"type": "list", "elements": "path", "default": []},
            "manifests": {"type": "path"},
        },
        supports_check_mode=True,
    )
    owned = owned_paths() | staged_paths(module.params["manifests"])
    stale, kept, within = {}, [], {}
    for rule in module.params["rules"]:
        try:
            paths = match_rule(rule)
        except (KeyError, ValueError, re.error) as err:
            module.fail_json(msg=f"stale_files rule {rule.get('name', rule)}: {err}")
        mine = [p for p in paths if not owned_below(p, owned)]
        kept += [p for p in paths if p not in mine]
        if mine:
            stale[rule.get("name", rule["type"])] = mine
            for path in mine:
                within[path] = within.get(path, ()) + prune_within(rule)
    every = sorted({p for paths in stale.values() for p in paths})
    removing = module.params["state"] == "absent" and every
    if removing and not module.check_mode:
        # A tree first, so a file inside it is not removed on its own afterwards.
        for path in sorted(every, key=len):
            if os.path.lexists(path):
                remove(path, within.get(path, ()))
    claimed = tuple(p.rstrip("/") + "/" for p in every) + tuple(
        p.rstrip("/") + "/" for p in module.params["ignore"]
    )
    ignored = set(every) | {p.rstrip("/") for p in module.params["ignore"]}
    unowned = {}
    if module.params["report"]:
        for path in files_in(module.params["report"]):
            if path in owned or path in ignored or path.startswith(claimed):
                continue
            top = "/".join(path.split("/")[:5])
            unowned[top] = unowned.get(top, 0) + 1
    found = audit(module.params["config_dirs"], owned | ignored, claimed) if module.params["audit"] else {}
    module.exit_json(
        changed=bool(removing),
        stale=stale,
        owned_kept=sorted(kept),
        unowned=dict(sorted(unowned.items())),
        audit=found,
    )


if __name__ == "__main__":
    main()
