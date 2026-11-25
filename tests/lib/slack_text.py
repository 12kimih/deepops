"""What a Slack message must never show. Every text is written in cluster_tools.rich's
Markdown and sent as rich text, so a rich text element holding a marker (**, a backtick,
a link or a mention typed out) means a text went around it. The few places Slack takes
only mrkdwn (context, fields, a section beside a button) must not hold a style marker
that touches a letter: Slack shows the stars there ("*3개*의"), not the style.

    problems(message) -> ["where: what"], empty when the message is clean
    MESSAGE: a list of blocks, a view ({"blocks"}), (text, blocks) or a block
"""

import re

# A marker in shown text: a style, a link or a reference typed out, an entity or an escape.
RAW = re.compile(r"\*\*|`|\]\(|<(?:https?:|mailto:|@|!|#)|&(?:lt|gt|amp);|\\[\\*_`\[\]<>:]")
# mrkdwn: what Slack reads itself and is no style (links, references), then a styled span.
REFERENCE = re.compile(r"<[^>\n]+>|:[a-z0-9_+-]+:")
SPAN = re.compile(r"(?P<m>[*_`])(?=\S)(?P<body>[^\n]*?\S)(?P=m)")
EDGE = re.compile(r"[\s!-/:-@\[-`{-~]")


def _mrkdwn(text, where):
    out = []
    body = REFERENCE.sub(" ", text)
    if "**" in body:
        out.append(f"{where}: ** in mrkdwn: {text!r}")
    for m in SPAN.finditer(body):
        if m["m"] == "_" and not (m.start() == 0 or EDGE.match(body[m.start() - 1])):
            continue  # an identifier's underscore (node_exporter), no italic
        before = body[m.start() - 1] if m.start() else ""
        after = body[m.end()] if m.end() < len(body) else ""
        if (before and not EDGE.match(before)) or (after and not EDGE.match(after)):
            out.append(f"{where}: {m.group(0)!r} touches a letter, Slack shows the marks: {text!r}")
    return out


def problems(message, where="message"):
    if isinstance(message, tuple):
        text, blocks = message[0], message[1]
        out = (
            [f"{where} text: ** in the fallback: {text!r}"] if isinstance(text, str) and "**" in text else []
        )
        return out + problems(blocks, where)
    if (
        isinstance(message, dict)
        and "blocks" in message
        and "type" in message
        and message["type"] in ("home", "modal")
    ):
        return problems(message["blocks"], where)
    out = []

    def walk(node, path, pre):
        if isinstance(node, list):
            for i, n in enumerate(node):
                walk(n, f"{path}[{i}]", pre)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("type")
        if kind == "mrkdwn":
            out.extend(_mrkdwn(node.get("text", ""), path))
            return
        if kind == "text" and isinstance(node.get("text"), str):
            if not pre and not node.get("style", {}).get("code") and RAW.search(node["text"]):
                out.append(f"{path}: a marker in rich text: {node['text']!r}")
            return
        for k, v in node.items():
            if k in ("elements", "blocks", "fields", "text", "accessory"):
                walk(v, f"{path}.{k}", pre or kind == "rich_text_preformatted")

    walk(message, where, False)
    return out
