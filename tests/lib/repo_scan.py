"""One way for the repository's guard tests to see what they check.

Every guard test (task rules, managed directories, root-run paths, shared defaults,
oneshot units, daemon.json) walks the same Ansible files. Each walking them its own way
let defects through: a test that globbed *.yml never saw a .yaml file, none read
handlers, a free-form `copy: src=x dest=y` or an `args:` block hid a task's arguments,
and an empty tree passed every test. Here is the one walker they all use:

  tracked(*pathspecs)  tracked files present in the tree (scripts/deepops/repo_files.py)
  ansible_files()      tracked task, handler and playbook files (.yml and .yaml)
  role_var_files()     tracked role defaults and vars files
  tasks(path)          every task, in plays, blocks, rescues, always and handlers
  module(task)         (canonical module name, its arguments as a dict)
  references(node)     the variable names a node's templates and conditions use
  defined_here(node)   the names a node defines itself (vars, set_fact, register)
  Scan                 counts what a test scanned and which allowlist entries it used,
                       so a test fails on an empty scan and on a stale exception
"""

import re
import shlex
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
from repo_files import tracked  # noqa: E402

# Keys that are a task's own settings, never its module.
TASK_KEYS = {
    "name", "when", "loop", "loop_control", "with_items", "with_dict", "with_fileglob", "with_list",
    "with_together", "with_subelements", "with_nested", "with_first_found", "with_sequence",
    "register", "notify", "tags", "vars", "become", "become_user", "become_method", "delegate_to",
    "delegate_facts", "run_once", "changed_when", "failed_when", "ignore_errors", "check_mode",
    "diff", "no_log", "environment", "args", "until", "retries", "delay", "async", "poll",
    "listen", "any_errors_fatal", "throttle", "timeout", "local_action", "block", "rescue",
    "always", "connection", "module_defaults", "collections", "debugger", "ignore_unreachable",
}  # fmt: skip
PLAY_LISTS = ("pre_tasks", "tasks", "post_tasks", "handlers")
NESTED = ("block", "rescue", "always")


def shell_scripts(templates=True):
    """(path, text) of every tracked shell script: *.sh and *.bash, and files whose #!
    line names sh, bash or dash (not zsh, csh or fish). templates=False leaves out role
    templates, which only parse as shell once rendered."""
    shebang = re.compile(r"#!\s*\S*?(?:/env\s+)?\S*?\b(?:ba|da)?sh\b")
    other = re.compile(r"#!.*\b(?:zsh|t?csh|fish|python\d*|perl|ruby)\b")
    for path in tracked("*", ":!submodules", ":!roles/galaxy"):
        rel = path.relative_to(ROOT).as_posix()
        if not templates and ("/templates/" in rel or rel.endswith(".j2")):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        first = text.split("\n", 1)[0]
        if path.suffix in (".sh", ".bash") or (shebang.match(first) and not other.match(first)):
            yield path, text


def _ours(path):
    rel = path.relative_to(ROOT).as_posix()
    return not rel.startswith("roles/galaxy/") and "/molecule/" not in rel


def ansible_files():
    """Task and handler files of every role, and every playbook."""
    patterns = []
    for kind in ("tasks", "handlers"):
        patterns += [f"roles/*/{kind}/*.yml", f"roles/*/{kind}/*.yaml", f"roles/*/{kind}/**/*.yml",
                     f"roles/*/{kind}/**/*.yaml"]  # fmt: skip
    patterns += ["playbooks/*.yml", "playbooks/*.yaml", "playbooks/**/*.yml", "playbooks/**/*.yaml"]
    return [p for p in tracked(*patterns) if _ours(p)]


def role_var_files():
    """Role defaults and vars files: defaults/main.y*ml and vars/*.y*ml."""
    return [p for p in tracked("roles/*/defaults/main.y*ml", "roles/*/vars/*.y*ml") if _ours(p)]


