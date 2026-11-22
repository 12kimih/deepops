"""plugins/module_utils/deepops_command.py: every command an in-tree module runs ends."""

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

in_tree.register_module_utils()
command = sys.modules["ansible.module_utils.deepops_command"]


class Failed(Exception):
    pass


class Module:
    def fail_json(self, msg):
        raise Failed(msg)


class Run(unittest.TestCase):
    def test_output_status_and_stdin(self):
        self.assertEqual(
            command.run(Module(), ["sh", "-c", "cat; echo e >&2; exit 3"], data="in"), (3, "in", "e\n")
        )
        self.assertEqual(command.run(Module(), ["cat"], data=b"\xff")[1], "�")

    def test_a_missing_command_is_127(self):
        rc, out, err = command.run(Module(), ["/nonexistent/tool"])
        self.assertEqual((rc, out), (127, ""))
        self.assertTrue(err)

    def test_the_environment_is_updated_and_names_unset(self):
        os.environ["DEEPOPS_COMMAND_TEST"] = "set"
        self.addCleanup(os.environ.pop, "DEEPOPS_COMMAND_TEST", None)
        argv = ["sh", "-c", 'echo "${DEEPOPS_COMMAND_TEST-unset} $X"']
        self.assertEqual(command.run(Module(), argv, environ_update={"X": "1"})[1], "set 1\n")
        self.assertEqual(
            command.run(Module(), argv, environ_update={"DEEPOPS_COMMAND_TEST": None})[1], "unset \n"
        )

    def test_a_hung_command_and_what_it_started_are_killed(self):
        # The shell's child keeps the pipe open: killing the shell alone would hang.
        start = time.monotonic()
        with self.assertRaisesRegex(Failed, "did not finish within 1s"):
            command.run(Module(), ["sh", "-c", "sleep 30 & sleep 30"], timeout=1)
        self.assertLess(time.monotonic() - start, 10)


if __name__ == "__main__":
    unittest.main()
