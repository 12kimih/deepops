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
3. No expression reads the internal `vars` dictionary (`x in vars`, `vars[...]`):
   ansible-core 2.20 deprecates it and 2.24 removes it. Test `x is defined`, or use
   the vars/varnames lookups for a computed name.
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = {
    "command",
    "shell",
    "script",
    "ansible.builtin.command",
    "ansible.builtin.shell",
    "ansible.builtin.script",
}
TEMPLATES = {"template", "ansible.builtin.template"}
# Fetched without a fixed checksum because the file changes in place upstream, and
# checked against its publisher's signing key right after instead.
SIGNATURE_CHECKED = {ROOT / "roles/package_repos/tasks/epel.yml"}
URL = re.compile(r"^\s*(https?|ftp)://|^\s*\{\{[^}]*(url|_src)\b")
SECRET = re.compile(
    r"\{\{[^}]*(password|passwd|_token|secret|webhook|api_key|_pass\b|heartbeat_url)[^}]*\}\}"
)


def task_files():
    for path in sorted(ROOT.glob("roles/*/tasks/**/*.yml")) + sorted(ROOT.glob("playbooks/**/*.yml")):
        if "galaxy" not in path.parts:
            yield path


def tasks(path):
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, list):
        return
    plays = (
        data
        if data and isinstance(data[0], dict) and ("hosts" in data[0] or "import_playbook" in data[0])
        else None
    )
    lists = [
        p.get(k) for p in plays or [] if isinstance(p, dict) for k in ("pre_tasks", "tasks", "post_tasks")
    ]
    stack = lists if plays is not None else [data]
    while stack:
        for task in stack.pop() or []:
            if isinstance(task, dict):
                stack += [task.get(k) for k in ("block", "rescue", "always")]
                yield task


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
    r"(?<![\w.'\"\[-])(?<!filter=)(?:(?:in\s+|not\s+in\s+)(?:services|packages)\b|(?:services|packages)\["
    r"|ansible_(?:distribution|os_family|kernel|architecture|processor|memtotal_mb|lsb|pkg_mgr"
    r"|service_mgr|devices|mounts|interfaces|default_ipv[46]|all_ipv[46]_addresses|fqdn|hostname"
    r"|nodename|machine_id|virtualization|system|python(?!_interpreter)|date_time|env|local|product|bios|cmdline"
    r"|selinux|apparmor|user_id|domain|dns|proc_cmdline|uptime_seconds|board)\w*)"
)


# The same facts read by name from hostvars, or by a name built at run time.
INJECTED_INDIRECT = re.compile(
    r"""\[\s*['"]ansible_(?!facts\b|host\b|user\b|port\b|connection\b|become|ssh_|python_interpreter)"""
    r"""|['"]ansible_['"]?\s*[+~]"""
)


def fact_files():
    for part in ("tasks", "handlers", "defaults", "vars"):
        yield from (p for p in sorted(ROOT.glob(f"roles/*/{part}/**/*.yml")) if "galaxy" not in p.parts)
    yield from (p for p in sorted(ROOT.glob("roles/*/templates/**/*.j2")) if "galaxy" not in p.parts)
    yield from sorted(ROOT.glob("playbooks/**/*.yml"))


WORLD_WRITABLE = re.compile(r':\s*"?/(?:tmp|var/tmp|dev/shm)/')
# file -> why its world-writable path is not root's to trust
WORLD_WRITABLE_OK = {
    "roles/enroot/defaults/main.yml": "a per-user directory the job user owns; the prolog makes it as that user",
    "playbooks/slurm-cluster/slurm-validation.yml": "the operator's own output file, written with become: false",
    "roles/nvidia-dgx-firmware/tasks/transfer-logs.yml": "upstream DGX: fetches the dump nvsm itself wrote there",
}