def load(path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None


def _walk(items):
    for task in items or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in NESTED:
            yield from _walk(task.get(key))


def tasks(path):
    """Every task in PATH: a playbook's plays (their task lists and handlers) or a task
    file, with blocks, rescues and always sections walked into."""
    data = load(path)
    if not isinstance(data, list):
        return
    for item in data:
        if isinstance(item, dict) and ("hosts" in item or "import_playbook" in item):
            for key in PLAY_LISTS:
                yield from _walk(item.get(key))
        else:
            yield from _walk([item])


# The actions that run a command: change_command and change_shell (plugins/action) are
# command and shell for a change, which --check reports instead of skipping.
CHANGES = frozenset({"change_command", "change_shell"})
COMMANDS = frozenset({"command", "shell", "script", "raw", "expect"}) | CHANGES
# Ansible's own service modules, which every task replaces with systemd_unit (rule 22).
SERVICES = frozenset({"systemd", "systemd_service", "service"})
UNITS = SERVICES | {"systemd_unit"}


def canonical(name):
    """ansible.builtin.copy, ansible.legacy.copy and copy are one module."""
    return name.rsplit(".", 1)[-1] if isinstance(name, str) else name


def _free_form(text):
    """`src=a dest=b` -> {"src": "a", "dest": "b"}; the rest is the raw command."""
    args, raw = {}, []
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    for word in words:
        key, sep, value = word.partition("=")
        if sep and key.isidentifier():
            args[key] = value
        else:
            raw.append(word)
    if raw:
        args["_raw_params"] = " ".join(raw)
    return args


def module(task):
    """(canonical module name, arguments as a dict), or (None, {}) for a block. Free-form
    arguments are parsed and a task's `args:` merged in, as Ansible does."""
    for key, value in task.items():
        if key in TASK_KEYS:
            continue
        if key == "local_action":
            continue
        args = value if isinstance(value, dict) else (_free_form(value) if isinstance(value, str) else {})
        extra = task.get("args")
        if isinstance(extra, dict):
            args = {**args, **extra}
        return canonical(key), args
    return None, {}


def text_of(task):
    """Everything a task would run or write, for checks that look at the words."""
    _, args = module(task)
    return " ".join(str(v) for v in args.values())


CONDITIONS = {"when", "that", "failed_when", "changed_when", "until"}
_JINJA = re.compile(r"\{\{(.*?)\}\}|\{%(.*?)%\}", re.S)
_STRING = re.compile(r"'[^']*'|\"[^\"]*\"")
_IDENT = re.compile(r"(?<![\w.])[A-Za-z_]\w*")


def _names(expression):
    return set(_IDENT.findall(_STRING.sub("", expression)))


def references(node, key=None):
    """The variable names NODE's templates and conditions use: identifiers inside
    {{ }} and {% %}, and in the bare expressions of when/that/failed_when/changed_when/
    until, string literals left out. Filter and test names come too; callers match
    against variables they know."""
    found = set()
    if isinstance(node, dict):
        for k, v in node.items():
            found |= references(v, k)
    elif isinstance(node, list):
        for item in node:
            found |= references(item, key)
    elif isinstance(node, str):
        if key in CONDITIONS:
            found |= _names(_JINJA.sub(lambda m: m.group(1) or m.group(2) or "", node))
        for m in _JINJA.finditer(node):
            found |= _names(m.group(1) or m.group(2) or "")
    return found


def defined_here(node):
    """Names NODE defines for itself: vars, set_fact, register and loop_var."""
    found = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("vars", "set_fact", "ansible.builtin.set_fact") and isinstance(v, dict):
                found |= set(v)
            elif k == "register" and isinstance(v, str):
                found.add(v)
            elif k == "loop_control" and isinstance(v, dict) and "loop_var" in v:
                found.add(v["loop_var"])
            found |= defined_here(v)
    elif isinstance(node, list):
        for item in node:
            found |= defined_here(item)
    return found


class Scan:
    """What a guard test looked at. `seen(path)` counts a scanned file; `excuse(key)`
    marks an allowlist entry used. `check(test)` fails a test that scanned fewer files
    than it must, or whose allowlist holds an entry that excused nothing."""

    def __init__(self, allowlist=(), minimum=1):
        self.allowlist, self.minimum, self.files, self.used = set(allowlist), minimum, 0, set()

    def seen(self, path):
        self.files += 1
        return path

    def excuse(self, key):
        if key in self.allowlist:
            self.used.add(key)
            return True
        return False

    def check(self, test):
        test.assertGreaterEqual(self.files, self.minimum, "the scan found nothing to check")
        test.assertEqual(
            sorted(map(str, self.allowlist - self.used)), [], "exceptions that excuse nothing; drop them"
        )
