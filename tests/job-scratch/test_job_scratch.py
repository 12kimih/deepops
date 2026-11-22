"""roles/job_scratch's decisions, evaluated as the tasks state them against made-up
readings of a host: the image's size (job_scratch_size at most, what the filesystem
has room for beyond the reserve, never less than the image has), when it may be
created or grown, and when the first mount must wait for the node's jobs to end. No
host is contacted."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from ansible_yaml import condition, load, render, role_defaults, same_name  # noqa: E402

TASKS = load("roles/job_scratch/tasks/present.yml")
DEFAULTS = role_defaults("job_scratch")
GIB = 1024**3
TB2 = 2 * 1000**4  # a "2 TB" disk


def task(name):
    found = [t for t in TASKS if same_name(t.get("name"), name)]
    assert len(found) == 1, name
    return found[0]


def reading(size=TB2, avail=TB2 - 30 * GIB, image=0, mounted="- -", occupied="", **settings):
    """The decisions for a host whose filesystem, image and job directories read so."""
    values = {
        **DEFAULTS,
        **settings,
        "job_scratch_host_fs": {"stdout": f"{size} {avail}"},
        "job_scratch_found": {"stdout_lines": [str(image), mounted] + ([occupied] if occupied else [])},
    }
    work = task("Work out the image's size")
    values.update(render(work["vars"], values))
    values["_job_scratch"] = render(work["set_fact"]["_job_scratch"], values)
    values["_job_scratch"] = render(
        task("Size the image within the space it can get")["set_fact"]["_job_scratch"], values
    )
    return values


def holds(name, values):
    return condition(task(name)["assert"]["that"], values)


def runs(name, values):
    return condition(task(name)["when"], values)


class Size(unittest.TestCase):
    def test_a_percentage_of_the_filesystem_in_whole_gib(self):
        wanted = int(reading()["_job_scratch"]["wanted"])
        self.assertEqual(wanted, TB2 * 70 // 100 // GIB * GIB)

    def test_an_absolute_size(self):
        self.assertEqual(int(reading(job_scratch_size="1200G")["_job_scratch"]["wanted"]), 1200 * GIB)
        self.assertEqual(int(reading(job_scratch_size="1T")["_job_scratch"]["wanted"]), 1024 * GIB)

    def test_the_request_is_checked(self):
        for good in ({}, {"job_scratch_size": "85%"}, {"job_scratch_size": "900G"}):
            self.assertTrue(holds("Check the request", reading(**good)), good)
        for bad in (
            {"job_scratch_size": "90%"},  # with the 15% reserve, over 100%
            {"job_scratch_size": "70"},
            {"job_scratch_size": "0%"},
            {"job_scratch_reserve": "15"},
            {"job_scratch_file": "/var/spool/slurmd-private/scratch.img"},
            {"slurm_job_private_dirs": []},
        ):
            self.assertFalse(holds("Check the request", reading(**bad)), bad)


GROWS = "Check the image only grows"
SPACE = "Check the filesystem has room for an image"


def fits(values):
    return holds(GROWS, values) and holds(SPACE, values)


class CreateAndGrow(unittest.TestCase):
    def test_a_new_image_is_created_where_it_fits(self):
        values = reading()
        self.assertTrue(fits(values))
        self.assertTrue(runs("Create the image", values))
        self.assertFalse(runs("Grow the image and its filesystem", values))

    def test_a_fuller_disk_gets_a_smaller_image_never_the_reserve(self):
        # A disk already a quarter used: the image takes what is free beyond the reserve.
        values = reading(avail=TB2 * 3 // 4)
        self.assertTrue(fits(values))
        wanted = int(values["_job_scratch"]["wanted"])
        self.assertLess(wanted, TB2 * 70 // 100)
        self.assertLessEqual(wanted, TB2 * 3 // 4 - TB2 * 15 // 100)
        self.assertGreater(wanted, TB2 * 3 // 4 - TB2 * 15 // 100 - GIB)

    def test_a_disk_with_no_room_beyond_the_reserve_refuses(self):
        values = reading(avail=TB2 * 10 // 100)
        self.assertTrue(holds(GROWS, values))
        self.assertFalse(holds(SPACE, values))

    def test_a_larger_size_grows_it(self):
        values = reading(image=1000 * GIB, avail=TB2 - 1030 * GIB, mounted="/dev/loop3 xfs")
        self.assertTrue(fits(values))
        self.assertFalse(runs("Create the image", values))
        self.assertTrue(runs("Grow the image and its filesystem", values))

    def test_the_same_size_does_nothing(self):
        image = TB2 * 70 // 100 // GIB * GIB
        values = reading(image=image, avail=TB2 - image - 30 * GIB, mounted="/dev/loop3 xfs")
        self.assertTrue(fits(values))
        self.assertFalse(runs("Create the image", values))
        self.assertFalse(runs("Grow the image and its filesystem", values))

    def test_never_shrinks(self):
        values = reading(image=1800 * GIB, mounted="/dev/loop3 xfs")
        self.assertFalse(holds(GROWS, values))
        self.assertTrue(holds(SPACE, values), "shrinking needs no space: only the growth check refuses")

    def test_an_image_larger_than_the_room_is_kept_as_it_is(self):
        # The root filesystem filled up after the image was made: nothing grows, nothing shrinks.
        values = reading(image=1300 * GIB, avail=50 * GIB, mounted="/dev/loop3 xfs")
        self.assertTrue(fits(values))
        self.assertEqual(int(values["_job_scratch"]["wanted"]), 1300 * GIB)
        self.assertFalse(runs("Grow the image and its filesystem", values))


class FirstMount(unittest.TestCase):
    CHECK = "Check no job uses the directories the filesystem will be mounted on"

    def test_an_idle_node_is_mounted(self):
        self.assertTrue(holds(self.CHECK, reading()))

    def test_running_jobs_stop_the_first_mount(self):
        self.assertFalse(holds(self.CHECK, reading(occupied="/var/spool/slurmd-private/4242")))

    def test_once_mounted_jobs_are_expected(self):
        values = reading(
            image=1300 * GIB, mounted="/dev/loop3 xfs", occupied="/var/spool/slurmd-private/4242"
        )
        self.assertTrue(holds(self.CHECK, values))


if __name__ == "__main__":
    unittest.main()
