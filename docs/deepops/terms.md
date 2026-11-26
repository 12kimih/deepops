# Korean glossary

Everything people read from the cluster in Korean (Slack messages, DMs, the bot, the
usage and disk reports, alerts, comments, the admin docs and the user docs) uses one word
for each thing. The list is `scripts/deepops/terms_ko.yml`. Under `terms`, each entry
names:

- `use`: the word in a sentence, and `not`: the variants refused anywhere (regular
  expressions over a line, always phrases, so a common verb in its own sense passes);
- `title`: its form in a title, where that differs (titles are nouns: `중단 노드` in a
  heading, `쓸 수 없는 노드` in a sentence), and `title_not`: phrases refused in titles
  only (`하는 일` is `역할` in a table header);
- `code`: the constants code sends the forms through (`usage_report.IDLE_JOB`,
  `DOWN_TITLE`), so a title may not interpolate a sentence constant, nor a sentence a
  title one;
- `why`, and optionally `scope`: path prefixes the entry applies to (the bot's
  `roles/cluster_bot/`).

Under `titles`, `endings` refuses a sentence ending (-다, -요, -까, -죠) at the end of a
title, and `exempt` lists files whose titles may be sentences, each with why. Today one:
the user docs' troubleshooting page, whose headings are the readers' own questions
(FAQ). Notice titles are not exempt: they are nouns (`사용 규정 변경 안내`).

A title is, by format: a Markdown heading, a bold-only line or a list item's bold lead,
a table header row, a Slack guide's `title`, `table` and `bookmark` (front matter of
`slack/*.md`); in Python a Slack header block, a modal title, a `**bold**` lead in a
string, a form field's label, a button and a `table()` header; a Grafana dashboard's or
panel's `title`; an alert's `summary`. Code blocks hold no titles.

Each repository checks its own files at its own commit, so none waits on another's
checkout:

- deepops: `tests/slack-text/test_terms.py` (every tracked file);
- the site config: its commit hook (`scripts/deepops/config-pre-commit.sh`) runs
  `scripts/deepops/terms.py --site` over the staged tree;
- the user docs: `scripts/docs/site_facts.py` exports the list (`terms` and `titles` in
  the public `site.json`) and their own test applies it.

`scripts/deepops/terms.py` prints each offending line (with paths, just those). To change
a word, edit its entry and the texts in the same commit; the check lists every line
still using the old one.