class TaskRules(unittest.TestCase):
    def test_root_never_works_under_a_world_writable_directory(self):
        offenders = [
            f"{path.relative_to(ROOT)}:{number}"
            for path in list(fact_files())
            if str(path.relative_to(ROOT)) not in WORLD_WRITABLE_OK and path.suffix == ".yml"
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if not line.lstrip().startswith("#") and WORLD_WRITABLE.search(line)
        ]
        self.assertEqual(offenders, [], "use /var/cache/deepops, never /tmp, /var/tmp or /dev/shm")

    def test_facts_through_ansible_facts(self):
        def code(path):  # Jinja comments blanked, line numbers kept
            return re.sub(r"\{#.*?#\}", lambda m: "\n" * m.group().count("\n"), path.read_text(), flags=re.S)

        offenders = [
            f"{path.relative_to(ROOT)}:{number}"
            for path in fact_files()
            for number, line in enumerate(code(path).splitlines(), 1)
            if not line.lstrip().startswith("#") and (INJECTED.search(line) or INJECTED_INDIRECT.search(line))
        ]
        self.assertEqual(offenders, [], "read these through ansible_facts")

    def test_the_fact_rule_sees_injected_names(self):
        for bad in ('when: ""x.service" in services"', "{{ ansible_distribution }}", "x: packages['a']"):
            self.assertTrue(INJECTED.search(bad), bad)
        for bad in ("hostvars[h]['ansible_hostname']", "hostvars[h]['ansible_default_' + p]"):
            self.assertTrue(INJECTED_INDIRECT.search(bad), bad)
        for good in ("hostvars[h]['ansible_facts']['fqdn']", "hostvars[h]['ansible_host']",
                     "ansible_python_interpreter: /usr/bin/python3"):  # fmt: skip
            self.assertFalse(INJECTED.search(good) or INJECTED_INDIRECT.search(good), good)
        for good in ("when: '\"x\" in ansible_facts.services'", "{{ ansible_facts.distribution }}",
                     "ansible_check_mode", "ansible_facts['packages']", "Gather service facts",
                     "ansible.builtin.setup: filter=ansible_local"):  # fmt: skip
            self.assertFalse(INJECTED.search(good), good)

    def test_read_only_commands_run_under_check(self):
        offenders = [
            f"{path.relative_to(ROOT)}: {task.get('name')}"
            for path in task_files()
            for task in tasks(path)
            if COMMANDS & set(task)
            and str(task.get("changed_when", "")).lower() == "false"
            and "register" in task
            and "check_mode" not in task
            and "ansible_check_mode" not in str(task.get("when", ""))
        ]
        self.assertEqual(offenders, [], "add check_mode: false to these read-only tasks")

    def test_secret_templates_hide_their_diff(self):
        offenders = []
        for path in task_files():
            for task in tasks(path):
                module = next((task[k] for k in TEMPLATES if k in task), None)
                if not isinstance(module, dict) or task.get("diff") is False:
                    continue
                src = template_path(path, str(module.get("src", "")))
                if src and SECRET.search(src.read_text()):
                    offenders.append(f"{path.relative_to(ROOT)}: {task.get('name')}")
        self.assertEqual(offenders, [], "add diff: false to these template tasks")

    def test_no_internal_vars_dictionary(self):
        pattern = re.compile(r"(?<![\w.])(?:in\s+vars\b|vars\[)")
        offenders = [
            f"{path.relative_to(ROOT)}:{number}"
            for path in list(task_files()) + sorted(ROOT.glob("roles/*/templates/**/*.j2"))
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if not line.lstrip().startswith("#") and pattern.search(line)
        ]
        self.assertEqual(offenders, [], "the internal vars dictionary is deprecated")

    def test_play_hosts_use_only_hostlist(self):
        offenders = []
        for path in sorted(ROOT.glob("playbooks/**/*.yml")):
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
                        offenders.append(f"{path.relative_to(ROOT)}: {value}")
        self.assertEqual(offenders, [], "play hosts must be inventory groups or hostlist")

    def test_conditions_are_boolean(self):
        bare = re.compile(r"\|\s*length\s*$|\|\s*default\((['\"])[^'\"]*\1\)\s*$")
        offenders = []
        for path in task_files():
            for task in tasks(path):
                conditions = task.get("when")
                for cond in conditions if isinstance(conditions, list) else [conditions]:
                    if isinstance(cond, str) and bare.search(cond.strip()):
                        offenders.append(f"{path.relative_to(ROOT)}: {task.get('name')}: {cond}")
        self.assertEqual(offenders, [], "compare or cast these conditions to a boolean")

    def test_downloads_are_verified(self):
        offenders = []
        for path in task_files():
            for task in tasks(path):
                for key, args in task.items():
                    if not isinstance(args, dict):
                        continue
                    module = key.removeprefix("ansible.builtin.")
                    if module == "get_url" and not args.get("checksum") and path not in SIGNATURE_CHECKED:
                        offenders.append(
                            f"{path.relative_to(ROOT)}: {task.get('name')}: get_url without checksum"
                        )
                    source = {
                        "unarchive": args.get("src"),
                        "apt": args.get("deb"),
                        "dnf": args.get("name"),
                    }.get(module)
                    for item in source if isinstance(source, list) else [source]:
                        if isinstance(item, str) and URL.search(item):
                            offenders.append(
                                f"{path.relative_to(ROOT)}: {task.get('name')}: {module} from {item}"
                            )
        self.assertEqual(offenders, [], "fetch through roles/download (a required checksum)")


if __name__ == "__main__":
    unittest.main()
