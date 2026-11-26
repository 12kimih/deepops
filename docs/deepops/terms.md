# Korean glossary

Everything people read from the cluster in Korean (Slack messages, DMs, the bot, the
usage and disk reports, alerts, comments, the admin docs and the user docs) uses one word
for each thing. The list is `scripts/deepops/terms_ko.yml`: each entry names the word to
use, the variants refused (regular expressions over a line, always phrases, so a common
verb in its own sense passes), why, and optionally a scope (path prefixes the entry
applies to, such as the bot's `roles/cluster_bot/`).

- `tests/slack-text/test_terms.py` refuses a variant in any tracked file of this
  repository and of the site config (`DEEPOPS_CONFIG_DIR`, or `config/`), so the push
  check covers both.
- `scripts/deepops/terms.py` prints each offending line (`scripts/deepops/terms.py`, or
  with paths to check just those).
- `scripts/docs/site_facts.py` exports the list (`terms` in the public `site.json`), and
  the user docs' own test applies it to their pages, snippets and Slack canvases.

To change a word, edit its entry and the texts in the same commit; the test lists every
line still using the old one. A word code sends from one constant (such as
`usage_report.IDLE_JOB`) has a test that the constant equals its entry.
