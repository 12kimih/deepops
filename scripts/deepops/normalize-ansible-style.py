#!/usr/bin/env python3
"""Rewrite Ansible YAML into this fork's lint style, touching only style.

Upstream DeepOps lints at the `min` profile; this fork lints at `production`.
Running this over upstream code before a merge removes the style-only
differences, so the merge conflicts only where content really differs.

Rules (all line based, so comments and layout survive):
  fqcn     task-level module keys get their collection prefix
  casing   task, play and handler names, and notify/listen entries, start upper case
  truthy   yes/no/True/False values become true/false
  octal    bare `mode: 0644` becomes `mode: "0644"`
  plugins  `become_method: sudo` becomes `become_method: ansible.builtin.sudo`
  roles    a play's `roles:` entries are written `- role: X`, not `- name: X`
  jinja    `{{x|f}}` becomes `{{ x | f }}`, outside quoted strings
  layout   a `---` document start and no trailing blanks everywhere; in Ansible
           files and role defaults/vars/meta, sequences indented under their key

The layout, truthy and octal rules apply to every YAML file yamllint checks;
fqcn, casing and jinja apply only to Ansible task, handler and play files.

Usage: normalize-ansible-style.py [--check] PATH...
  PATH may be a file or a directory. --check lists files that would change and
  exits 1 if any would.
"""
import os
import re
import sys

BUILTIN = set("""
add_host apt apt_key apt_repository assemble assert async_status blockinfile command copy cron
deb822_repository debconf debug dnf dnf5 dpkg_selections expect fail fetch file find gather_facts
get_url getent git group group_by hostname import_playbook import_role import_tasks include_role
include_tasks include_vars iptables known_hosts lineinfile meta package package_facts pause ping pip
raw reboot replace rpm_key script service service_facts set_fact set_stats setup shell slurp stat
subversion systemd systemd_service sysvinit tempfile template unarchive uri user
validate_argument_spec wait_for wait_for_connection yum_repository
""".split())
# yum was removed from ansible.builtin; dnf is the supported module on the RHEL family.
RENAME = {"yum": "ansible.builtin.dnf"}
COLLECTION = {
    "authorized_key": "ansible.posix", "mount": "ansible.posix", "sysctl": "ansible.posix",
    "seboolean": "ansible.posix", "firewalld": "ansible.posix", "synchronize": "ansible.posix",
    "selinux": "ansible.posix", "patch": "ansible.posix",
    "modprobe": "community.general", "timezone": "community.general", "sefcontext": "community.general",
    "locale_gen": "community.general", "ini_file": "community.general", "ufw": "community.general",
    "pam_limits": "community.general", "alternatives": "community.general", "make": "community.general",
    "docker_container": "community.docker", "docker_volume": "community.docker",
    "docker_login": "community.docker", "docker_image": "community.docker",
    "docker_network": "community.docker",
    "mysql_user": "ansible.mysql", "mysql_db": "ansible.mysql",
}
# Keys that mark a list item as a task (or play), besides a module key.
TASK_MARKERS = {"block", "hosts", "import_playbook", "include_tasks", "import_tasks",
                "include_role", "import_role", "roles", "tasks"}

ITEM_RE = re.compile(r"^(\s*)- (?:(\S+?):(?=\s|$))?")
KEY_RE = re.compile(r"^(\s*)([A-Za-z_][\w.]*):(?=\s|$)")


def fqcn(mod):
    if mod in RENAME:
        return RENAME[mod]
    if mod in BUILTIN:
        return "ansible.builtin." + mod
    if mod in COLLECTION:
        return COLLECTION[mod] + "." + mod
    return None


def is_module(key):
    # import_playbook is a play-level keyword here; the fork keeps it short.
    return key != "import_playbook" and (key in BUILTIN or key in COLLECTION or key in RENAME)


def is_action(key):
    """A module key, short or already fully qualified."""
    return is_module(key) or key.count(".") == 2


def items(lines):
    """Yield (start, end, key_indent) for every block-sequence item."""
    for i, line in enumerate(lines):
        m = ITEM_RE.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        end = len(lines)
        for j in range(i + 1, len(lines)):
            s = lines[j]
            if not s.strip() or s.lstrip().startswith("#"):
                continue
            if len(s) - len(s.lstrip()) <= indent:
                end = j
                break
        yield i, end, indent + 2


TASK_LISTS = {"tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"}


def in_task_list(lines, start):
    """True when the item at `start` is a task or play, not an entry of a data list."""
    col = len(lines[start]) - len(lines[start].lstrip())
    for j in range(start - 1, -1, -1):
        s = lines[j]
        if not s.strip() or s.lstrip().startswith("#") or s == "---":
            continue
        ind = len(s) - len(s.lstrip())
        if ind < col or (ind == col and not s.lstrip().startswith("- ")):
            m = re.match(r"^\s*(?:- )?([\w.]+):\s*(?:#.*)?$", s)
            return bool(m) and m.group(1) in TASK_LISTS
    return True


