"""The Korean glossary (scripts/deepops/terms_ko.yml): one word for each thing people
read, in every text the repositories keep (Slack messages, DMs, the bot, reports,
comments, docs, tests): this repository's here, the site config's in its own commit hook
(terms.py --site), each at its own commit. The owner asked for it on 2026-11-27, after "GPU를
쓰지 않는 잡" read as a CPU-only job and "기다리는 잡" sat beside "대기 중인 잡". The user
docs apply the same list from site.json (scripts/docs/site_facts.py terms)."""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
import terms  # noqa: E402
from cluster_tools import usage_report  # noqa: E402

ENTRIES = terms.load()


class Glossary(unittest.TestCase):
    def test_no_refused_variant_is_left_here(self):
        self.assertEqual(terms.scan(), [], "use the glossary's word (scripts/deepops/terms_ko.yml)")

    def test_the_site_config_is_judged_as_committed_never_by_its_checkout(self):
        """A check that read the shared config clone failed every session until the owner
        synced it (2026-11-27): the site is read at the rev its hook names, from git."""
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            git = ["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
            subprocess.run(["git", "init", "-q", tmp], check=True)
            (site / "a.md").write_text("대기 중인 잡\n")
            subprocess.run([*git, "add", "a.md"], check=True)
            subprocess.run([*git, "commit", "-q", "-m", "a"], check=True)
            (site / "a.md").write_text("기다리는 잡\n")  # in the checkout only
            env = {"DEEPOPS_CONFIG_DIR": tmp, "DEEPOPS_SITE_REV": ""}
            with unittest.mock.patch.dict("os.environ", env):
                self.assertEqual(terms.scan_site(), [], "HEAD passes; the unstaged edit is not read")
                subprocess.run([*git, "add", "a.md"], check=True)
                tree = subprocess.run(
                    [*git, "write-tree"], capture_output=True, text=True, check=True
                ).stdout.strip()
                env["DEEPOPS_SITE_REV"] = tree
                with unittest.mock.patch.dict("os.environ", env):
                    self.assertEqual(len(terms.scan_site()), 1, "the staged tree, as the hook names it")

    def test_each_word_passes_every_entry_that_applies_where_it_does(self):
        for e in ENTRIES:
            for other in ENTRIES:
                if other["scope"] and other["scope"] != e["scope"]:
                    continue  # a scoped rule refuses a word only on its paths
                for word in filter(None, (e.get("use"), e.get("title"))):
                    for p in other["patterns"]:
                        self.assertIsNone(p.search(word), f"{word!r} refused by {p.pattern!r}")
                if e.get("title"):
                    for p in other["title_patterns"]:
                        self.assertIsNone(
                            p.search(e["title"]), f"title {e['title']!r} refused by {p.pattern!r}"
                        )

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

    def titled(self, rel, text):
        return terms.title_violations(ENTRIES, rel, text.split("\n"))

    def test_titles_are_nouns_and_sentences_stay_descriptive(self):
        """Owner, 2026-11-27: GPU 유휴 잡 in a title, 할당받은 GPU를 쓰지 않는 잡 in a sentence."""
        self.assertTrue(self.titled("a.md", "## 할당받은 GPU를 쓰지 않는 잡\n"))
        self.assertFalse(self.titled("a.md", "## GPU 유휴 잡\n\n할당받은 GPU를 쓰지 않는 잡은 취소됩니다.\n"))
        self.assertTrue(self.titled("a.md", "GPU 유휴 잡은 취소됩니다.\n"), "the title form in a sentence")
        self.assertTrue(
            self.titled("a.py", 'x = f"**{IDLE_JOB}**\\n{n}"\n'), "the sentence constant in a title"
        )
        self.assertFalse(
            self.titled("a.py", 'x = f"**{IDLE_JOB_TITLE}**\\n{n}"\nIDLE_JOB_TITLE = "GPU 유휴 잡"\n')
        )
        self.assertTrue(
            self.titled("a.py", 'x = f"평균 {IDLE_JOB_TITLE} {n}개"\n'), "the title constant in a sentence"
        )

    def test_a_title_ends_no_sentence_unless_its_file_is_exempt(self):
        self.assertTrue(self.titled("a.md", "# 사용 규정이 바뀝니다\n"))
        self.assertTrue(self.titled("a.py", 'field("p", "어디에 둘까요", {})\n'))
        self.assertFalse(self.titled("a.md", "# 사용 규정 변경 안내\n"))
        self.assertFalse(
            self.titled("a.md", "## 로그인 노드 사용 규칙 (중요)\n"), "a parenthetical is no ending"
        )
        self.assertFalse(self.titled("a.md", "```bash\n# 확인합니다\n```\n"), "a comment in code is no title")
        exempt = ENTRIES.titles["exempt"][0]["path"]
        self.assertFalse(self.titled(exempt, "### 밖에서 접속이 안 돼요\n"))

    def test_the_title_idioms_have_their_nouns_in_titles_only(self):
        self.assertTrue(self.titled("a.md", "| 명령 | 하는 일 |\n|---|---|\n"))
        self.assertTrue(self.titled("a.md", "## 설정을 바꾸는 법\n"))
        self.assertFalse(
            self.titled("a.md", "| 명령 | 역할 |\n|---|---|\n| a | 잡이 하는 일을 보여 줍니다 |\n")
        )

    def test_an_entry_needs_a_word_variants_and_a_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.yml"
            path.write_text("terms:\n  - use: a\n    not: [b]\n")
            with self.assertRaises(ValueError):
                terms.load(path)

    def test_the_reports_name_is_the_glossarys(self):
        self.assertEqual(usage_report.IDLE_JOB, terms.term(ENTRIES, "idle_job"))
        for e in ENTRIES:
            for form, name in (e.get("code") or {}).items():
                self.assertEqual(getattr(usage_report, name), e[form], f"usage_report.{name}")

    def test_the_user_docs_get_the_same_list(self):
        spec = importlib.util.spec_from_file_location("site_facts", ROOT / "scripts/docs/site_facts.py")
        sf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sf)
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            (config / "group_vars").mkdir(parents=True)
            out = sf.public(sf.facts(config))["terms"]
        self.assertEqual(out, terms.public(ENTRIES))
        self.assertEqual(sf.public(sf.facts(config))["titles"], terms.public_titles(ENTRIES))
        self.assertNotIn("patterns", out[0], "plain data: the docs compile it themselves")


if __name__ == "__main__":
    unittest.main()
