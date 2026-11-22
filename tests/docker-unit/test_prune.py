"""roles/docker's docker-prune.service: every store Docker keeps unused things in has its
step, each at the site's one cleanup age. The build cache is among them: BuildKit's own
collection goes by a size it undercounts on overlay2 (10.6GB counted, 53G held, 2026-11),
so without its step a node kept 53G of cache from one build session."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import load, same_name  # noqa: E402

BLOCK = next(
    t
    for t in load("roles/docker/tasks/present.yml")
    if same_name(t.get("name"), "Prune what Docker keeps unused, on a schedule")
)
UNIT = next(t for t in BLOCK["block"] if same_name(t.get("name"), "Install the prune service"))["copy"][
    "content"
]
STEPS = re.findall(r"^ExecStart=/usr/bin/docker (\w+) prune (.*)$", UNIT, re.M)


class PruneUnit(unittest.TestCase):
    def test_every_store_has_a_step(self):
        self.assertEqual([store for store, _ in STEPS], ["container", "image", "builder", "volume"])

    def test_aged_stores_use_the_one_age(self):
        for store, args in STEPS:
            if store == "volume":  # anonymous volumes no container uses: no age to keep
                continue
            self.assertIn("--filter until={{ docker_prune_after }}", args, store)

    def test_unused_images_and_all_cache_go(self):
        # image prune: every unused image, not only dangling ones; builder prune (buildx):
        # the internal and frontend images' cache too (the classic builder: every
        # unused record, not only dangling ones).
        for store, args in STEPS:
            if store in ("image", "builder"):
                self.assertIn("--all", args.split(), store)


if __name__ == "__main__":
    unittest.main()