def item_keys(lines, start, end, key_indent):
    keys = {}
    first = ITEM_RE.match(lines[start])
    if first and first.group(2):
        keys[first.group(2)] = start
    for j in range(start + 1, end):
        m = KEY_RE.match(lines[j])
        if m and len(m.group(1)) == key_indent:
            keys.setdefault(m.group(2), j)
    return keys


def cap(text):
    m = re.match(r"(\s*[\"']?)([a-z])", text)
    return text if not m else text[:m.end(1)] + m.group(2).upper() + text[m.end(2):]


def space_jinja(expr):
    """Space `|` filters and the braces of one {{ ... }} body, skipping quoted text."""
    out, quote, i = [], None, 0
    while i < len(expr):
        c = expr[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < len(expr):
                out.append(expr[i + 1]); i += 1
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c; out.append(c)
        elif c == "|" and not (i + 1 < len(expr) and expr[i + 1] == "|") and not (out and out[-1] == "|"):
            while out and out[-1] == " ":
                out.pop()
            out.append(" | ")
            i += 1
            while i < len(expr) and expr[i] == " ":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    body = re.sub(r"\belse\{", "else {", "".join(out).strip())
    return " " + body + " "


CONDITION_KEYS = ("when", "failed_when", "changed_when", "until")


def jinja_spans(line):
    """(start, end) of each {{ ... }} body, matching braces and skipping quoted text."""
    spans, i = [], 0
    while True:
        i = line.find("{{", i)
        if i < 0:
            return spans
        j, depth, quote = i + 2, 0, None
        while j < len(line) - 1:
            c = line[j]
            if quote:
                if c == "\\":
                    j += 1
                elif c == quote:
                    quote = None
            elif c in "'\"":
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                if depth == 0 and line[j + 1] == "}":
                    break
                depth -= 1
            j += 1
        else:
            return spans
        spans.append((i + 2, j))
        i = j + 2


def fix_jinja(line):
    if line.lstrip().startswith("#"):
        return line
    out, last = [], 0
    for start, end in jinja_spans(line):
        body = line[start:end]
        if body.startswith("-") or body.endswith("-"):
            continue
        out.append(line[last:start] + space_jinja(body))
        last = end
    return "".join(out) + line[last:]


def indent_sequences(lines):
    """Indent a block sequence that sits at the same column as its parent key."""
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)(?:- )?[\w.\"']+:\s*(?:#.*)?$", lines[i])
        if m:
            col = len(lines[i]) - len(lines[i].lstrip())
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("#")):
                j += 1
            if j < len(lines) and lines[j].startswith(" " * col + "- ") and not lines[i].lstrip().startswith("- "):
                k = j
                while k < len(lines):
                    s = lines[k]
                    if s.strip() and not s.lstrip().startswith("#"):
                        ind = len(s) - len(s.lstrip())
                        if ind < col or (ind == col and not s.lstrip().startswith("- ")):
                            break
                    if s.strip():
                        lines[k] = "  " + s
                    k += 1
        i += 1
    return lines


# Same exclusions as .yamllint, plus Jinja templates which are not YAML yet.
SKIP_DIRS = re.compile(r"(^|/)(submodules|roles/galaxy|\.ansible|\.venv|collections|config|templates|\.git)(/|$)")


def is_ansible(path, lines):
    if re.search(r"(^|/)(tasks|handlers|playbooks)/", path) or re.search(r"(^|/)molecule/.*/(converge|prepare|verify|side_effect)\.ya?ml$", path):
        return True
    body = [l for l in lines if l.strip() and not l.lstrip().startswith("#") and l != "---"]
    return bool(body) and body[0].startswith("- ") and any(re.match(r"^\s*-?\s*hosts:", l) for l in body)


def fix_conditions(lines):
    """Space the filters of bare conditions (when: a|b and its list items)."""
    for n, line in enumerate(lines):
        m = re.match(r"^(\s*(?:- )?(?:" + "|".join(CONDITION_KEYS) + r"):\s+)(.+)$", line)
        if m and not m.group(2).lstrip().startswith(("'", '"', ">", "|", "{{")):
            lines[n] = m.group(1) + space_jinja(m.group(2)).strip()
            continue
        m = re.match(r"^(\s*(?:" + "|".join(CONDITION_KEYS) + r"):)\s*$", line)
        if m:
            col = len(line) - len(line.lstrip())
            for k in range(n + 1, len(lines)):
                mm = re.match(r"^(\s*- )(.+)$", lines[k])
                if not mm or len(mm.group(1)) - 2 < col:
                    break
                if not mm.group(2).lstrip().startswith(("'", '"', ">", "|", "{{")):
                    lines[k] = mm.group(1) + space_jinja(mm.group(2)).strip()
    return lines


