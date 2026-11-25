"""rich: every text the cluster says in Slack, written once and shown as written.

Slack's mrkdwn styles `*bold*` only where the star touches a space or punctuation, so
bold before a Korean particle ("*3개*의") shows its stars. Every message is therefore
written in one small Markdown and sent as rich_text blocks, each span with its style
set explicitly; nothing is left for Slack to parse.

    blocks(text)    -> rich_text blocks (split within Slack's limits)
    plain(text)     -> the message's fallback text (notifications), without markers
    mrkdwn(text)    -> for the few places Slack takes only mrkdwn (a context element, a
                       section's fields or a section beside a button): a style is kept
                       only where Slack would show it, never as raw markers
    message(text, blocks=None) -> {"text", "blocks"} for chat.postMessage and the like
    problems(message) -> what a message would show raw (a marker Slack or this module
                       did not read), empty when it shows as written

The Markdown, the same the user docs' notices use (cluster-docs scripts/notices.py):

    **bold**  _italic_  `code`  [text](https://...)  <https://...|text>
    - item (two spaces more per level)   > quote   ``` fenced, shown as typed ```
    <@U123> <!subteam^S123> <!here> <#C123> :emoji:   (Slack's own references)

A value put into a text (a name, a path, a reason Slurm gave) goes through
cluster_tools.slack.escape, so it is shown as it is, never read as Markdown.
Python 3.9 or newer, standard library only.
"""

import re

MAX_CHUNK = 2900  # characters of source per rich_text block (Slack: 3000 per section)

