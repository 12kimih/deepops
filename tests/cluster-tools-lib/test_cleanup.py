"""cluster_tools.cleanup: the folders worth cleaning, one way for every scanner: a folder
is one level below a base, sized by its files, last used by its newest, owned by who
holds most of it, ranked by size times the days past the stale period."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import cleanup, prom  # noqa: E402

DAY = 86400
NOW = 2_000_000_000


class Folders(unittest.TestCase):
    def setUp(self):
        self.f = cleanup.Folders(["/home/alice", "/home/bob", "/scratch/projects/vision"])

    def test_a_file_counts_to_the_folder_one_level_below_its_base(self):
        self.assertEqual(self.f.folder("/home/alice/runs/a/b.pt"), "/home/alice/runs")
        self.assertEqual(self.f.folder("/home/alice/notes.txt"), "/home/alice")
        self.assertEqual(self.f.folder("/scratch/projects/vision/cache/x"), "/scratch/projects/vision/cache")
        self.assertIsNone(self.f.folder("/home/carol/x"))
        self.assertIsNone(self.f.folder("/home/alicex/y"), "a base is matched by whole components")

    def test_large_and_long_unused_first_recent_never(self):
        self.f.add("/home/alice/old/a", "alice", 10 * 1024**3, NOW - 200 * DAY)
        self.f.add("/home/alice/old/b", "alice", 1, NOW - 300 * DAY)  # newest of the folder: 200 days
        self.f.add("/home/bob/small/c", "bob", 1024**3, NOW - 400 * DAY)
        self.f.add("/home/bob/live/d", "bob", 50 * 1024**3, NOW - 5 * DAY)
        ranked = self.f.ranked(NOW, 60, 10)
        self.assertEqual([c["path"] for c in ranked], ["/home/alice/old", "/home/bob/small"])
        self.assertEqual(ranked[0]["unused_days"], 200)
        self.assertEqual(self.f.ranked(NOW, 60, 1)[0]["path"], "/home/alice/old", "only the top N")

    def test_the_owner_is_who_holds_most_of_it(self):
        self.f.add("/scratch/projects/vision/cache/a", "alice", 10, NOW - 100 * DAY)
        self.f.add("/scratch/projects/vision/cache/b", "bob", 30, NOW - 100 * DAY)
        self.assertEqual(self.f.ranked(NOW, 60, 5)[0]["user"], "bob")

    def test_metrics(self):
        self.f.add("/home/alice/old/a", "alice", 5, NOW - 100 * DAY)
        values = prom.parse(prom.render(cleanup.values("/home", self.f.ranked(NOW, 60, 5), NOW)))
        labels = {"filesystem": "/home", "path": "/home/alice/old", "user": "alice"}
        self.assertEqual(values[prom.key("disk_cleanup_candidate_bytes", **labels)], "5")
        self.assertEqual(values[prom.key("disk_cleanup_candidate_unused_days", **labels)], "100")
        self.assertEqual(
            values[prom.key("disk_cleanup_scan_timestamp_seconds", filesystem="/home")], str(NOW)
        )


if __name__ == "__main__":
    unittest.main()
