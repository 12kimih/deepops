"""Repository-wide rules for tasks that keep `--check --diff` previews correct and safe.

1. A command/shell/script task that only reads (changed_when: false) and registers a
   result must set check_mode: false, unless its `when` skips it under --check on
   purpose (`not ansible_check_mode`). Under --check Ansible skips commands, so the
   registered result is empty and every task that reads it fails or decides wrongly.
2. A template task whose template renders a secret must set diff: false, or a
   --diff preview prints the secret. So must a copy whose content holds one or whose
   file is one by name (a private key, a kubeconfig, a token; no_log hides it as well):
   the registry cache's CA key and the admin kubeconfig were printed (2026-11 audit).
4. A play's hosts (`hosts:`, and the `hostlist` a playbook import passes) name inventory
   groups and may template only `hostlist`: they are resolved before group_vars
   load, so any other variable there silently ignores what config sets.
5. A `when` condition is a boolean: ansible-core 2.19+ fails the task on one that
   yields a number or a string (`x | length`, `x | default('')`), so it must compare
   (`| length > 0`) or cast (`| bool`).
6. Nothing from the network is used unverified: every get_url carries a checksum, and
   an archive or a package is never taken straight from a URL (unarchive src, apt deb,
   dnf name). roles/download fetches with a required checksum, then unpacks.
7. Facts are read through ansible_facts (`ansible_facts.services`,
   `ansible_facts.distribution`), never as the variables Ansible also injects
   (`services`, `ansible_distribution`): injection is deprecated and goes away, and a
   condition on a name that is gone is simply never true.
8. Nothing root writes, unpacks, builds or runs lives under a world-writable directory
   (/tmp, /var/tmp, /dev/shm): a user can plant the file or tree first, or swap it
   after. Root's scratch is /var/cache/deepops.
9. No secret is on a command line: argv is readable by every user on the host (ps). That
   includes a task's `environment:`, which Ansible puts on the module's command line
   (`/bin/sh -c 'VAR=value python3 ...'`) even under no_log.
   A command takes it on stdin, from a root-only file, or from its environment.
10. Only roles/maintenance reboots, and only a playbook asks it to: a role whose change
   needs a reboot calls its reboot_request (the reboot is left pending, then done with
   the node drained), never its reboot step, ansible.builtin.reboot or a reboot command.
11. A tag on a dynamic include (include_tasks, include_role) other than `always` is
   also passed on with apply: tags on the include select only the include itself, so
   `--tags build` ran the include and none of the tasks it loads.
12. A dpkg status abbreviation (${db:Status-Abbrev}) is read by its second letter, the
   package's state: installed is "?i". The first is only what is wanted (h for a held
   package), so matching "ii" missed held packages: the Slurm version guards and the
   package metrics skipped them.
13. A template in a YAML value (`{{ }}`, `{% %}`) holds single backslashes once YAML is
   read: ansible-core passes its string literals to the filter as written, so `'\\1'`
   or `'\\.'` reached regex_search and match doubled (a backreference that failed the
   task, a version that never matched). Conditionals and .j2 files unescape them, so
   they do not count.
14. No task keeps Ansible's backup copy (`backup: true`) of a host file: each change
   leaves `<file>.<pid>.<date>@<time>~` beside it, never removed, so /etc fills with
   stale copies (and copies of secrets). The repository is the history; a task that
   needs the old file to roll back removes the copy itself.
15. A container sees a host path only through a bind mount (`--mount type=bind`,
   docker_container `mounts:`), never `-v`/`--volume` or `volumes:`: given a source
   that does not exist yet, those create it as an empty root-owned directory, so a file
   written later went inside it and the container never read it (the registry's
   config.yml, 2026-06 to 2026-11). A bind refuses the missing source instead.
16. A task's delegate_to never names a setting that may be empty while the task can be
   skipped: ansible-core resolves delegate_to before `when`, so the skipped task fails
   ("Empty hostname produced from delegate_to"). The driver's removal on a host with no
   Slurm controller stopped there (require_drained, 2026-11). Delegate to a value that
   falls back to a host (maintenance_slurm_delegate).
17. An inventory group is named by its name (groups['slurm-node']), never through a
   role setting that holds it (slurm_compute_group: slurm-node): a play's hosts cannot
   be templated, so overriding such a setting would move half the code and not the
   rest. config.example/inventory names the groups, the one contract.
18. deepops' own directories on a host are named once, deepops_state_dir and
   deepops_cache_dir in roles/deepops_paths: no task, default or template spells out
   /var/lib/deepops or /var/cache/deepops (41 places did, each a copy to keep in step).
19. A command that changes the host runs as change_command or change_shell
   (plugins/action), never as command or shell: Ansible skips those under --check and
   display_skipped_hosts hides the skip, so a preview reported changed=0 for changes
   the run then made (a BMC's gateway, 2026-11). command and shell are for reading
   (changed_when: false), for creates/removes, which command predicts itself, and for a
   task that runs under --check on purpose (check_mode: false). A change task's
   changed_when, failed_when and until that read its own result are guarded, since
   under --check nothing ran: `ansible_check_mode or (...)` for changed_when and until,
   `not ansible_check_mode and (...)` for failed_when. A command whose line plainly
   changes the host (kubectl apply, systemctl start, mount, dnf enable, ...) cannot
   claim to only read (changed_when: false): that hid `kubectl apply` and
   `dnf module enable` from previews and reports.
   systemd_unit, change_command and change_shell preview themselves under --check (a
   unit not installed yet included), so they never skip it with `when: not
   ansible_check_mode`: that hid 18 restarts and enables from every preview.
20. A condition (when, changed_when, failed_when, until, an assert's that) spells only
   escapes Python knows in its string literals: Jinja unescapes them there (rule 13's
   other half), so `match('^\\\\S+\\\\s')` is the regex `^\\S+\\s`, and a single `\\s`
   is an invalid escape that Python deprecates and will refuse.
21. An in-tree module that supports check mode says what it changes: it returns a
   `diff` built by plugins/module_utils/deepops_diff.py (a file's text, or the steps it
   takes). Without one --check --diff showed only "changed" for files a module would
   remove (stale_files, 2026-11), entries it would write, packages it would install.
22. A unit is started, stopped, enabled or reloaded through systemd_unit
   (plugins/action), never ansible.builtin.systemd, systemd_service or service: under
   --check a unit this run installs (a package's, a template's) does not exist yet, and
   those fail on it (fail2ban's preview failed on every node without it, 2026-11), in a
   task or a handler alike. systemd_unit runs as systemd_service and previews such a
   unit as what it would do once installed.
23. Only roles/deepops_hosts writes /etc/hosts. It assembles the file from fragments;
   another role adds its lines through `tasks_from: fragment`. A line written straight
   into /etc/hosts (the registry's, Run:ai's) was dropped by the next deploy.
24. A package listing names packages with their architecture (dpkg-query
   ${binary:Package}), never ${Package}: that drops the qualifier, so a foreign-arch copy
   (libnvidia-*:i386) is named as the native one and never removed (a driver switch left
   them, 2026-11).
25. Files are found through find_present (plugins/action), never ansible.builtin.find:
   find warns for every path not on the host, so a component's removal looking on hosts
   that never had it (prometheus' alert rules) printed a warning per host each deploy.
   find_present treats a missing path as an empty directory.
26. An in-tree module runs a command through plugins/module_utils/deepops_command.py,
   never module.run_command or subprocess: run_command takes no timeout, so a sacctmgr
   with slurmdbd gone or a stuck systemctl held the whole play (2026-11 audit).
27. A task of a module that previews itself under --check (PREVIEWING: change_command,
   systemd_unit, file, copy, template, apt, ...) has no `not ansible_check_mode` in its
   when, in any form: `not ansible_check_mode or X` hid Lmod's profile links from every
   first-install preview. A gate stays only with its reason in CHECK_GATED_OK.
28. A tempfile task sets check_mode: false, and so does the task removing what it made:
   tempfile is skipped under --check, so every later task naming its .path failed the
   preview (helm_release's authenticated install, 2026-11). Scratch is not a host change.
3. No expression reads the internal `vars` dictionary (`x in vars`, `vars[...]`):
   ansible-core 2.20 deprecates it and 2.24 removes it. Test `x is defined`, or use
   the vars/varnames lookups for a computed name.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

import jinja2
import yaml
from jinja2 import nodes

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import (  # noqa: E402
    CHANGES,
    COMMANDS,
    SERVICES,
    Scan,
    ansible_files,
    conditioned_tasks,
    module,
    role_var_files,
    tasks,
    tracked,
)

# How a change task's conditions on its own result start (rule 19).
CHECK_GUARDS = {
    "changed_when": "ansible_check_mode or ",
    "until": "ansible_check_mode or ",
    "failed_when": "not ansible_check_mode and ",
}
TEMPLATES = {"template"}
# Fetched without a fixed checksum because the file changes in place upstream, and
# checked against its publisher's signing key right after instead.
SIGNATURE_CHECKED = {"roles/package_repos/tasks/epel.yml"}
URL = re.compile(r"^\s*(https?|ftp)://|^\s*git\+|^\s*\{\{[^}]*(url|_src)\b")
REBOOT_STEP = "roles/maintenance/tasks/reboot.yml"
REBOOT_COMMAND = re.compile(
    r"(?:^|[;&|(]\s*|\s)(?:/\S*/)?(?:reboot|shutdown\s+(?:-r|--reboot)|systemctl\s+(?:--\S+\s+)*(?:reboot|kexec))(?=\s|$|[;&|)])",
    re.M,
)
SECRET = re.compile(
    r"\{\{[^}]*(password|(?<!no)passwd|_token|\btoken\b|secret|webhook|api_key|_pass\b|heartbeat_url|munge_key)[^}]*\}\}"
)
# A file that is a secret by its name: a private key (not a loop's item.key), a
# kubeconfig, a token, a password or credentials file, the MUNGE key.
SECRET_FILE = re.compile(
    r"(?<!item)\.key\b|private|password|passwd|secret|token|admin\.conf|kubeconfig|\.kube/config|credentials|htpasswd|munge",
    re.I,
)
MINIMUM_FILES = 200  # a scan that sees fewer has lost its way


# A backslash in a Python string literal that does not start a known escape.
INVALID_ESCAPE = re.compile(r"\\(?:\\|[\n'\"abfnrtv0-7xuUN]|(.))", re.S)
CONDITION_KEYS = ("when", "changed_when", "failed_when", "until")


def condition_strings(task):
    """A task's conditions as written: its when/changed_when/failed_when/until and, for
    an assert, its that."""
    found = [task.get(key) for key in CONDITION_KEYS]
    name, args = module(task)
    if name == "assert":
        found.append(args.get("that"))
    for value in found:
        for cond in value if isinstance(value, list) else [value]:
            if isinstance(cond, str):
                yield cond


def invalid_escapes(cond):
    """The invalid escapes in COND's string literals (a condition is an expression)."""
    try:
        tokens = list(JINJA.lex("{{ " + cond + " }}"))
    except jinja2.TemplateSyntaxError:
        return []
    return [
        m.group(0)
        for _, kind, value in tokens
        if kind == "string"
        for m in INVALID_ESCAPE.finditer(value)
        if m.group(1) is not None
    ]


