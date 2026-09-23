"""Markdown → HTML, just enough for what Claude writes.

The strategy and the social prep report come back from Claude as Markdown and
become Google Docs through Drive's HTML import. Drive does not read Markdown, and
a ``.md`` file in Dror's folder is a text file he cannot comfortably edit. This
covers what those documents use — headings, bold/italic, bullet and numbered
lists, links, paragraphs, rules — and escapes everything else, since the text is
model output about client-supplied pages.
"""

from __future__ import annotations

import html
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![\"'>=])(https?://[^\s<]+)")


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    links: list[str] = []

    def keep(m: re.Match[str]) -> str:
        links.append(f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>')
        return f"\x00{len(links) - 1}\x00"

    out = _LINK.sub(keep, out)
    out = _BARE_URL.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], out)


def to_html(markdown: str) -> str:
    """Render ``markdown`` as HTML block elements (no wrapper)."""
    blocks: list[str] = []
    para: list[str] = []
    list_kind: str = ""
    list_start = 1
    items: list[str] = []

    def flush_para() -> None:
        if para:
            blocks.append("<p>" + "<br>".join(_inline(line) for line in para) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if items:
            start = f' start="{list_start}"' if list_kind == "ol" and list_start != 1 else ""
            blocks.append(f"<{list_kind}{start}>" + "".join(f"<li>{i}</li>" for i in items)
                          + f"</{list_kind}>")
            items.clear()
        list_kind = ""

    for raw in markdown.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        bullet = re.match(r"^[-*•]\s+(.*)$", stripped)
        numbered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if not stripped:
            # A blank line ends a paragraph, not a list: Claude spaces out its
            # numbered items, and closing the list there restarted every item at 1.
            flush_para()
        elif heading:
            flush_para(); flush_list()
            level = min(len(heading.group(1)), 4)
            blocks.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush_para(); flush_list()
            blocks.append("<hr>")
        elif bullet or numbered:
            flush_para()
            kind = "ul" if bullet else "ol"
            if list_kind and list_kind != kind:
                flush_list()
            if not list_kind and numbered:
                list_start = int(numbered.group(1))
            list_kind = kind
            items.append(_inline(bullet.group(1) if bullet else numbered.group(2)))
        elif items and raw[:1] in (" ", "\t"):
            items[-1] += "<br>" + _inline(stripped)  # an indented line continues its item
        else:
            flush_list()
            para.append(stripped)
    flush_para(); flush_list()
    return "".join(blocks)


def to_document(title: str, markdown: str) -> str:
    """A full RTL HTML document for Drive's Google Doc import."""
    return (f'<html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>'
            f'<body dir="rtl" lang="he">{to_html(markdown)}</body></html>')
