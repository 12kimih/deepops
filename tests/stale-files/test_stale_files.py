"""roles/stale_files/library/stale_files.py: each kind of evidence, on a scratch tree."""

import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

mod = in_tree.load("roles/stale_files/library/stale_files.py")


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def put(self, rel, text="", mode=0o644):
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
        return str(path)

    def test_a_perl_packlist_takes_its_files_and_a_perllocal_only_it_wrote(self):
        pm = self.put("perl/5.38.2/Slurm.pm")
        pack = self.put("perl/5.38.2/auto/Slurm/.packlist", pm + "\n")
        pod = self.put("perl/5.38.2/perllocal.pod", "=head2 Thu: C<Module> L<Slurm|Slurm>\n")
        found = mod.match_rule(
            {"type": "packlist", "globs": [str(self.tmp / "perl/*/auto/Slurm*/.packlist")]}
        )
        self.assertEqual(sorted(found), sorted([pm, pack, pod]))
        self.put(
            "perl/5.38.2/perllocal.pod", "=head2 C<Module> L<Slurm|Slurm>\n=head2 C<Module> L<Other|Other>\n"
        )
        found = mod.match_rule(
            {"type": "packlist", "globs": [str(self.tmp / "perl/*/auto/Slurm*/.packlist")]}
        )
        self.assertNotIn(pod, found)

    def test_a_libtool_archive_goes_only_once_its_library_is_gone(self):
        la = self.put("lib/libpmi.la", "dlname='libpmi.so.0'\nold_library='libpmi.a'\n")
        a = self.put("lib/libpmi.a")
        rule = {"type": "libtool", "globs": [str(self.tmp / "lib/*.la")]}
        self.assertEqual(mod.match_rule(rule), sorted([la, a]))
        self.put("lib/libpmi.so.0")
        self.assertEqual(mod.match_rule(rule), [])

    def test_a_program_linked_to_a_library_is_told_by_its_dynamic_section(self):
        true = shutil.which("true")  # an ELF everyone has; it needs libc
        os.makedirs(self.tmp / "bin")
        shutil.copy(true, self.tmp / "bin" / "prog")
        self.put("bin/script", "#!/bin/sh\nlibc.so.6\n")
        self.assertEqual(
            mod.match_rule({"type": "links", "dirs": [str(self.tmp / "bin")], "needed": r"^libc\.so"}),
            [str(self.tmp / "bin" / "prog")],
        )
        self.assertEqual(
            mod.match_rule({"type": "links", "dirs": [str(self.tmp / "bin")], "needed": "^libslurm"}), []
        )

    def test_a_script_needs_its_name_and_what_it_says(self):
        qsub = self.put("bin/qsub", "#!/usr/bin/perl\nuse Slurm;\n", 0o755)
        self.put("bin/mpiexec", '#!/bin/sh\nexec orterun "$@"\n', 0o755)
        self.put("bin/sq", "#!/usr/bin/python3\nslurm\n", 0o755)
        rule = {
            "type": "scripts",
            "dirs": [str(self.tmp / "bin")],
            "names": ["qsub", "mpiexec"],
            "contains": "(?i)slurm",
        }
        self.assertEqual(mod.match_rule(rule), [qsub])

    def test_a_runtime_whose_interpreter_is_gone_takes_its_scripts_and_records(self):
        tree = self.tmp / "lib/python3.10"
        record_dir = tree / "dist-packages/charset_normalizer-3.4.2.dist-info"
        normalizer = self.put("bin/normalizer", "#!/usr/bin/python3\n", 0o755)
        self.put(str(record_dir.relative_to(self.tmp) / "RECORD"), "../../../bin/normalizer,sha256=x,1\n")
        rails = self.put("bin/rails", "#!/usr/bin/env python3.10\n", 0o755)
        self.put("bin/other", "#!/usr/bin/env python3\n", 0o755)
        rule = {
            "type": "runtime",
            "glob": str(self.tmp / "lib/python3.*"),
            "version": r"python(\d+\.\d+)$",
            "interpreter": str(self.tmp / "no/python{0}"),
            "dirs": [str(self.tmp / "bin")],
        }
        self.assertEqual(mod.match_rule(rule), sorted([str(tree), normalizer, rails]))
        rule["interpreter"] = shutil.which("sh") + "{0}"[:0]  # an interpreter that exists
        self.assertEqual(mod.match_rule(rule), [])

    def test_nothing_a_package_owns_is_counted(self):
        owned = {"/usr/lib/x/libfoo.so"}
        self.assertTrue(mod.owned_below("/usr/lib/x", owned))
        self.assertFalse(mod.owned_below("/usr/lib/y", owned))

    def test_removal_prunes_only_below_its_bound(self):
        # Unbounded, it walked up and removed every directory it emptied, the scratch
        # directory itself here, /usr/local/src on a host.
        path = self.put("a/b/c/file")
        mod.remove(path)
        self.assertTrue((self.tmp / "a/b/c").is_dir())
        mod.remove(self.put("perl/5.38.2/auto/Slurm/Slurm.so"), (str(self.tmp / "perl/5.38.2"),))
        self.assertFalse((self.tmp / "perl/5.38.2/auto").exists())
        self.assertTrue((self.tmp / "perl/5.38.2").is_dir())

    def test_a_script_rule_without_names_is_refused(self):
        with self.assertRaises(ValueError):
            mod.match_rule({"type": "scripts", "dirs": [str(self.tmp)], "names": [], "contains": "x"})

    def test_a_named_file_needs_its_name_and_what_it_says(self):
        hit = self.put(
            "apt/zzzz-temp-installer-unattended-upgrade", "# for the run to avoid failing on battery\n"
        )
        self.put("apt/zzzz-temp-installer-unattended-upgrade.mine", "avoid failing on battery\n")
        self.put("apt/other/zzzz-temp-installer-unattended-upgrade", "a site's own file\n")
        rule = {
            "type": "files",
            "dirs": [str(self.tmp / "apt")],
            "pattern": "zzzz-temp-installer-unattended-upgrade",
            "contains": "avoid failing on battery",
        }
        self.assertEqual(mod.match_rule(rule), [hit])

    def test_ansible_backup_copies_are_told_by_their_name(self):
        name = next(
            r["pattern"]
            for r in __import__("yaml").safe_load((ROOT / "roles/stale_files/defaults/main.yml").read_text())[
                "stale_files_host_rules"
            ]
            if r["type"] == "files" and "@" in r["pattern"]
        )
        copy = self.put("etc/default/grub.3831733.2026-09-04@19:35:39~")
        self.put("etc/default/grub")
        self.put("etc/default/grub~")  # an editor's, not Ansible's
        self.put("etc/default/grub.ucf-dist")
        rule = {"type": "files", "dirs": [str(self.tmp / "etc")], "pattern": name}
        self.assertEqual(mod.match_rule(rule), [copy])

    def test_ucf_kept_configuration_counts_as_owned(self):
        registry = self.put("ucf/registry", "openssh-server \t /etc/ssh/sshd_config\n")
        with mock.patch.object(mod, "UCF_REGISTRY", registry):
            self.assertIn("/etc/ssh/sshd_config", mod.owned_paths())

    def test_a_name_that_is_not_utf8_is_owned_as_the_os_spells_it(self):
        # A package's file named in Latin-1: read with errors="replace" it would not equal
        # the name os.walk yields, count as unowned and be removed by a matching rule.
        name = os.fsencode(str(self.tmp)) + b"/caf\xe9.conf"
        open(name, "wb").close()
        (self.tmp / "ucf").mkdir()
        registry = self.tmp / "ucf" / "registry"
        registry.write_bytes(b"pkg \t " + name + b"\n")
        with mock.patch.object(mod, "UCF_REGISTRY", str(registry)):
            owned = mod.owned_paths()
        walked = [p for p in mod.files_in([str(self.tmp)]) if p.endswith(".conf")]
        self.assertEqual(walked, [os.fsdecode(name)])
        self.assertTrue(mod.owned_below(walked[0], owned))

        # A library path in Latin-1 that exists keeps its ld.so.conf.d file.
        lib = os.fsencode(str(self.tmp)) + b"/lib\xe9"
        os.mkdir(lib)
        (self.tmp / "ld").mkdir()
        (self.tmp / "ld" / "x.conf").write_bytes(lib + b"\n")
        self.assertEqual(mod.match_rule({"type": "ld_conf", "dirs": [str(self.tmp / "ld")]}), [])

    def test_what_a_staged_install_lists_counts_as_owned(self):
        manifests = self.tmp / "source-builds"
        self.put(
            "source-builds/lmod.json",
            '{"files": ["/usr/local/share/zsh/site-functions/ml"], "dirs": ["/opt/lmod"]}',
        )
        self.put("source-builds/lmod.json.tmp", '{"files": ["/elsewhere"]}')
        owned = mod.staged_paths(str(manifests))
        self.assertEqual(owned, {"/usr/local/share/zsh/site-functions/ml", "/opt/lmod"})
        self.assertEqual(mod.staged_paths(str(self.tmp / "absent")), set())
        self.assertEqual(mod.staged_paths(None), set())

    def test_unowned_config_is_listed_and_deepops_files_are_marked(self):
        conf = self.tmp / "profile.d"
        hand = self.put("profile.d/ssh-key.sh", "ssh-keygen\n")
        named = self.put("profile.d/deepops-env.sh", "export X=1\n")
        role = self.put("profile.d/zz-unit.sh", "# roles/nvidia_driver: persistence mode on\n")
        ours = self.put("profile.d/z10-cluster-tools.sh", "# Ansible managed\n")
        owned = self.put("profile.d/bash_completion.sh")
        recorded = self.put("profile.d/recorded.sh")
        self.put("profile.d/claimed/inside.sh")  # a directory: never listed itself
        found = mod.unowned_config([str(conf), str(self.tmp / "missing")], {owned}, ())
        self.assertEqual(
            found, [f"{named} (deepops)", recorded, hand, f"{ours} (deepops)", f"{role} (deepops)"]
        )
        # What a component record claims reaches it as owned (files) or claimed (trees).
        self.assertEqual(
            mod.unowned_config([str(conf)], {owned, recorded}, ()),
            [f"{named} (deepops)", hand, f"{ours} (deepops)", f"{role} (deepops)"],
        )

    def test_unit_overrides_leave_out_enable_links_and_snapd_units(self):
        root = self.tmp / "system"
        copy = self.put("system/docker.service", "[Service]\n")
        dropin = self.put("system/docker.service.d/options.conf", "[Service]\n")
        self.put("system/snap-core22.mount", "[Unit]\nX-Snappy=yes\n")
        (root / "multi-user.target.wants").mkdir()
        (root / "multi-user.target.wants" / "docker.service").symlink_to(copy)
        self.put("system/multi-user.target.wants/stray.service")
        (root / "alias.service").symlink_to(copy)
        ours = self.put("system/nvidia-persistenced.service", "# roles/nvidia_driver: persistence on\n")
        self.assertEqual(mod.unit_overrides(set(), str(root)), sorted([copy, dropin]) + [f"{ours} (deepops)"])
        self.assertEqual(mod.unit_overrides({copy}, str(root)), [dropin, f"{ours} (deepops)"])

    def test_prompt_copies_are_listed_and_package_working_copies_are_not(self):
        root = self.tmp / "etc"
        found = [
            self.put("etc/ssh/sshd_config.ucf-dist"),
            self.put("etc/sudoers.dpkg-old"),
            self.put("etc/fstab.bak"),
        ]
        self.put("etc/ssh/sshd_config")
        with mock.patch.object(mod, "PROMPT_COPY_SKIP", (str(root / "xml") + "/",)):
            self.put("etc/xml/catalog.old")
            self.assertEqual(mod.prompt_copies(str(root)), sorted(found))

    def test_package_state_is_read_from_dpkg(self):
        verify = ["??5??????   /usr/bin/x", "??5?????? c /etc/bash.bashrc", "missing   c /etc/gone"]
        status = ["ii  bash", "rc  linux-image-6.8.0-1-generic", "rc  mariadb-server-10.6"]
        with mock.patch.object(
            mod, "run_lines", side_effect=lambda argv: verify if argv[0] == "dpkg" else status
        ):
            with mock.patch.object(mod.os.path, "isdir", return_value=True):
                self.assertEqual(mod.changed_conffiles(), ["/etc/bash.bashrc"])
            self.assertEqual(mod.removed_packages(), ["linux-image-6.8.0-1-generic", "mariadb-server-10.6"])

    def test_kernel_trees_never_take_the_running_kernel(self):
        running = mod.os.uname().release
        old = self.put("modules/5.15.0-1-generic/modules.dep")
        self.put(f"modules/{running}/modules.dep")
        rule = {"type": "kernel_trees", "dirs": [str(self.tmp / "modules")]}
        self.assertEqual(mod.match_rule(rule), [str(Path(old).parent)])

    def test_a_library_path_goes_once_every_directory_is_gone(self):
        lib = self.tmp / "cuda-12.4" / "lib64"
        lib.mkdir(parents=True)
        gone = self.put("ld/cuda-11-8.conf", f"{self.tmp}/cuda-11.8/lib64\n# a comment\n")
        self.put("ld/cuda-12-4.conf", f"{self.tmp}/cuda-11.8/lib64\n{lib}\n")
        self.put("ld/empty.conf", "# nothing listed\n")
        self.put("ld/README", f"{self.tmp}/nowhere\n")
        rule = {"type": "ld_conf", "dirs": [str(self.tmp / "ld")]}
        self.assertEqual(mod.match_rule(rule), [gone])

    def test_orphan_module_trees_keep_installed_and_running_kernels(self):
        root = self.tmp / "modules"
        running = mod.os.uname().release
        for version in ("5.15.0-1-generic", "6.8.0-2-generic", running):
            self.put(f"modules/{version}/modules.dep")
        owned = {"/lib/modules/6.8.0-2-generic/kernel/x.ko"}
        self.assertEqual(mod.orphan_modules(owned, str(root)), [str(root / "5.15.0-1-generic")])


if __name__ == "__main__":
    unittest.main()