# Attributes of registered results and facts that hold a number, string or list.
VALUE_ATTRS = {
    "stdout",
    "stderr",
    "stdout_lines",
    "stderr_lines",
    "rc",
    "count",
    "files",
    "results",
    "matched",
    "msg",
    "content",
    "path",
    "size",
}
# Filters that return a boolean (plugins/filter adds mode_exceeds).
BOOL_FILTERS = {"bool", "mode_exceeds"}
JINJA = jinja2.Environment()


def not_boolean(cond):
    """True when a condition surely yields a number, string or list, never a boolean.
    ansible-core 2.19+ fails a task on such a `when`; a bare variable of unknown type
    passes (most are switches)."""
    if "{{" in cond:
        return False
    try:
        tree = JINJA.parse("{{ " + cond + " }}").body[0].nodes[0]
    except jinja2.TemplateSyntaxError:
        return False

    def value(node):
        if isinstance(node, (nodes.Compare, nodes.Test, nodes.Not)):
            return False
        if isinstance(node, (nodes.And, nodes.Or)):
            return value(node.left) or value(node.right)
        if isinstance(node, nodes.CondExpr):
            return value(node.expr1) or (node.expr2 is not None and value(node.expr2))
        if isinstance(node, nodes.Filter):
            if node.name in ("default", "d") and node.args and isinstance(node.args[0], nodes.Const):
                return not isinstance(node.args[0].value, bool) or value(node.node)
            return node.name not in BOOL_FILTERS
        if isinstance(node, nodes.Const):
            return not isinstance(node.value, bool)
        if isinstance(node, nodes.Getattr):
            return node.attr in VALUE_ATTRS
        if isinstance(node, nodes.Getitem):
            return isinstance(node.arg, nodes.Const) and node.arg.value in VALUE_ATTRS
        return isinstance(
            node, (nodes.BinExpr, nodes.UnaryExpr, nodes.List, nodes.Dict, nodes.Tuple, nodes.Concat)
        )

    return value(tree)


