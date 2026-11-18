"""Unit tests for clustertools.cli (roles/cluster_tools_lib)."""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import slurm  # noqa: E402
from clustertools.cli import COMPLETERS, Command, Opt, run_main  # noqa: E402

CMD = Command(
    "demo",
    "a demo command",
    [
        Opt("-m", "--me", brief=True, help="only your jobs"),
        Opt("-p", "--partition", metavar="P", complete="partitions", brief=True, help="these partitions"),
        Opt("-U", "--users", negate=True, default=True, help="the user table"),
        Opt("--json", section="Output", help="machine-readable"),
    ],
    env="DEMO_OPTIONS",
    machine="--json",
    notes=["Trailing note."],
    theme_env="DEMO_THEME",
)

ENV = {"LANG": "C.UTF-8"}


def run(argv, env=ENV):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            args, theme = CMD.parse(argv, env)
        except SystemExit as e:
            return e.code, out.getvalue(), None, None
    return None, out.getvalue(), args, theme


class CliTests(unittest.TestCase):
    def test_brief_and_full_help(self):
        code, brief, _, _ = run(["-h"])
        self.assertEqual(code, 0)
        self.assertIn("-p, --partition P", brief)
        self.assertNotIn("--users", brief)
        self.assertIn("More: demo --help", brief)
        _, full, _, _ = run(["--help"])
        self.assertIn("-U, --users, --no-users", full)
        self.assertIn("Style:", full)
        self.assertIn("Trailing note.", full)

    def test_environment_defaults_and_the_last_word(self):
        _, _, args, _ = run([], {**ENV, "DEMO_OPTIONS": "--no-users -p 'a b'"})
        self.assertEqual((args.users, args.partition), (False, "a b"))
        _, _, args, _ = run(["--users"], {**ENV, "DEMO_OPTIONS": "--no-users"})
        self.assertTrue(args.users)

    def test_machine_output_ignores_defaults_and_colour(self):
        _, _, args, theme = run(["--json", "--color", "always"], {**ENV, "DEMO_OPTIONS": "--no-users"})
        self.assertTrue(args.users)
        self.assertFalse(theme.color)

    def test_style_options(self):
        _, _, args, theme = run(["--no-color", "--theme", "latte"])
        self.assertEqual((args.color, theme.flavour, theme.color), ("never", "latte", False))
        _, _, _, theme = run([], {**ENV, "DEMO_THEME": "frappe"})
        self.assertEqual(theme.flavour, "frappe")
        _, _, _, theme = run([], {**ENV, "DEMO_THEME": "frappe", "CLUSTER_THEME": "latte"})
        self.assertEqual(theme.flavour, "latte")

    def test_errors_exit_2(self):
        for argv in (["--bogus"], ["-p"], ["--color", "sometimes"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run(argv)[0], 2)

    def test_a_broken_default_is_a_usage_error_naming_it(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = run([], env={**ENV, "DEMO_OPTIONS": '-p "a10'})[0]
        self.assertEqual(code, 2)
        self.assertIn("$DEMO_OPTIONS: No closing quotation", err.getvalue())

    def test_failures_are_reported_as_what_they_are(self):
        def fails(error):
            def body():
                raise error

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                return run_main("demo", body), err.getvalue().strip()

        self.assertEqual(fails(slurm.Missing("scontrol not found")), (127, "demo: scontrol not found"))
        self.assertEqual(fails(slurm.Failed("scontrol: down")), (1, "demo: scontrol: down"))
        self.assertEqual(fails(PermissionError(13, "Permission denied", "/etc/x.prom")),
                         (1, "demo: /etc/x.prom: Permission denied"))  # fmt: skip
        # A bug of the command's own is not dressed up as Slurm's.
        with self.assertRaises(ValueError):
            run_main("demo", lambda: int("x"))

    def test_refused_options_say_why(self):
        cmd = Command("demo", "x", [], refuse={"-o": "use squeue -o", "--format": "use squeue --format"})
        self.assertIsNone(cmd.refused(["-m", "--", "-o"]))
        for argv in (["-o", "%i"], ["-o%i"], ["--format=%i"], ["--format", "%i"]):
            with self.subTest(argv=argv):
                self.assertIsNotNone(cmd.refused(argv))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            cmd.parse(["-o%i"], ENV)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("-o: use squeue -o", err.getvalue())

    def test_completion_is_generated_from_the_options(self):
        code, script, _, _ = run(["--completion", "bash"])
        self.assertEqual(code, 0)
        self.assertIn("complete -F _demo demo", script)
        self.assertIn("--no-users", script)
        # Wired to the completer as defined, so the test follows the definition.
        self.assertIn(f'-p|--partition) COMPREPLY=($(compgen -W "$({COMPLETERS["partitions"]})"', script)
        self.assertIn("--theme)", script)

    def test_themes_lists_flavours(self):
        code, out, _, _ = run(["--themes"])
        self.assertEqual(code, 0)
        self.assertIn("macchiato", out)


if __name__ == "__main__":
    unittest.main()