_ESCAPES = {"&lt;": "<", "&gt;": ">", "&amp;": "&"}
INLINE = re.compile(
    r"\\(?P<esc>[\\*_`\[\]<>:])"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|`(?P<code>(?:\\.|[^`\\])+)`"
    r"|\[(?P<ltext>[^\]]+)\]\((?P<lurl>[^)\s]+)\)"
    r"|<(?P<url>(?:https?://|mailto:)[^|>\s]+)(?:\|(?P<utext>[^>]+))?>"
    r"|<@(?P<user>[UW][A-Z0-9]+)(?:\|[^>]*)?>"
    r"|<!subteam\^(?P<group>[A-Z0-9]+)(?:\|[^>]*)?>"
    r"|<!(?P<broadcast>here|channel|everyone)>"
    r"|<#(?P<channel>C[A-Z0-9]+)(?:\|[^>]*)?>"
    r"|(?<![\w:]):(?P<emoji>(?=[a-z0-9_+-]*[a-z])[a-z0-9_+-]+):(?![\w:])"
    r"|(?<!\w)_(?P<italic>(?=\S)[^_\n]+?(?<=\S))_(?!\w)"
)
ITEM = re.compile(r"^( *)- (.*)$")
ENTITY = re.compile(r"&(?:lt|gt|amp);")
# What Slack's mrkdwn would read as a style in a text that is not one: it has no escape.
_READ = re.compile(r"[*`~]|(?:^|[\s!-/:-@\[-`{-~])_")
# Where Slack's mrkdwn shows a style: the marker after a start, space or punctuation, the
# closing one before an end, space or punctuation.
_EDGE = re.compile(r"[\s!-/:-@\[-`{-~]")


def _literal(text):
    return ENTITY.sub(lambda m: _ESCAPES[m.group(0)], text)


def _styled(elements, style):
    return [dict(e, style={**e.get("style", {}), **style}) if e["type"] in ("text", "link") else e
            for e in elements]  # fmt: skip


def inline(text):
    """The rich text elements of one line of the Markdown."""
    out, at = [], 0

    def add(s):
        if not s:
            return
        if out and out[-1]["type"] == "text" and "style" not in out[-1]:
            out[-1] = {"type": "text", "text": out[-1]["text"] + s}
        else:
            out.append({"type": "text", "text": s})

    for m in INLINE.finditer(text):
        add(_literal(text[at : m.start()]))
        at = m.end()
        if m["esc"] is not None:
            add(m["esc"])
        elif m["bold"] is not None:
            out += _styled(inline(m["bold"]), {"bold": True})
        elif m["italic"] is not None:
            out += _styled(inline(m["italic"]), {"italic": True})
        elif m["code"] is not None:
            out.append({"type": "text", "text": _literal(_unescape(m["code"])), "style": {"code": True}})
        elif m["lurl"] is not None:
            out.append({"type": "link", "url": m["lurl"], "text": _literal(m["ltext"])})
        elif m["url"] is not None:
            link = {"type": "link", "url": m["url"]}
            if m["utext"]:
                link["text"] = _literal(m["utext"])
            out.append(link)
        elif m["user"] is not None:
            out.append({"type": "user", "user_id": m["user"]})
        elif m["group"] is not None:
            out.append({"type": "usergroup", "usergroup_id": m["group"]})
        elif m["broadcast"] is not None:
            out.append({"type": "broadcast", "range": m["broadcast"]})
        elif m["channel"] is not None:
            out.append({"type": "channel", "channel_id": m["channel"]})
        else:
            out.append({"type": "emoji", "name": m["emoji"]})
    add(_literal(text[at:]))
    return out


def _block(lines):
    """One rich_text block of the Markdown LINES."""
    elements, para, fence, quote = [], [], None, []

    def flush_para():
        if para:
            parts = []
            for i, line in enumerate(para):
                if i:
                    parts.append({"type": "text", "text": "\n"})
                parts += inline(line)
            elements.append({"type": "rich_text_section", "elements": _merge(parts)})
            para.clear()

    def flush_quote():
        if quote:
            parts = []
            for i, line in enumerate(quote):
                if i:
                    parts.append({"type": "text", "text": "\n"})
                parts += inline(line)
            elements.append({"type": "rich_text_quote", "elements": _merge(parts)})
            quote.clear()

    for line in lines:
        if fence is not None:
            if line.strip() == "```":
                text = "\n".join(fence)
                elements.append({"type": "rich_text_preformatted",
                                 "elements": [{"type": "text", "text": _literal(_unescape(text)) or " "}]})  # fmt: skip
                fence = None
            else:
                fence.append(line)
            continue
        if line.startswith("```"):
            flush_para(), flush_quote()
            fence = [line[3:]] if line[3:].strip() else []
            continue
        item = ITEM.match(line)
        if item:
            flush_para(), flush_quote()
            indent = len(item[1]) // 2
            section = {
                "type": "rich_text_section",
                "elements": inline(item[2]) or [{"type": "text", "text": " "}],
            }
            last = elements[-1] if elements else {}
            if last.get("type") == "rich_text_list" and last["indent"] == indent:
                last["elements"].append(section)
            else:
                elements.append(
                    {"type": "rich_text_list", "style": "bullet", "indent": indent, "elements": [section]}
                )
            continue
        if line.startswith("> ") or line == ">":
            flush_para()
            quote.append(line[2:])
            continue
        flush_quote()
        para.append(line)
    if fence is not None:  # an unclosed fence is shown as typed
        para += ["```", *fence]
    flush_para(), flush_quote()
    while elements and elements[-1]["type"] == "rich_text_section" and _blank(elements[-1]):
        elements.pop()
    return {"type": "rich_text", "elements": elements}


def _unescape(text):
    return re.sub(r"\\([\\*_`\[\]<>:])", r"\1", text)


def _blank(section):
    return all(e["type"] == "text" and not e["text"].strip() for e in section["elements"])


def _merge(parts):
    out = []
    for e in parts:
        if (
            out
            and e["type"] == "text"
            and out[-1]["type"] == "text"
            and e.get("style") == out[-1].get("style")
        ):
            out[-1] = dict(out[-1], text=out[-1]["text"] + e["text"])
        else:
            out.append(e)
    return out or [{"type": "text", "text": " "}]


def _chunks(text):
    """TEXT in pieces of at most MAX_CHUNK characters, split at lines; a fence open at a
    split is closed in one piece and reopened in the next. Nothing is cut."""
    chunks, cur, fenced = [], [], False
    width = MAX_CHUNK - 8
    pieces = [ln[i : i + width] for ln in text.split("\n") for i in range(0, max(len(ln), 1), width)]
    for line in pieces:
        if cur and len("\n".join(cur + [line])) + 4 > MAX_CHUNK:
            chunks.append(cur + (["```"] if fenced else []))
            cur = ["```"] if fenced else []
        cur.append(line)
        if line.startswith("```"):
            fenced = not fenced
    chunks.append(cur)
    return chunks


def blocks(text):
    """TEXT (the Markdown above) as rich_text blocks; an empty text gives none."""
    out = []
    for lines in _chunks(text or ""):
        block = _block(lines)
        if block["elements"]:
            out.append(block)
    return out


def _flat(elements, style_marks):
    """ELEMENTS as text; STYLE_MARKS(e, s) gives an element's text with its style."""
    out = []
    for e in elements:
        t = e["type"]
        if t == "text":
            s = e["text"]
        elif t == "link":
            s = e.get("text") or e["url"]
            if style_marks not in (_plain_marks, _fallback_marks, _read_marks):
                s = f"<{e['url']}|{_entities(s)}>" if e.get("text") else f"<{e['url']}>"
                out.append((s, {}, True))
                continue
            s = f"{s} ({e['url']})" if e.get("text") and e["text"] != e["url"] else s
        elif t == "user":
            s = f"<@{e['user_id']}>"
        elif t == "usergroup":
            s = f"<!subteam^{e['usergroup_id']}>"
        elif t == "broadcast":
            s = f"<!{e['range']}>"
        elif t == "channel":
            s = f"<#{e['channel_id']}>"
        elif t == "emoji":
            s = f":{e['name']}:"
        else:
            s = ""
        out.append((s, e.get("style", {}), t in ("user", "usergroup", "broadcast", "channel", "emoji")))
    return style_marks(out)


def _read_marks(parts):
    """As a test or a log quotes a message: code in backticks, the rest unstyled."""
    return "".join(f"`{s}`" if style.get("code") else s for s, style, _ in parts)


def _plain_marks(parts):
    return "".join(s for s, _, _ in parts)


def _fallback_marks(parts):
    """As plain text, but a text Slack reads safely: its &, < and > as entities, so only
    a real reference (a mention) mentions."""
    return "".join(s if raw else _entities(s) for s, _, raw in parts)


def _entities(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mrkdwn_marks(parts, lost=None):
    """Slack mrkdwn of PARTS: a style's markers only where Slack shows them (the text
    around them a start, an end, a space or punctuation); elsewhere the text unstyled,
    and LOST (a list) told so."""
    texts = [(s if raw else _entities(s), style, raw) for s, style, raw in parts]
    out = []
    for i, (s, style, raw) in enumerate(texts):
        marks = ""
        if style.get("code"):
            marks = "`"
        elif style.get("bold"):
            marks = "*"
        elif style.get("italic"):
            marks = "_"
        core = s.strip()
        lead, trail = s[: len(s) - len(s.lstrip())], s[len(s.rstrip()) :]
        before = lead[-1:] or (out[-1][-1:] if out else "")
        after = trail[:1] or (texts[i + 1][0][:1] if i + 1 < len(texts) else "")
        safe = marks and core and (not before or _EDGE.match(before)) and (not after or _EDGE.match(after))
        out.append(f"{lead}{marks}{core}{marks}{trail}" if safe else s)
        if lost is not None and ((marks and core and not safe) or (not raw and _READ.search(s))):
            lost.append(core)
    return "".join(out)


def _sections(block):
    for e in block["elements"]:
        if e["type"] == "rich_text_list":
            for item in e["elements"]:
                yield "  " * e["indent"] + "- ", item["elements"], False
        elif e["type"] == "rich_text_preformatted":
            yield "", e["elements"], True
        elif e["type"] == "rich_text_quote":
            yield "> ", e["elements"], False
        else:
            yield "", e["elements"], False


def plain(text, marks=_plain_marks):
    """TEXT as plain text, as a person reads it."""
    lines = []
    for block in blocks(text):
        for lead, elements, pre in _sections(block):
            if pre:
                body = elements[0]["text"]
                lines.append(_entities(body) if marks is _fallback_marks else body)
            else:
                lines.append(lead + _flat(elements, marks))
    return "\n".join(lines)


def mrkdwn(text, lost=None):
    """TEXT as Slack mrkdwn, for a context element, a section's fields or a section with
    an accessory; a style Slack would not show there is left out, never shown as stars
    (and named in LOST, a list, when given)."""
    lines = []
    marks = _mrkdwn_marks if lost is None else (lambda parts: _mrkdwn_marks(parts, lost))
    for block in blocks(text):
        for lead, elements, pre in _sections(block):
            if pre:
                lines.append("```\n" + _entities(elements[0]["text"]) + "\n```")
            else:
                lines.append(lead.replace("- ", "• ") + _flat(elements, marks))
    return "\n".join(lines)


def message(text, blocks_=None):
    """The keywords of a message: its fallback TEXT, and BLOCKS or TEXT's own blocks."""
    return {
        "text": plain(text, _fallback_marks) or " ",
        "blocks": blocks_ if blocks_ is not None else blocks(text),
    }


def context(*texts):
    """A context block of TEXTS (small print; mrkdwn, as Slack takes no rich text there).
    One mrkdwn cannot show as written (a command right before a Korean particle, a value
    with a star) is rich text instead: the command stays a command, the star a star."""
    lost = []
    shown = [mrkdwn(t, lost) for t in texts if t]
    if lost:
        return section("\n".join(t for t in texts if t))
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": t} for t in shown]}


def fields(*texts):
    """A section of up to ten TEXTS side by side (mrkdwn: Slack's fields take no rich text)."""
    return {"type": "section", "fields": [{"type": "mrkdwn", "text": mrkdwn(t)} for t in texts]}


def section(text, accessory=None):
    """One block of a short TEXT: beside ACCESSORY (a button) a section, which takes only
    mrkdwn, or plain text when mrkdwn cannot show TEXT as written; without one, the rich
    text itself."""
    if accessory is None:
        return _block((text or " ").split("\n"))
    lost = []
    shown = mrkdwn(text, lost)
    if lost:  # what mrkdwn cannot show as written is shown plain: the text as it is
        return {
            "type": "section",
            "text": {"type": "plain_text", "text": plain(text), "emoji": True},
            "accessory": accessory,
        }
    return {"type": "section", "text": {"type": "mrkdwn", "text": shown}, "accessory": accessory}


def read(blocks_):
    """What BLOCKS_ show, as text, one line per block (their rich text, sections, fields
    and context): code in backticks and code blocks in fences, a link as "text (url)".
    For the tests."""
    out = []
    for b in blocks_ or []:
        if b.get("type") == "rich_text":
            for lead, elements, pre in _sections(b):
                out.append(f"```\n{elements[0]['text']}\n```" if pre else lead + _flat(elements, _read_marks))
        elif b.get("type") in ("section", "header"):
            if b.get("text"):
                out.append(b["text"]["text"])
            out += [f["text"] for f in b.get("fields", [])]
        elif b.get("type") == "context":
            out += [e.get("text", "") for e in b["elements"]]
    return "\n".join(out)


# --- What a message must never show (the tests, and the docs' notices at commit) ---

# A marker in shown text: a style, a link or a reference typed out, an entity or an escape.
# Markdown this grammar does not have, left as typed: *one star*, __two__, a heading.
_RAW = re.compile(
    r"\*\*|`|\]\(|<(?:https?:|mailto:|@|!|#)|&(?:lt|gt|amp);|\\[\\*_`\[\]<>:]"
    r"|(?<!\*)\*(?=[^\s*])[^*\n]+?(?<=[^\s*])\*(?!\*)|(?<!\w)__\S|^#{1,6} ",
    re.M,
)
# mrkdwn: what Slack reads itself and is no style (links, references), then a styled span.
_REFERENCE = re.compile(r"<[^>\n]+>|:[a-z0-9_+-]+:")
_SPAN = re.compile(r"(?P<m>[*_`])(?=\S)(?P<body>[^\n]*?\S)(?P=m)")


def _mrkdwn_problems(text, where):
    out = []
    body = _REFERENCE.sub(" ", text)
    if "**" in body:
        out.append(f"{where}: ** in mrkdwn: {text!r}")
    for m in _SPAN.finditer(body):
        if m["m"] == "_" and not (m.start() == 0 or _EDGE.match(body[m.start() - 1])):
            continue  # an identifier's underscore (node_exporter), no italic
        before = body[m.start() - 1] if m.start() else ""
        after = body[m.end()] if m.end() < len(body) else ""
        if (before and not _EDGE.match(before)) or (after and not _EDGE.match(after)):
            out.append(f"{where}: {m.group(0)!r} touches a letter, Slack shows the marks: {text!r}")
    return out


def problems(message, where="message"):
    """What MESSAGE would show that it should not: ["where: what"], empty when clean. A
    rich text element holding a marker (**, a backtick, a link or a mention typed out,
    Markdown this grammar has not) means a text went around this module or used Markdown
    it does not read; mrkdwn (context, fields, a section beside a button) must not hold
    a style marker that touches a letter: Slack shows the stars there, not the style.
    MESSAGE: a list of blocks, a view ({"type", "blocks"}), (text, blocks) or a block."""
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
            out.extend(_mrkdwn_problems(node.get("text", ""), path))
            return
        if kind == "text" and isinstance(node.get("text"), str):
            if not pre and not node.get("style", {}).get("code") and _RAW.search(node["text"]):
                out.append(f"{path}: a marker in rich text: {node['text']!r}")
            return
        for k, v in node.items():
            if k in ("elements", "blocks", "fields", "text", "accessory"):
                walk(v, f"{path}.{k}", pre or kind == "rich_text_preformatted")

    walk(message, where, False)
    return out