def rel(path):
    return path.relative_to(ROOT).as_posix()


# A command line that plainly changes the host. A task running one cannot claim to only
# read (changed_when: false): it would be skipped by --check and report "ok" when it ran.
MUTATES = re.compile(
    r"\bkubectl\s+(apply|create(?![^|]*--dry-run)|delete|patch|replace|label|annotate|taint)\b"
    r"|\bsystemctl\s+(start|stop|restart|reload|enable|disable|mask|unmask)\b"
    r"|\bu?mount\s+-"
    r"|\bdnf\b[^|;]*\b(install|remove|erase|enable|disable|reset|config-manager)\b"
    r"|\bapt(-get)?\s+(-\S+\s+)*(install|remove|purge)\b"
    r"|\bnvidia-smi\b[^|;]*\s-(acp|pm|e|mig|lgc|rgc|pl|rac)\b"
    r"|\b(useradd|usermod|userdel|groupadd|chown|chmod|ln\s+-s)\b"
)


def command_line(args):
    """What a command task runs: its free-form text, cmd or argv."""
    line = args.get("cmd") or args.get("argv") or args.get("_raw_params") or args.get("command") or ""
    return " ".join(map(str, line)) if isinstance(line, list) else str(line)


def template_path(path, src):
    """The role template a task renders; a `{{ var }}` src is resolved from the role defaults."""
    if "roles" not in path.parts:
        return None
    role = ROOT / "roles" / path.parts[path.parts.index("roles") + 1]
    var = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", src)
    if var:
        defaults = role / "defaults" / "main.yml"
        src = str(
            ((yaml.safe_load(defaults.read_text()) if defaults.is_file() else None) or {}).get(
                var.group(1), ""
            )
        )
    if not src or "{{" in src:
        return None
    candidate = role / "templates" / src
    return candidate if candidate.is_file() else None


# Facts as injected variables: the fact modules' bare names, and gathered facts by
# their ansible_ prefix. Magic variables (ansible_check_mode, ansible_host...) are not
# facts and are not listed.
INJECTED = re.compile(
    r"(?<![\w.'\"\[-])(?<!filter=)(?<!filter: )(?:(?:in\s+|not\s+in\s+)(?:services|packages)\b|(?:services|packages)\["
    r"|ansible_(?:distribution|os_family|kernel|architecture|processor|memtotal_mb|memory_mb|memfree_mb|lsb"
    r"|pkg_mgr|service_mgr|devices|mounts|interfaces|default_ipv[46]|all_ipv[46]_addresses|fqdn|hostname"
    r"|nodename|machine_id|virtualization|system|python(?!_interpreter)|date_time|env|local|product|bios"
    r"|cmdline|selinux|apparmor|user_id|domain|dns|proc_cmdline|uptime_seconds|board|swaptotal_mb)\w*)"
)


# The same facts read by name from hostvars, or by a name built at run time.
INJECTED_INDIRECT = re.compile(
    r"""\[\s*['"]ansible_(?!facts\b|local\b|host\b|user\b|port\b|connection\b|become|ssh_|python_interpreter)"""
    r"""|['"]ansible_['"]?\s*[+~]"""
)


def fact_files():
    """Everything a fact could be read in: tasks, handlers, playbooks, role variables
    and templates."""
    return ansible_files() + role_var_files() + tracked("roles/*/templates/**")


# A path under a world-writable directory, wherever it appears in a line.
WORLD_WRITABLE = re.compile(r"(?<![\w.$}-])/(?:tmp|var/tmp|dev/shm)(?:/|\b)")
# file -> why its world-writable path is not root's to trust
WORLD_WRITABLE_OK = {
    "roles/enroot/defaults/main.yml": "a per-user directory the job user owns; the prolog makes it as that user",
    "playbooks/slurm-cluster/slurm-validation.yml": "the operator's own output file, written with become: false",
    "roles/nvidia-dgx-firmware/tasks/transfer-logs.yml": "upstream DGX: fetches the dump nvsm itself wrote there",
    "roles/nvidia-dgx-firmware/tasks/clean.yml": "upstream DGX: removes that same nvsm dump",
    "roles/ai_agent_rules/tasks/main.yml": "fetched on the controller, as the operator (become: false)",
    "roles/download/tasks/main.yml": "the assert that refuses these paths names them",
    "roles/slurm_paths/defaults/main.yml": "slurm_job_private_dirs names the directories Slurm makes private per job",
}
# file -> why it keeps Ansible's backup copy
BACKUP_OK = {
    "roles/sshd/tasks/main.yml": "puts back what sshd -t rejects, then removes the copies (always:)",
    "playbooks/k8s-cluster.yml": "the operator's own ~/.kube/config on the controller, not a host file",
}
# file -> why it changes the host through command or shell (rule 19)
UPSTREAM_DGX = "upstream DGX role, kept as NVIDIA ships it (outside ansible-lint too)"
# Modules that preview themselves under --check: a `not ansible_check_mode` in their when
# hides from the preview a change the run then makes (rule 27).
PREVIEWING = {
    "systemd_unit", "change_command", "change_shell", "repo_package", "file", "copy", "template",
    "lineinfile", "blockinfile", "replace", "assemble", "apt", "dnf", "package", "user", "group",
}  # fmt: skip
# (file, task name) -> why it stays out of the preview.
CHECK_GATED_OK = {
    ("roles/build_record/tasks/main.yml",
     "Mark the install incomplete until it is redone ({{ build_record_path | basename }})"):
        "bookkeeping of a build the preview did not run",
    ("roles/deploy_record/tasks/main.yml", "Record this deploy on the host"): "a preview deploys nothing",
    ("roles/enroot/tasks/redhat.yml", "RHEL | install enroot from the release .rpm"):
        "the package file is fetched only by the run",
    ("roles/enroot/tasks/ubuntu.yml", "Ubuntu | install enroot from the release .deb"):
        "the package file is fetched only by the run",
    ("roles/nvidia_cuda_toolkit/tasks/present.yml", "Generate one Lmod modulefile per CUDA minor"):
        "a modulefile reads the toolkit the run installs",
    ("roles/internal_ca/tasks/cert.yml", "Install the certificate"):
        "the certificate is signed for the key the run creates",
    ("roles/open_ondemand/tasks/absent.yml", "Create the backup directory"):
        "the archive of the configuration is data the run keeps, not a change it previews",
    ("roles/sshd/tasks/main.yml", "Move ssh.socket to the new ports and addresses"):
        "the ports are read from the configuration the run writes (sshd -T)",
    ("roles/sshd/tasks/main.yml", "Reload sshd"): "the ports are read from the configuration the run writes",
    ("roles/package_repos/tasks/repo.yml", "Refresh the package index"):
        "repo_package previews installs from an unrefreshed index (package_repos_unrefreshed)",
}  # fmt: skip
PLAIN_CHANGE_OK = {
    "roles/nvidia-dgx-firmware/tasks/check-firmware.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx-firmware/tasks/clean.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx-firmware/tasks/get-data.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx-firmware/tasks/get-health.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx-firmware/tasks/get-ib.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx-firmware/tasks/get-time.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx-firmware/tasks/load-image.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx-firmware/tasks/transfer-logs.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx-firmware/tasks/update-firmware.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx-firmware/tasks/verify-firmware.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx/tasks/configure-raid.yml": UPSTREAM_DGX,
    "roles/nvidia-dgx/tasks/redhat-el8-plus.yml": UPSTREAM_DGX,
}
# file -> why a secret on its command line is acceptable
SECRET_ARGV_OK = {
    "roles/maas-controller/tasks/main.yml": "maas createadmin takes the password only as an argument; "
    "run once on the operator's own MAAS host",
}


