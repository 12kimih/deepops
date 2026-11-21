"""The bound a test puts on a process it starts (a validator, a CLI, a tool): it guards
against a hang, not speed, so it is sized for a shared, loaded host, where the full check
runs dozens of suites at once (a 5 s bound failed at load average 15, 2026-11). An
Ansible run takes scripts/deepops/unrecorded.py TIMEOUT instead. A test of timeout
handling itself passes its own short value to the code under test, never to subprocess.
tests/python-rules/test_test_bounds.py refuses a literal bound in a test's subprocess call.
"""

CHILD = 600
