"""Repository-wide rules for tasks that keep `--check --diff` previews correct and safe.

1. A command/shell/script task that only reads (changed_when: false) and registers a
   result must set check_mode: false, unless its `when` skips it under --check on
   purpose (`not ansible_check_mode`). Under --check Ansible skips commands, so the
   registered result is empty and every task that reads it fails or decides wrongly.
2. A template task whose template renders a secret must set diff: false, or a
   --diff preview prints the secret.
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
9. No secret is on a command line: argv is readable by every user on the host (ps).
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
3. No expression reads the internal `vars` dictionary (`x in vars`, `vars[...]`):
   ansible-core 2.20 deprecates it and 2.24 removes it. Test `x is defined`, or use
   the vars/varnames lookups for a computed name.
"""

import re
import sys
import unittest
from pathlib import Path

import jinja2
import yaml
from jinja2 import nodes

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import Scan, ansible_files, module, role_var_files, tasks, tracked  # noqa: E402

COMMANDS = {"command", "shell", "script", "raw", "expect"}
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
    r"\{\{[^}]*(password|passwd|_token|\btoken\b|secret|webhook|api_key|_pass\b|heartbeat_url|munge_key)[^}]*\}\}"
)
MINIMUM_FILES = 200  # a scan that sees fewer has lost its way


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
}
# file -> why it keeps Ansible's backup copy
BACKUP_OK = {
    "roles/netplan/tasks/main.yml": "puts back a file netplan rejects, then removes the copy (always:)",
    "roles/sshd/tasks/main.yml": "puts back what sshd -t rejects, then removes the copies (always:)",
    "playbooks/k8s-cluster.yml": "the operator's own ~/.kube/config on the controller, not a host file",
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
                if name in COMMANDS and SECRET.search(
                    command_line(args) + " " + str(args.get("responses", ""))
                ):
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
        base = ROOT / "roles" / "slurm" / "templates" / "etc" / "slurm"
        for path in sorted(p for sub in ("prolog.d", "epilog.d", "shared") for p in (base / sub).rglob("*")):
            if not path.is_file():
                continue
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

    def test_suites_list_files_through_repo_scan(self):
        # A suite running git ls-files itself sees files deleted but not yet staged and
        # fails on them; repo_scan.tracked is the one listing (tracked and present).
        own = ROOT / "tests" / "lib" / "repo_scan.py"
        offenders = [
            rel(path)
            for path in tracked("tests/*.py", "scripts/validation/tests/*.py")
            if path != own and path.name != Path(__file__).name and '"ls-files"' in path.read_text()
        ]
        self.assertEqual(offenders, [], "use tests/lib/repo_scan.py tracked()")

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
            for top in ("roles", "playbooks", "scripts", "tests")
            for p in (ROOT / top).rglob("*")
            if p.is_file() and p.suffix in (".yml", ".yaml", ".j2", ".sh", ".py", ".service")
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
        for path in sorted(ROOT.glob("roles/*/handlers/*.yml")):
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
        for path in sorted(ROOT.glob("roles/**/*")):
            if (
                path.is_file()
                and path.suffix in ("", ".yml", ".sh", ".j2", ".py")
                and "molecule" not in path.parts
            ):
                text = path.read_text(errors="ignore")
                if "Status-Abbrev" in text or "dpkg-query" in text:
                    offenders += [f"{rel(path)}: {m.group(0)}" for m in wanted.finditer(text)]
        self.assertEqual(offenders, [], "installed is the second letter: '.i', [1:2] == 'i'")

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
        variables = sorted(ROOT.glob("roles/*/defaults/*.yml")) + sorted(ROOT.glob("roles/*/vars/*.yml"))
        for path in [scan.seen(p) for p in ansible_files()] + variables:
            for doc in yaml.safe_load_all(path.read_text()):
                walk(doc, rel(path))
        scan.check(self)
        self.assertEqual(offenders, [], "write one backslash: YAML keeps it, ansible passes it on as is")


if __name__ == "__main__":
    unittest.main()
