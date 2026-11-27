"""cluster-bot: the cluster's Slack app (roles/cluster_bot). Slack is its front: slash
commands, forms and buttons through Socket Mode (no inbound port), and requests from the
login node's commands through a spool directory; each feature is one module (accounts,
bans, projects). Decisions are plain functions over plain data, so they are tested
without Slack; the Slack layer only reads input and posts results."""

from cluster_tools.cli import line_output  # the cluster's library (PYTHONPATH)

# Every program of the bot (the service, scopes, notices, tidy) starts by importing this
# package: its lines reach the journal as written, also on a stop (cli.line_output).
line_output()