def lines_of(path):
    """(number, line) of a file without its comments: YAML # lines and trailing
    "  # ..." comments, and Jinja {# #}."""
    text = re.sub(
        r"\{#.*?#\}", lambda m: "\n" * m.group().count("\n"), path.read_text(errors="ignore"), flags=re.S
    )
    for number, line in enumerate(text.splitlines(), 1):
        if not line.lstrip().startswith("#"):
            yield number, re.sub(r"\s+#\s.*$", "", line) if path.suffix in (".yml", ".yaml") else line


class TaskRules(unittest.TestCase):
    def test_no_secret_on_a_command_line(self):
        scan, offenders = Scan(SECRET_ARGV_OK, MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                name, args = module(task)
                on_argv = (
                    command_line(args) + " " + str(args.get("responses", "")) if name in COMMANDS else ""
                )
                # Ansible puts a task's `environment:` on the module's command line
                # (`VAR=value python3 ...`), whatever the module, no_log or not.
                if SECRET.search(on_argv + " " + str(task.get("environment", ""))):
                    if not scan.excuse(rel(path)):
                        offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertEqual(offenders, [], "pass the secret on stdin or from a root-only file, never in argv")
        scan.check(self)

    def test_root_never_works_under_a_world_writable_directory(self):
        scan, offenders = Scan(WORLD_WRITABLE_OK, MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files() + role_var_files()):
            hits = [n for n, line in lines_of(path) if WORLD_WRITABLE.search(line)]
            if hits and not scan.excuse(rel(path)):
                offenders += [f"{rel(path)}:{n}" for n in hits]
        self.assertEqual(offenders, [], "use /var/cache/deepops, never /tmp, /var/tmp or /dev/shm")
        scan.check(self)

    def test_facts_through_ansible_facts(self):
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, fact_files()):
            offenders += [
                f"{rel(path)}:{n}"
                for n, line in lines_of(path)
                if INJECTED.search(line) or INJECTED_INDIRECT.search(line)
            ]
        self.assertEqual(offenders, [], "read these through ansible_facts")
        scan.check(self)

    def test_the_rules_see_what_they_claim(self):
        for bad in ('when: ""x.service" in services"', "{{ ansible_distribution }}", "x: packages['a']"):
            self.assertTrue(INJECTED.search(bad), bad)
        for bad in ("hostvars[h]['ansible_hostname']", "hostvars[h]['ansible_default_' + p]"):
            self.assertTrue(INJECTED_INDIRECT.search(bad), bad)
        for good in ("hostvars[h]['ansible_facts']['fqdn']", "hostvars[h]['ansible_host']",
                     "ansible_python_interpreter: /usr/bin/python3"):  # fmt: skip
            self.assertFalse(INJECTED.search(good) or INJECTED_INDIRECT.search(good), good)
        for good in ("when: '\"x\" in ansible_facts.services'", "{{ ansible_facts.distribution }}",
                     "ansible_check_mode", "ansible_facts['packages']", "Gather service facts",
                     "ansible.builtin.setup: filter=ansible_local", "filter: ansible_local"):  # fmt: skip
            self.assertFalse(INJECTED.search(good), good)
        for bad in (
            "chdir: /tmp",
            "cd /tmp && make",
            "dest: '/var/tmp/x'",
            "rm /tmp/{{ f }}",
            "src: /dev/shm/a",
        ):
            self.assertTrue(WORLD_WRITABLE.search(bad), bad)
        for good in ("PrivateTmp=true", "/var/cache/deepops/tmp", "tmpfs", "{{ x }}/tmp"):
            self.assertFalse(WORLD_WRITABLE.search(good), good)
        for bad in ("reboot", "sync\nreboot", "sleep 2 && reboot", "systemctl --no-block reboot",
                    "shutdown --reboot now", "/sbin/reboot -f"):  # fmt: skip
            self.assertTrue(REBOOT_COMMAND.search(bad), bad)
        for good in ("reboot-required", "echo rebooting later", "dmesg | grep reboot_reason"):
            self.assertFalse(REBOOT_COMMAND.search(good), good)

    def test_read_only_commands_run_under_check(self):
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                name, _ = module(task)
                if (
                    name in COMMANDS
                    and str(task.get("changed_when", "")).lower() == "false"
                    and "register" in task
                    and task.get("check_mode") is not False
                    and "not ansible_check_mode" not in str(task.get("when", ""))
                ):
                    offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertEqual(offenders, [], "add check_mode: false to these read-only tasks")
        scan.check(self)

    def test_changes_run_through_change_command(self):
        scan, offenders = Scan(PLAIN_CHANGE_OK, MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                name, args = module(task)
                if (
                    name in ("command", "shell")
                    and str(task.get("changed_when", "")).lower() != "false"
                    and task.get("check_mode") is not False
                    and not {"creates", "removes"} & set(args)
                    and not scan.excuse(rel(path))
                ):
                    offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertEqual(
            offenders, [], "a change runs as change_command or change_shell; a read sets changed_when: false"
        )
        scan.check(self)

    def test_a_previewing_task_runs_under_check(self):
        scan, offenders = Scan(CHECK_GATED_OK, MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task, conds in conditioned_tasks(path):
                name, _ = module(task)
                text = " and ".join(conds)
                # Ansible refuses async under --check, so an async change has a twin
                # that previews it (roles/netplan apply.yml).
                if (
                    name in PREVIEWING
                    and "async" not in task
                    and re.search(r"\bnot ansible_check_mode\b", text)
                    and not scan.excuse((rel(path), task.get("name")))
                ):
                    offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertEqual(
            offenders,
            [],
            "these preview themselves: no `not ansible_check_mode` in their when (rule 27)",
        )
        scan.check(self)

    def test_a_read_never_runs_a_changing_command(self):
        scan, offenders = Scan(PLAIN_CHANGE_OK, MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                name, args = module(task)
                if (
                    name in ("command", "shell")
                    and str(task.get("changed_when", "")).lower() == "false"
                    and MUTATES.search(command_line(args))
                    and not scan.excuse(rel(path))
                ):
                    offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertEqual(offenders, [], "a command that changes the host is a change_command, not a read")

    def test_a_change_reads_its_result_only_when_it_ran(self):
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                name, _ = module(task)
                register = task.get("register")
                if name not in CHANGES or not register:
                    continue
                for key, guard in CHECK_GUARDS.items():
                    value = task.get(key)
                    text = " and ".join(map(str, value)) if isinstance(value, list) else str(value or "")
                    if re.search(rf"\b{register}\s*[.\[]", text) and not text.strip().startswith(guard):
                        offenders.append(f"{rel(path)}: {task.get('name')} ({key})")
        self.assertEqual(offenders, [], "under --check nothing ran: guard these conditions as rule 19 says")
        scan.check(self)

    def test_secret_copies_hide_their_diff(self):
        scan, offenders, seen = Scan(minimum=MINIMUM_FILES), [], 0
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                name, args = module(task)
                if name != "copy":
                    continue
                files = " ".join(str(args.get(k, "")) for k in ("src", "dest"))
                if SECRET_FILE.search(files) or SECRET.search(str(args.get("content", ""))):
                    seen += 1
                    if task.get("diff") is not False and task.get("no_log") is not True:
                        offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertGreater(seen, 3, "the scan finds the copies of secrets")
        self.assertEqual(offenders, [], "add diff: false to these copy tasks")
        scan.check(self)

    def test_secret_templates_hide_their_diff(self):
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                name, args = module(task)
                if name not in TEMPLATES or task.get("diff") is False:
                    continue
                src = template_path(path, str(args.get("src", "")))
                if src and SECRET.search(src.read_text()):
                    offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertEqual(offenders, [], "add diff: false to these template tasks")
        scan.check(self)

    def test_no_internal_vars_dictionary(self):
        pattern = re.compile(r"(?<![\w.])(?:in\s+vars\b|vars\[)")
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, fact_files()):
            offenders += [f"{rel(path)}:{n}" for n, line in lines_of(path) if pattern.search(line)]
        self.assertEqual(offenders, [], "the internal vars dictionary is deprecated")
        scan.check(self)

    def test_play_hosts_use_only_hostlist(self):
        scan, offenders = Scan(minimum=20), []
        for path in map(scan.seen, tracked("playbooks/*.y*ml", "playbooks/**/*.y*ml")):
            data = yaml.safe_load(path.read_text())
            for play in data if isinstance(data, list) else []:
                if not isinstance(play, dict):
                    continue
                values = [play.get("hosts"), (play.get("vars") or {}).get("hostlist")]
                for value in filter(None, values):
                    names = set(re.findall(r"\{\{(.*?)\}\}", str(value)))
                    code = [re.sub(r"'[^']*'|\"[^\"]*\"", "", expr) for expr in names]
                    words = {w for expr in code for w in re.findall(r"[A-Za-z_]\w*", expr)}
                    stray = words - {"hostlist", "default", "groups", "join"}
                    if stray:
                        offenders.append(f"{rel(path)}: {value}")
        self.assertEqual(offenders, [], "play hosts must be inventory groups or hostlist")
        scan.check(self)

    def test_conditions_are_boolean(self):
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                for key in ("when", "failed_when", "changed_when", "until"):
                    conditions = task.get(key)
                    for cond in conditions if isinstance(conditions, list) else [conditions]:
                        if isinstance(cond, str) and not_boolean(cond):
                            offenders.append(f"{rel(path)}: {task.get('name')}: {key}: {cond}")
        self.assertEqual(offenders, [], "compare or cast these conditions to a boolean")
        scan.check(self)

    def test_the_condition_classifier(self):
        for cond in (
            "x | length",
            "x | default('')",
            "r.stdout",
            "r.stdout and r.stdout is version('1', '<')",
            "f['gpus']['count']",
            "x | trim | regex_search('^[0-9]+$')",
            "r.rc",
            "1 + 1",
        ):
            self.assertTrue(not_boolean(cond), cond)
        for cond in (
            "x | length > 0",
            "x | bool",
            "x is defined and x",
            "not r.stdout",
            "r.rc == 0",
            "x is regex('^a')",
            "install_dcgm",
            "a or b | bool",
            "'x' in r.stdout",
        ):
            self.assertFalse(not_boolean(cond), cond)

    def test_downloads_are_verified(self):
        scan, offenders = Scan(SIGNATURE_CHECKED, MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                name, args = module(task)
                where = f"{rel(path)}: {task.get('name')}"
                if name == "get_url" and not args.get("checksum") and not scan.excuse(rel(path)):
                    offenders.append(f"{where}: get_url without checksum")
                source = {
                    "unarchive": args.get("src"),
                    "apt": args.get("deb"),
                    "dnf": args.get("name"),
                    "yum": args.get("name"),
                    "package": args.get("name"),
                    "pip": args.get("name"),
                }.get(name)
                for item in source if isinstance(source, list) else [source]:
                    if isinstance(item, str) and URL.search(item):
                        offenders.append(f"{where}: {name} from {item}")
                if name in COMMANDS and re.search(r"(curl|wget)\b[^|]*\|\s*(ba)?sh\b", command_line(args)):
                    offenders.append(f"{where}: a script piped from the network into a shell")
        self.assertEqual(offenders, [], "fetch through roles/download (a required checksum)")
        scan.check(self)

    def test_the_slurm_deploy_takes_its_targets_from_the_components(self):
        # A component playbook defaults to its own cluster target, so it acts the same
        # run alone or from slurm-cluster.yml (nfs-server.yml alone once made every node
        # an NFS server). The deploy passing a hostlist would write the target twice.
        data = yaml.safe_load((ROOT / "playbooks/slurm-cluster.yml").read_text())
        passed = [
            item["import_playbook"]
            for item in data
            if isinstance(item, dict) and "import_playbook" in item and "hostlist" in (item.get("vars") or {})
        ]
        self.assertGreater(sum(1 for i in data if isinstance(i, dict) and "import_playbook" in i), 20)
        self.assertEqual(passed, [], "give the component playbook this hostlist as its default instead")

    def test_a_loop_item_is_never_tested_for_being_defined(self):
        # In a loop item is always defined: `that: item is not defined` failed every
        # host (package_repos retired.yml). A variable NAMED by item is asked with
        # query('ansible.builtin.varnames', ...) or lookup('ansible.builtin.vars', ...).
        pattern = re.compile(r"\bitem\s+is\s+(not\s+)?defined\b")
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                _, args = module(task)
                for value in (task.get("when"), task.get("failed_when"), args.get("that")):
                    for cond in value if isinstance(value, list) else [value]:
                        if isinstance(cond, str) and pattern.search(cond):
                            offenders.append(f"{rel(path)}: {task.get('name')}: {cond}")
        self.assertEqual(offenders, [], "item is always defined in a loop; test the variable it names")
        scan.check(self)

    def test_tags_on_a_dynamic_include_reach_its_tasks(self):
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                name, args = module(task)
                if name not in ("include_tasks", "include_role"):
                    continue
                tags = task.get("tags") or []
                tags = set(tags if isinstance(tags, list) else [tags]) - {"always"}
                applied = (args.get("apply") or {}).get("tags") or []
                applied = set(applied if isinstance(applied, list) else [applied])
                if tags - applied:
                    offenders.append(f"{rel(path)}: {task.get('name')}: {sorted(tags - applied)}")
        self.assertEqual(offenders, [], "pass the include's tags on with apply: {tags: [...]}")
        scan.check(self)

    def test_scripts_slurmd_runs_call_slurm_by_absolute_path(self):
        # slurmd's PATH lacks a custom slurm_install_prefix: a bare scontrol in an epilog
        # failed there, and a node with residual GPU processes was never drained.
        bare = re.compile(r"(?<![\w/${}.-])(scontrol|squeue|sinfo|sacct|scancel|srun|sbatch)\s")
        scan, offenders = Scan(minimum=10), []
        base = "roles/slurm/templates/etc/slurm"
        for path in tracked(*(f"{base}/{sub}/*" for sub in ("prolog.d", "epilog.d", "shared"))):
            scan.seen(path)
            for number, line in lines_of(path):
                code = re.sub(r"(['\"]).*?\1", "", line)  # messages may name a command
                if bare.search(code):
                    offenders.append(f"{rel(path)}:{number}: {line.strip()}")
        self.assertEqual(offenders, [], "call it as {{ slurm_install_prefix }}/bin/<command>")
        scan.check(self)

    def test_only_the_maintenance_role_reboots(self):
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            in_role = rel(path).startswith("roles/")
            for task in tasks(path):
                where = f"{rel(path)}: {task.get('name')}"
                name, args = module(task)
                if name == "reboot" and rel(path) != REBOOT_STEP:
                    offenders.append(f"{where}: reboot module")
                if (
                    name in ("include_role", "import_role")
                    and args.get("name") == "maintenance"
                    and str(args.get("tasks_from", "")).removesuffix(".yml") == "reboot"
                    and in_role
                ):
                    offenders.append(f"{where}: a role runs the reboot step")
                if name in COMMANDS and REBOOT_COMMAND.search(command_line(args)):
                    offenders.append(f"{where}: reboot command")
        self.assertEqual(offenders, [], "ask with roles/maintenance reboot_request instead")
        scan.check(self)

    def test_the_repository_is_listed_one_way(self):
        # What the tools and tests read is what git tracks (scripts/deepops/repo_files.py).
        # Each listing its own way drifted: copies without -z split on spaces, saw files
        # deleted but not yet staged, or needed their own roles/galaxy exception; and a
        # glob of the tree judged Galaxy's roles and a peer's unfinished file.
        own = {"scripts/deepops/repo_files.py"}
        # Readers of another repository or revision, under their own git environment.
        other_repo = {"scripts/deepops/settings.py", "scripts/deepops/sensitive_guard.py"}
        this = rel(Path(__file__).resolve())
        tree_glob = re.compile(
            r"\b(ROOT|ROLES|REPO)\b[^#\n]*\.(r?glob|iterdir|walk)\(|os\.walk\(\s*(ROOT|ROLES|REPO)\b"
        )
        offenders = []
        for path in tracked("*.py"):
            name = rel(path)
            if name == this or name.startswith("submodules/"):
                continue
            text = path.read_text()
            if '"ls-files"' in text and name not in own | other_repo:
                offenders.append(f"{name}: git ls-files")
            offenders += [f"{name}:{n}: globs the tree" for n, line in enumerate(text.splitlines(), 1)
                          if tree_glob.search(line)]  # fmt: skip
        self.assertEqual(offenders, [], "list files with repo_files.tracked (tests: repo_scan.tracked)")
        self.assertGreater(len(tracked("*.py")), 50)

    def test_a_role_that_adds_a_repository_installs_with_repo_package(self):
        # package_repos adds a repository under --check without indexing it, so apt and
        # dnf did not know its packages and every first-deploy preview failed at the
        # install (Slurm, Docker, the NVIDIA stack). repo_package previews that install
        # instead; a role adding a repository installs every package through it.
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        files = [scan.seen(p) for p in ansible_files()]
        adders = set()
        for path in files:
            for task in tasks(path):
                name, args = module(task)
                if name in ("include_role", "import_role") and args.get("name") == "package_repos":
                    if args.get("tasks_from") in ("repo", "cuda", "epel") and rel(path).startswith("roles/"):
                        adders.add(rel(path).split("/")[1])
        for path in files:
            parts = rel(path).split("/")
            if (
                parts[0] != "roles"
                or parts[1] not in adders
                or parts[2] != "tasks"
                or parts[1] == "package_repos"
            ):
                continue
            for task in tasks(path):
                name, args = module(task)
                if name in ("apt", "dnf", "yum", "package") and "name" in args and not args.get("deb"):
                    if str(args.get("state", "present")) in ("present", "latest", "installed"):
                        offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertGreater(len(adders), 5, "the scan finds the roles that add a repository")
        self.assertEqual(offenders, [], "install with repo_package (plugins/action/repo_package.py)")
        scan.check(self)

    def test_a_docker_api_task_waits_for_docker_in_a_preview(self):
        # A preview installs nothing: on a host without Docker every community.docker
        # task failed the preview of a first deploy. Each is skipped under
        # docker_preview_installs (roles/docker), or under --check altogether.
        scan, offenders, seen = Scan(minimum=MINIMUM_FILES), [], 0
        for path in map(scan.seen, ansible_files()):
            if not rel(path).startswith("roles/") or "/molecule/" in rel(path):
                continue
            for task, conds in conditioned_tasks(path):
                if not any(str(k).startswith("community.docker.") for k in task):
                    continue
                seen += 1
                joined = " ".join(conds)
                if "docker_preview_installs" not in joined and "ansible_check_mode" not in joined:
                    if rel(path) != "roles/component_record/tasks/main.yml":  # asks the API first
                        offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertGreater(seen, 5, "the scan finds the Docker API tasks")
        self.assertEqual(offenders, [], "when: not docker_preview_installs | default(false)")
        scan.check(self)

    def test_only_host_baseline_sets_kernel_settings(self):
        # A sysctl written straight to a file was undone at boot by a later file (a hand
        # line in /etc/sysctl.conf) while the running value looked right; the shared
        # task removes the other assignments and checks the value boot applies.
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                if module(task)[0] == "sysctl" and rel(path) != "roles/host_baseline/tasks/sysctl.yml":
                    offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertEqual(offenders, [], "include_role host_baseline, tasks_from: sysctl")
        scan.check(self)

    def test_no_backup_copies_of_host_files(self):
        scan, offenders = Scan(BACKUP_OK, MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                args = module(task)[1]
                if isinstance(args, dict) and str(args.get("backup", "")).lower() in ("true", "yes"):
                    if not scan.excuse(rel(path)):
                        offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertEqual(offenders, [], "drop backup: true; the repository is the file's history")
        scan.check(self)

    def test_containers_see_host_paths_only_through_binds(self):
        run_volume = re.compile(r"\b(docker|podman)\b[^\n]*\brun\b[^\n]*\s(-v|--volume)(\s|=)")
        argv_volume = re.compile(r"""["'](-v|--volume)["']""")
        offenders, scan = [], Scan(minimum=MINIMUM_FILES)
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                name, args = module(task)
                if name == "docker_container" and isinstance(args, dict) and "volumes" in args:
                    offenders.append(f"{rel(path)}: {task.get('name')}: volumes:")
        texts = [
            p
            for p in tracked("roles", "playbooks", "scripts", "tests")
            if p.suffix in (".yml", ".yaml", ".j2", ".sh", ".py", ".service")
        ]
        for path in texts:
            if path == Path(__file__).resolve():
                continue
            text = path.read_text(errors="replace")
            offenders += [f"{rel(path)}: {m.group(0)[:80]}" for m in run_volume.finditer(text)]
            if path.suffix == ".py" and re.search(r"\bdocker\b", text):
                offenders += [f"{rel(path)}: {m.group(0)}" for m in argv_volume.finditer(text)]
        scan.check(self)
        self.assertEqual(offenders, [], "bind with --mount type=bind,src=,dst= (docker_container: mounts:)")

    def test_deepops_directories_are_named_once(self):
        literal = re.compile(r"/var/(lib|cache)/deepops\b")
        offenders, scan = [], Scan(minimum=MINIMUM_FILES)
        for path in map(scan.seen, tracked("roles/**/*.yml", "roles/**/*.j2", "playbooks/**/*.yml")):
            r = rel(path)
            if r == "roles/deepops_paths/defaults/main.yml" or "/molecule/" in r:
                continue
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if literal.search(line) and not line.lstrip().startswith("#"):
                    offenders.append(f"{r}:{n}")
        scan.check(self)
        self.assertEqual(offenders, [], "name them through deepops_state_dir / deepops_cache_dir")

    def test_a_skippable_task_never_delegates_to_an_empty_host(self):
        # Settings whose default may render empty: "" or a `default('')` fallback.
        maybe_empty = set()
        for path in role_var_files():
            for key, value in (yaml.safe_load(path.read_text()) or {}).items():
                if value == "" or (
                    isinstance(value, str) and re.search(r"default\(\s*(''|\"\")\s*\)\s*\}\}\s*$", value)
                ):
                    maybe_empty.add(key)
        # (file, task): an assert earlier in the file stops on an empty host first.
        asserted = {
            ("roles/internal_ca/tasks/ca.yml", "Make the CA certificate"),
            ("roles/nginx-docker-registry-cache-client/tasks/present.yml", "Read the proxy's CA certificate from the cache host"),
        }  # fmt: skip
        offenders, scan = [], Scan(asserted, minimum=MINIMUM_FILES)
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                target = task.get("delegate_to")
                named = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", str(target or "").strip())
                if named and named.group(1) in maybe_empty and "when" in task:
                    if scan.excuse((rel(path), task.get("name"))):
                        continue
                    offenders.append(f"{rel(path)}: {task.get('name')}: {target}")
        scan.check(self)
        self.assertEqual(offenders, [], "delegate to a value that falls back to a host")

    def test_no_handler_includes_a_role(self):
        # ansible-core refuses include_role in a handler, also through an included task
        # file, and only when the handler runs: a reboot request there failed on the
        # rare run that needed it. Act on the registered result in a task instead.
        offenders = []
        for path in tracked("roles/*/handlers/*.yml", "roles/*/handlers/*.yaml"):
            role_tasks = path.parents[1] / "tasks"
            for task in tasks(path):
                name, args = module(task)
                where = f"{rel(path)}: {task.get('name')}"
                if name in ("include_role", "import_role"):
                    offenders.append(where)
                elif name in ("include_tasks", "import_tasks"):
                    target = args.get("file", args.get("_raw_params", ""))
                    included = role_tasks / str(target)
                    if included.is_file() and any(
                        module(t)[0] in ("include_role", "import_role") for t in tasks(included)
                    ):
                        offenders.append(f"{where}: through {target}")
        self.assertEqual(offenders, [], "a handler cannot include a role")

    def test_script_output_loops_drop_blank_lines(self):
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            found = list(tasks(path))
            registered = {t.get("register") for t in found if module(t)[0] == "script" and t.get("register")}
            for task in found:
                loop = str(task.get("loop", ""))
                for var in registered:
                    if re.search(rf"\b{re.escape(var)}\.stdout_lines\b", loop) and "select" not in loop:
                        offenders.append(f"{rel(path)}: {task.get('name')}: loops over {var}.stdout_lines")
        self.assertEqual(offenders, [], "filter the lines: | map('trim') | select | list")
        scan.check(self)

    def test_dpkg_status_is_read_by_the_package_state(self):
        wanted = re.compile(r"""\^ii\b|\^\[[a-z]+\]i\b|['"]ii['" ]|match\(['"]\[[a-z]+\]i['"]""")
        offenders = []
        for path in tracked("roles"):
            if path.suffix in ("", ".yml", ".sh", ".j2", ".py") and "molecule" not in path.parts:
                text = path.read_text(errors="ignore")
                if "Status-Abbrev" in text or "dpkg-query" in text:
                    offenders += [f"{rel(path)}: {m.group(0)}" for m in wanted.finditer(text)]
        self.assertEqual(offenders, [], "installed is the second letter: '.i', [1:2] == 'i'")

    def test_inventory_groups_are_named_not_held_in_settings(self):
        inventory = (ROOT / "config.example" / "inventory").read_text()
        groups = set(re.findall(r"^\[([\w.-]+?)(?::children)?\]", inventory, re.M)) - {"all"}
        self.assertIn("slurm-node", groups, "config.example/inventory lost its groups")
        scan, offenders = Scan(minimum=50), []
        for path in map(scan.seen, role_var_files()):
            data = yaml.safe_load(path.read_text()) or {}
            for key, value in data.items() if isinstance(data, dict) else ():
                if isinstance(value, str) and value.strip() in groups:
                    offenders.append(f"{rel(path)}: {key}: {value}")
        self.assertEqual(offenders, [], "name the group itself (groups['<name>']), see rule 17")
        scan.check(self)

    def test_files_are_found_through_find_present(self):
        offenders = []
        for path in ansible_files():
            if rel(path).startswith("roles/nvidia-dgx"):
                continue
            offenders += [f"{rel(path)}: {t.get('name')}" for t in tasks(path) if module(t)[0] == "find"]
        self.assertEqual(offenders, [], "use find_present (plugins/action/find_present.py)")

    def test_package_listings_name_the_architecture(self):
        offenders = [
            f"{rel(path)}:{number}"
            for path in ansible_files()
            if not rel(path).startswith("roles/nvidia-dgx")
            for number, line in lines_of(path)
            if "dpkg-query" in path.read_text() and "${Package}" in line
        ]
        self.assertEqual(offenders, [], "use ${binary:Package}, which keeps :i386")

    def test_only_deepops_hosts_writes_etc_hosts(self):
        offenders = []
        for path in ansible_files():
            if rel(path).startswith(("roles/deepops_hosts/", "roles/nvidia-dgx")):
                continue
            for task in tasks(path):
                _, args = module(task)
                target = str(args.get("path") or args.get("dest") or "")
                if target in ("/etc/hosts", "{{ hosts_file }}"):
                    offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertEqual(offenders, [], "add lines through deepops_hosts tasks_from: fragment")

    def test_units_go_through_systemd_unit(self):
        offenders = []
        for path in ansible_files():
            if rel(path).startswith("roles/nvidia-dgx"):
                continue  # NVIDIA's DGX roles are kept as shipped
            offenders += [f"{rel(path)}: {t.get('name')}" for t in tasks(path) if module(t)[0] in SERVICES]
        self.assertEqual(offenders, [], "use systemd_unit (plugins/action/systemd_unit.py)")

    def test_scratch_exists_in_a_preview(self):
        scan, offenders, seen = Scan(minimum=MINIMUM_FILES), [], 0
        for path in map(scan.seen, ansible_files()):
            found = list(tasks(path))
            made = {t.get("register") for t in found if module(t)[0] == "tempfile"}
            for task in found:
                name, args = module(task)
                removes = (
                    name == "file"
                    and str(args.get("state")) == "absent"
                    and any(f"{r}.path" in str(args.get("path", "")) for r in made if r)
                )
                if name == "tempfile" or removes:
                    seen += 1
                    if task.get("check_mode") is not False:
                        offenders.append(f"{rel(path)}: {task.get('name')}")
        self.assertGreaterEqual(seen, 4, "the scan finds the tempfile tasks and their removals")
        self.assertEqual(offenders, [], "set check_mode: false (rule 28)")
        scan.check(self)

    def test_modules_run_commands_bounded(self):
        spawn = {"run", "Popen", "call", "check_call", "check_output"}
        offenders = []
        for path in tracked("roles/*/library/*.py", "plugins/modules/*.py", "plugins/module_utils/*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(("roles/nvidia-dgx", "roles/galaxy/")) or rel.endswith("/deepops_command.py"):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                f = node.func
                unbounded = f.attr == "run_command" or (
                    f.attr in spawn
                    and isinstance(f.value, ast.Name)
                    and f.value.id == "subprocess"
                    and not any(k.arg == "timeout" for k in node.keywords)
                )
                if unbounded:
                    offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(offenders, [], "run commands with deepops_command.run (rule 26)")

    def test_modules_say_what_they_change(self):
        offenders = []
        for path in tracked("roles/*/library/*.py", "plugins/modules/*.py"):
            rel = path.relative_to(ROOT).as_posix()
            text = path.read_text()
            if rel.startswith("roles/nvidia-dgx") or "supports_check_mode=True" not in text:
                continue
            if not re.search(
                r"^from ansible\.module_utils import .*\bdeepops_diff\b", text, re.M
            ) or not re.search(r'\bdiff=|\["diff"\]', text):
                offenders.append(rel)
        self.assertEqual(offenders, [], "return diff=deepops_diff.text(...) or .actions(...) when changed")

    def test_conditions_spell_only_known_escapes(self):
        scan, offenders = Scan(minimum=MINIMUM_FILES), []
        for path in map(scan.seen, ansible_files()):
            for task in tasks(path):
                for cond in condition_strings(task):
                    if invalid_escapes(cond):
                        offenders.append(f"{rel(path)}: {task.get('name')}: {cond[:80]}")
        self.assertEqual(offenders, [], "double the backslash: a condition's string literals are unescaped")
        scan.check(self)
        self.assertEqual(invalid_escapes(r"x is match('^[^@\s]+$')"), [r"\s"])
        self.assertEqual(invalid_escapes(r"x is match('^[^@\\s]+\t$')"), [])

    def test_templates_in_yaml_hold_single_backslashes(self):
        jinja = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
        offenders = []

        def walk(value, where):
            if isinstance(value, dict):
                for item in value.values():
                    walk(item, where)
            elif isinstance(value, list):
                for item in value:
                    walk(item, where)
            elif isinstance(value, str):
                offenders.extend(
                    f"{where}: {m.group(0)[:80]}" for m in jinja.finditer(value) if "\\\\" in m.group(0)
                )

        scan = Scan(minimum=MINIMUM_FILES)
        variables = role_var_files()
        for path in [scan.seen(p) for p in ansible_files()] + variables:
            for doc in yaml.safe_load_all(path.read_text()):
                walk(doc, rel(path))
        scan.check(self)
        self.assertEqual(offenders, [], "write one backslash: YAML keeps it, ansible passes it on as is")


if __name__ == "__main__":
    unittest.main()