def fix_roles(lines):
    """`roles:` list entries name their role with `role:`, the key the playbook schema requires."""
    for n, line in enumerate(lines):
        m = re.match(r"^(\s*)roles:\s*$", line)
        if not m:
            continue
        col = len(m.group(1))
        for k in range(n + 1, len(lines)):
            s = lines[k]
            if not s.strip() or s.lstrip().startswith("#"):
                continue
            ind = len(s) - len(s.lstrip())
            if ind < col or (ind == col and not s.lstrip().startswith("- ")):
                break
            mm = re.match(r"^(\s*)- name:(\s.*)$", s)
            if mm and len(mm.group(1)) <= col + 2:
                lines[k] = mm.group(1) + "- role:" + mm.group(2)
    return lines


def double_quote(line):
    """'text' -> "text" for whole scalars that read the same in both quotings."""
    def swap(m):
        inner = m.group(3)
        if '"' in inner or "\\" in inner or "''" in inner:
            return m.group(0)
        return m.group(1) + m.group(2) + '"' + inner + '"' + m.group(4)
    return re.sub(r"^(\s*)((?:- )?(?:[\w.\-]+:\s+)?)'([^']*)'(\s*(?:#.*)?)$", swap, line)


def normalize(text, path):
    lines = [l.rstrip() for l in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    lines.append("")
    while lines and not lines[0].strip():
        lines.pop(0)
    head = next((l for l in lines if l.strip() and not l.lstrip().startswith("#")), "---")
    if head != "---" and not (lines and lines[0].startswith("#!")):
        lines.insert(0, "---")
    ansible = is_ansible(path, lines)
    layout = bool(re.search(r"(^|/)roles/[^/]+/(defaults|vars|meta|molecule)/", path))
    if ansible or layout:
        lines = indent_sequences(lines)
    is_handlers = "/handlers/" in path
    for start, end, key_indent in (list(items(lines)) if ansible else []):
        if not in_task_list(lines, start):
            continue
        keys = item_keys(lines, start, end, key_indent)
        # Play-level keys such as gather_facts share names with modules.
        modules = [] if "hosts" in keys else [k for k in keys if is_module(k)]
        actions = [k for k in keys if is_action(k)]
        if not (actions or TASK_MARKERS & set(keys) or (is_handlers and "name" in keys)):
            continue
        for k in modules:
            j = keys[k]
            new = fqcn(k)
            lines[j] = re.sub(r"^(\s*(?:- )?)" + re.escape(k) + ":", r"\g<1>" + new + ":", lines[j], count=1)
        for k in ("name", "listen"):
            if k in keys:
                j = keys[k]
                lines[j] = re.sub(r"^(\s*(?:- )?" + k + r":\s*)(.*)$", lambda m: m.group(1) + cap(m.group(2)), lines[j])
        if "notify" in keys:
            j = keys["notify"]
            m = re.match(r"^(\s*(?:- )?notify:\s*)(\S.*)$", lines[j])
            if m:
                lines[j] = m.group(1) + cap(m.group(2))
            else:
                for n in range(j + 1, end):
                    mm = re.match(r"^(\s*- )(.*)$", lines[n])
                    if not mm or len(mm.group(1)) - 2 <= key_indent - 2:
                        break
                    lines[n] = mm.group(1) + cap(mm.group(2))
    if ansible:
        lines = fix_conditions(lines)
        lines = fix_roles(lines)
    for n, line in enumerate(lines):
        if ansible or layout:
            line = double_quote(line)
        line = re.sub(r"^(\s*(?:- )?[\w.]+:\s+)(yes|Yes|YES|True|TRUE)(\s*(?:#.*)?)$", r"\1true\3", line)
        line = re.sub(r"^(\s*(?:- )?[\w.]+:\s+)(no|No|NO|False|FALSE)(\s*(?:#.*)?)$", r"\1false\3", line)
        line = re.sub(r"^(\s*(?:- )?mode:\s+)(0[0-7]{3})(\s*(?:#.*)?)$", r'\1"\2"\3', line)
        if ansible:
            line = re.sub(r"^(\s*(?:- )?become_method:\s+)(sudo|su|runas|doas|pbrun|pfexec|ksu|dzdo|machinectl|sesu|enable)(\s*(?:#.*)?)$",
                          r"\1ansible.builtin.\2\3", line)
        lines[n] = fix_jinja(line) if ansible else line
    return "\n".join(lines)


def targets(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if not SKIP_DIRS.search(os.path.relpath(os.path.join(root, d)))]
            for f in files:
                if f.endswith((".yml", ".yaml")):
                    yield os.path.relpath(os.path.join(root, f))


def main(argv):
    check = "--check" in argv
    paths = [a for a in argv if a != "--check"] or ["."]
    changed = []
    for f in targets(paths):
        text = open(f, encoding="utf-8").read()
        new = normalize(text, f)
        if new != text:
            changed.append(f)
            if not check:
                open(f, "w", encoding="utf-8").write(new)
    for f in changed:
        print(f)
    return 1 if (check and changed) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
