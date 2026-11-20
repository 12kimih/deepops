"""roles/stale_files/library/stale_files.py: each kind of evidence, on a scratch tree."""

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "stale_files", ROOT / "roles" / "stale_files" / "library" / "stale_files.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


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


if __name__ == "__main__":
    unittest.main()
