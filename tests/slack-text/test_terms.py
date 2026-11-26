"""The Korean glossary (scripts/deepops/terms_ko.yml): one word for each thing people
read, in every text the repository and the site config keep (Slack messages, DMs, the
bot, reports, comments, docs, tests). The owner asked for it on 2026-11-27, after "GPU를
쓰지 않는 잡" read as a CPU-only job and "기다리는 잡" sat beside "대기 중인 잡". The user
docs apply the same list from site.json (scripts/docs/site_facts.py terms)."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
import terms  # noqa: E402
from cluster_tools import usage_report  # noqa: E402

ENTRIES = terms.load()


class Glossary(unittest.TestCase):
    def test_no_refused_variant_is_left_here_or_in_the_site_config(self):
        self.assertEqual(terms.scan(), [], "use the glossary's word (scripts/deepops/terms_ko.yml)")

    def test_each_word_passes_every_entry_that_applies_where_it_does(self):
        for e in ENTRIES:
            for other in ENTRIES:
                if other["scope"] and other["scope"] != e["scope"]:
                    continue  # a scoped rule refuses a word only on its paths
                for p in other["patterns"]:
                    self.assertIsNone(p.search(e["use"]), f"{e['use']!r} refused by {p.pattern!r}")

    def test_the_variants_are_caught_and_their_words_pass(self):
        caught = {
            "GPU를 쓰지 않는 잡": "할당받은 GPU를 쓰지 않는 잡",
            "6시간 동안 GPU를 쓰지 않은 잡": "할당받은 GPU를 6시간 동안 쓰지 않은 잡",
            "기다리는 잡": "대기 중인 잡",
            "대기 잡의 우선순위": "대기 중인 잡의 우선순위",
            "돌던 잡은 계속 돕니다": "실행 중이던 잡은 계속 돕니다",
            "idle GPU jobs": "jobs leaving their GPUs idle",
            "SSH 공개 키": "SSH 공개키",
            "새 Slurm 잡은 제출되지 않습니다": "새 Slurm 잡을 제출할 수 없습니다",
        }
        for old, new in caught.items():
            self.assertTrue(any(p.search(old) for e in ENTRIES if not e["scope"] for p in e["patterns"]), old)
            self.assertFalse(
                any(p.search(new) for e in ENTRIES if not e["scope"] for p in e["patterns"]), new
            )

    def test_a_scoped_entry_applies_on_its_paths_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "roles/cluster_bot").mkdir(parents=True)
            bot, docs = root / "roles/cluster_bot/x.py", root / "guide.md"
            bot.write_text("새 프로젝트\n")
            docs.write_text("uv 새 프로젝트\n")
            found = terms.violations(ENTRIES, [bot, docs], root)
        self.assertEqual([v.split(":")[0] for v in found], ["roles/cluster_bot/x.py"])

    def test_an_entry_needs_a_word_variants_and_a_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.yml"
            path.write_text("- use: a\n  not: [b]\n")
            with self.assertRaises(ValueError):
                terms.load(path)

    def test_the_reports_name_is_the_glossarys(self):
        self.assertEqual(usage_report.IDLE_JOB, terms.term(ENTRIES, "idle_job"))

    def test_the_user_docs_get_the_same_list(self):
        spec = importlib.util.spec_from_file_location("site_facts", ROOT / "scripts/docs/site_facts.py")
        sf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sf)
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            (config / "group_vars").mkdir(parents=True)
            out = sf.public(sf.facts(config))["terms"]
        self.assertEqual(out, terms.public(ENTRIES))
        self.assertNotIn("patterns", out[0], "plain data: the docs compile it themselves")


if __name__ == "__main__":
    unittest.main()
